# -*- coding: utf-8 -*-
"""
P1 快速验证脚本（开发文档 v2.2）。

用法（在 app/Python_Backend 目录下）：
  python tests/test_p1.py baseline   # 后端接口基线：/api/sensor 写入/校验/latest + AI 建议
  python tests/test_p1.py count      # data 表行数与最新 3 行（验证 APP 上报是否入库）
  python tests/test_p1.py risk       # 注入 soil=5 测试行验证风险决策（测完自动清理）
"""
import json
import os
import sqlite3
import sys

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
DB = os.path.join(BASE, 'sensor.db')
API = 'http://127.0.0.1:5000'
TIMEOUT = 8


def api_count():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COALESCE(MAX(timestamp),'-') FROM data")
    total, last_ts = cur.fetchone()
    cur.execute("SELECT id, timestamp, temp, humi, light, soil, water "
                "FROM data ORDER BY id DESC LIMIT 3")
    rows = cur.fetchall()
    conn.close()
    return total, last_ts, rows


def insert_test_row(soil):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("INSERT INTO data (temp,humi,light,soil,water) VALUES (24,33,350,?,2048)", (soil,))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def delete_rows(ids):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.executemany("DELETE FROM data WHERE id = ?", [(i,) for i in ids])
    conn.commit()
    conn.close()


def show_recommendation():
    r = requests.get(API + '/api/ai/recommendation', timeout=TIMEOUT)
    body = r.json()
    rec = body.get('recommendation', {})
    fc = body.get('forecast', {})
    print('  HTTP %d | forecast.method=%s stale=%s last_ts=%s' % (
        r.status_code, fc.get('method'), fc.get('stale'), fc.get('last_ts', '-')))
    print('  forecast: soil_now=%s soil_30=%s soil_60=%s' % (
        fc.get('soil_now'), fc.get('soil_30'), fc.get('soil_60')))
    print('  risk_level=%s should_irrigate=%s target=%s pump=%ss' % (
        rec.get('risk_level'), rec.get('should_irrigate'),
        rec.get('target_soil'), rec.get('suggested_pump_seconds')))
    for reason in rec.get('reasons', []):
        print('   - ' + reason)
    return body


def cmd_baseline():
    print('== [1] POST /api/sensor 正常写入 ==')
    payload = {'temp': 24, 'humi': 33, 'light': 350, 'soil': 35, 'water': 400, 'device': 'p1-test'}
    r = requests.post(API + '/api/sensor', json=payload, timeout=TIMEOUT)
    print('  HTTP %d %s' % (r.status_code, r.text.strip()))
    assert r.status_code == 200 and r.json().get('status') == 'ok', '写入失败'
    test_id = r.json()['id']

    print('== [2] POST /api/sensor 缺字段（期待 400） ==')
    r = requests.post(API + '/api/sensor', json={'temp': 24}, timeout=TIMEOUT)
    print('  HTTP %d %s' % (r.status_code, r.text.strip()))
    assert r.status_code == 400, '校验未生效'

    print('== [3] GET /api/sensor/latest ==')
    r = requests.get(API + '/api/sensor/latest', timeout=TIMEOUT)
    print('  HTTP %d %s' % (r.status_code, r.text.strip()))
    assert r.status_code == 200

    delete_rows([test_id])
    print('  （基线测试行 %d 已清理）' % test_id)

    print('== [4] GET /api/ai/recommendation ==')
    show_recommendation()
    print('BASELINE PASS')


def cmd_count():
    total, last_ts, rows = api_count()
    print('data 表总行数: %d | 最新时间戳: %s' % (total, last_ts))
    for row in rows:
        print('  %s' % (row,))


def cmd_risk():
    print('== 注入 soil=5 测试行 ==')
    rid = insert_test_row(soil=5)
    try:
        body = show_recommendation()
        rec = body['recommendation']
        ok = (rec.get('risk_level') == '高' and rec.get('should_irrigate') is True
              and 30 <= rec.get('suggested_pump_seconds', 0) <= 120)
        print('  判定: %s' % ('PASS' if ok else 'FAIL'))
    finally:
        delete_rows([rid])
        print('  （测试行 %d 已清理）' % rid)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'baseline'
    {'baseline': cmd_baseline, 'count': cmd_count, 'risk': cmd_risk}[cmd]()
