# -*- coding: utf-8 -*-
"""
土壤墒情推理服务（P1）：供 /api/ai/recommendation 调用。

- 进程内加载 ai/soil_model.pkl（训练产物，train_soil_model.py 生成）
- predict_from_db()：取最近 3 小时 data 表数据现算特征 → 输出未来 30/60 分钟土壤湿度
- 模型缺失或历史不足时，退化为"最近 10 分钟线性外推"，接口永不 500
"""
import os
import sqlite3
import threading
from datetime import datetime

import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'sensor.db')
MODEL_PATH = os.path.join(BASE_DIR, 'ai', 'soil_model.pkl')

_lock = threading.Lock()
_cached = {'bundle': None, 'mtime': 0.0}


def _load_bundle():
    """按文件 mtime 热加载模型（重训后无需重启 Flask）"""
    if not os.path.exists(MODEL_PATH):
        return None
    mtime = os.path.getmtime(MODEL_PATH)
    with _lock:
        if _cached['bundle'] is None or _cached['mtime'] != mtime:
            import joblib
            _cached['bundle'] = joblib.load(MODEL_PATH)
            _cached['mtime'] = mtime
    return _cached['bundle']


def _recent_rows(minutes=180, max_rows=5000):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, timestamp, temp, humi, light, soil, water FROM data "
        "WHERE timestamp >= datetime('now', 'localtime', ?) ORDER BY timestamp ASC LIMIT ?",
        ('-%d minutes' % minutes, max_rows))
    rows = cur.fetchall()
    conn.close()
    if rows:
        return rows, False
    # 近窗口无数据：退回最近 20 条（标注 stale，调用方按陈旧数据处理）
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, timestamp, temp, humi, light, soil, water FROM data "
        "ORDER BY timestamp DESC LIMIT 20")
    rows = list(reversed(cur.fetchall()))
    conn.close()
    return rows, True


def _linear_fallback(rows, horizons=(30, 60)):
    """无模型/特征不足时的兜底：用最近约 10 分钟数据的线性速率外推"""
    if not rows:
        raise ValueError('no recent sensor data')
    ts = [datetime.strptime(r[1], '%Y-%m-%d %H:%M:%S') for r in rows]
    soil = [float(r[5]) for r in rows]
    now = ts[-1]
    past_idx = None
    for i in range(len(ts) - 1, -1, -1):
        if (now - ts[i]).total_seconds() >= 570:  # ~10 分钟前
            past_idx = i
            break
    if past_idx is None:
        past_idx = 0
    span_min = max((now - ts[past_idx]).total_seconds() / 60.0, 1.0)
    rate = (soil[-1] - soil[past_idx]) / span_min  # %/min，可能是负数（变干）
    out = {'method': 'linear_fallback', 'rate_per_min': round(rate, 4)}
    for h in horizons:
        out['soil_%d' % h] = round(float(np.clip(soil[-1] + rate * h, 0, 100)), 2)
    out['soil_now'] = round(soil[-1], 2)
    return out


def predict_from_db():
    """返回 {'method', 'stale', 'last_ts', 'soil_now', 'soil_30', 'soil_60',
    'rate_per_min'?, 'model'?, 'trained_at'?}，数据全无时抛 ValueError"""
    rows, stale = _recent_rows()
    bundle = None
    try:
        bundle = _load_bundle()
    except Exception:
        bundle = None

    if bundle is not None and rows:
        try:
            from ai.features import FEATURES, build_inference_row
            row = build_inference_row(rows)
            x = [[row[f] for f in FEATURES]]
            out = {'method': 'model',
                   'stale': stale,
                   'last_ts': rows[-1][1],
                   'soil_now': round(row['soil_now'], 2),
                   'rate_per_min': round(row['rate5'], 4),
                   'model': bundle['meta'].get('horizons', {}).get('30', {}).get('best_model', 'unknown'),
                   'trained_at': bundle['meta'].get('trained_at', '')}
            for h, model in bundle['models'].items():
                pred = float(np.clip(model.predict(x)[0], 0, 100))
                out['soil_%d' % int(h)] = round(pred, 2)
            return out
        except Exception as e:
            # 特征不足（如刚启动数据太稀）→ 线性兜底
            fb = _linear_fallback(rows)
            fb['stale'] = stale
            fb['last_ts'] = rows[-1][1] if rows else ''
            fb['fallback_reason'] = str(e)
            return fb
    fb = _linear_fallback(rows)
    fb['stale'] = stale
    fb['last_ts'] = rows[-1][1] if rows else ''
    return fb
