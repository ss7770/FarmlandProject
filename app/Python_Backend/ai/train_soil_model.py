# -*- coding: utf-8 -*-
"""
土壤墒情预测模型训练（P1，开发文档 §5.3）。

- 数据源：sensor.db 的 data 表（Android APP / data_collect.py 经 /api/sensor 持续入库）
- 特征：当前土壤湿度、5/10 分钟前土壤湿度及变化率、温湿光、水位、时刻
- 目标：未来 30 / 60 分钟实测土壤湿度（两个前视窗口各训一个模型）
- 算法：随机森林 vs 梯度提升（时间序列上 LightGBM 的 CPU 替代，零额外依赖），
  按时间切分训练/测试（前 80% 训练、后 20% 测试，杜绝未来信息泄漏）
- 产物：ai/soil_model.pkl（模型+元数据）、ai/soil_model_report.json（评估报告）

用法： python ai/train_soil_model.py
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai.features import FEATURES, build_supervised, prepare_base_frame  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'sensor.db')
MODEL_PATH = os.path.join(BASE_DIR, 'ai', 'soil_model.pkl')
REPORT_PATH = os.path.join(BASE_DIR, 'ai', 'soil_model_report.json')

HORIZONS = [30, 60]
RANDOM_STATE = 42


def load_rows():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, timestamp, temp, humi, light, soil, water FROM data ORDER BY timestamp ASC")
    rows = cur.fetchall()
    conn.close()
    return rows


def make_models():
    return {
        'RandomForest': RandomForestRegressor(
            n_estimators=300, max_depth=None, min_samples_leaf=2,
            n_jobs=-1, random_state=RANDOM_STATE),
        'GBDT': GradientBoostingRegressor(
            n_estimators=300, learning_rate=0.05, max_depth=3,
            subsample=0.9, random_state=RANDOM_STATE),
    }


def evaluate(y_true, y_pred):
    mae = mean_absolute_error(y_true, y_pred)
    rmse = float(np_sqrt(mean_squared_error(y_true, y_pred)))
    r2 = r2_score(y_true, y_pred)
    return mae, rmse, r2


def np_sqrt(x):
    return x ** 0.5


def main():
    rows = load_rows()
    print('[1/4] 读取 sensor.db： %d 行' % len(rows))
    if len(rows) < 200:
        print('警告：样本较少（<200 行），指标仅作管线验证，数据积累后请重训')

    df = prepare_base_frame(rows)
    sup = build_supervised(df)
    print('[2/4] 监督样本： %d 条（原始 %d 行）' % (len(sup), len(df)))
    if len(sup) < 50:
        raise SystemExit('监督样本不足（%d 条），请先运行数据采集积累历史' % len(sup))

    # 时间切分：前 80% 训练，后 20% 测试
    split = int(len(sup) * 0.8)
    train_df, test_df = sup.iloc[:split], sup.iloc[split:]
    x_train, x_test = train_df[FEATURES], test_df[FEATURES]
    print('[3/4] 训练/测试： %d / %d 条' % (len(train_df), len(test_df)))

    models, report = {}, {
        'trained_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'rows_total': int(len(sup)), 'rows_train': int(len(train_df)),
        'rows_test': int(len(test_df)), 'features': FEATURES,
        'horizons': {}, 'data_span': [str(sup['ts'].iloc[0]), str(sup['ts'].iloc[-1])],
    }

    for horizon in HORIZONS:
        target = 'soil_future_%d' % horizon
        y_train, y_test = train_df[target], test_df[target]
        best_name, best_model, best_mae = None, None, None
        per_model = {}
        for name, model in make_models().items():
            model.fit(x_train, y_train)
            pred = model.predict(x_test)
            mae, rmse, r2 = evaluate(y_test.values, pred)
            per_model[name] = {'mae': round(mae, 3), 'rmse': round(rmse, 3), 'r2': round(r2, 4)}
            print('  [%2d min] %-13s MAE=%.3f  RMSE=%.3f  R2=%.4f' % (horizon, name, mae, rmse, r2))
            if best_mae is None or mae < best_mae:
                best_name, best_model, best_mae = name, model, mae
        models[horizon] = best_model
        importance = {f: round(float(v), 4) for f, v in
                      zip(FEATURES, best_model.feature_importances_)}
        importance = dict(sorted(importance.items(), key=lambda kv: -kv[1]))
        report['horizons'][str(horizon)] = {
            'best_model': best_name, 'metrics': per_model, 'importance': importance}

    joblib.dump({'models': models, 'meta': report}, MODEL_PATH)
    with open(REPORT_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print('[4/4] 已保存：%s' % MODEL_PATH)
    print('      评估报告：%s' % REPORT_PATH)
    for h in HORIZONS:
        info = report['horizons'][str(h)]
        print('      +%dmin 最优：%s，特征Top3：%s' % (
            h, info['best_model'],
            list(info['importance'].items())[:3]))


if __name__ == '__main__':
    main()
