# -*- coding: utf-8 -*-
"""
土壤墒情特征工程（P1，训练与推理共用，保证口径一致）。

数据口径：
- 原始 data 表采样间隔不均匀（真实采集约 7 秒/条，虚拟回填 10 分钟/条）
- 统一重采样到 1 分钟网格：同分钟取中位数，缺失分钟前向填充（最多 10 分钟），
  之后所有滞后/前视特征都按固定网格 shift 计算，对任意采样间隔稳健
- 特征只依赖"当前时刻及之前"的数据 → 推理时用最近历史现算
- 目标值（训练专用）：soil_future_30 / soil_future_60，即 t+30min、t+60min 的实测土壤湿度
"""
import numpy as np
import pandas as pd

# 参与训练/推理的特征列（顺序即模型输入顺序）
FEATURES = ['soil_now', 'soil_5m', 'soil_10m', 'rate5', 'rate10',
            'temp', 'humi', 'light', 'water', 'hour_f']

_GRID = '1min'      # 重采样网格
_FFILL = 10         # 网格缺失最多向前填充 10 分钟（超过视为断档）
_HORIZONS = [30, 60]


def prepare_base_frame(rows):
    """原始 data 表行 → 按 ts 升序、去重、数值化的基础帧"""
    df = pd.DataFrame(rows, columns=['id', 'timestamp', 'temp', 'humi', 'light', 'soil', 'water'])
    df['ts'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df.dropna(subset=['ts'])
    for c in ['temp', 'humi', 'light', 'soil', 'water']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['temp', 'humi', 'light', 'soil', 'water'])
    df = df.drop_duplicates(subset='ts', keep='last').sort_values('ts').reset_index(drop=True)
    return df


def to_grid(df):
    """1 分钟网格重采样 + 有限前向填充，返回以 ts 为索引、含原始列的网格帧"""
    cols = ['temp', 'humi', 'light', 'soil', 'water']
    s = df.set_index('ts')[cols].resample(_GRID).median()
    s = s.ffill(limit=_FFILL)
    grid = s.dropna(how='any').copy()
    grid.index.name = 'ts'
    return grid


def build_feature_frame(df):
    """基础帧 → 带特征列的网格帧（含 FEATURES，保留目标列位）"""
    grid = to_grid(df)
    grid['soil_now'] = grid['soil']
    grid['soil_5m'] = grid['soil'].shift(5)
    grid['soil_10m'] = grid['soil'].shift(10)
    grid['rate5'] = (grid['soil'] - grid['soil'].shift(5)) / 5.0
    grid['rate10'] = (grid['soil'] - grid['soil'].shift(10)) / 10.0
    grid['hour_f'] = grid.index.hour + grid.index.minute / 60.0
    return grid


def build_supervised(df):
    """训练集构造：特征 + 未来 30/60 分钟实测土壤湿度目标，去掉缺行"""
    grid = build_feature_frame(df)
    for horizon in _HORIZONS:
        grid['soil_future_%d' % horizon] = grid['soil'].shift(-horizon)

    out = grid.reset_index()
    cols = FEATURES + ['soil_future_30', 'soil_future_60', 'ts']
    out = out[cols].dropna().reset_index(drop=True)
    return out


def build_inference_row(rows):
    """推理：用最近历史（建议 >=15 分钟）算出"最新时刻"一行特征向量。

    返回 dict（FEATURES -> 值）；历史不足/断档时抛 ValueError。
    """
    if not rows:
        raise ValueError('no history rows')
    df = prepare_base_frame(rows)
    if len(df) < 2:
        raise ValueError('not enough history rows')
    span_min = (df['ts'].iloc[-1] - df['ts'].iloc[0]).total_seconds() / 60.0
    if span_min < 12:
        raise ValueError('history span too short: %.1f min' % span_min)

    grid = build_feature_frame(df)
    feat_rows = grid[FEATURES].dropna()
    if feat_rows.empty:
        raise ValueError('lag features incomplete (history too sparse)')
    last = feat_rows.iloc[-1]
    row = {f: float(last[f]) for f in FEATURES}
    if any(np.isnan(v) for v in row.values()):
        raise ValueError('feature contains NaN')
    return row
