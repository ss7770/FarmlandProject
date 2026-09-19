# -*- coding: utf-8 -*-
"""
P2 快速验证脚本（开发文档 v2.2 §4.4 拒识 + AI 决策看板）。

用法（在 app/Python_Backend 目录下）：
  python tests/test_p2.py
用 Flask test_client 运行，不依赖正在运行的服务，测试产物自动清理：
  [1] classify_verdict 三档边界单测
  [2] dashboard 首页包含"AI 灌溉决策引擎"面板
  [3] 噪声图上传 /api/upload_image → 拒识/疑似档且不入库（真实模型推理）
"""
import io
import json
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import app  # noqa: E402
from blueprints.capture import classify_verdict, CONFIRMED_THRESHOLD, SUSPECTED_THRESHOLD  # noqa: E402

client = app.test_client()

print('== [1] classify_verdict 三档边界 ==')
cases = [(100.0, 'confirmed'), (75.0, 'confirmed'), (74.9, 'suspected'),
         (45.0, 'suspected'), (44.9, 'rejected'), (0.0, 'rejected')]
for conf, want in cases:
    got = classify_verdict(conf)
    print('  %5.1f%% -> %-9s (期望 %s) %s' % (conf, got, want, 'OK' if got == want else 'FAIL'))
    assert got == want
print('  阈值: confirmed>=%.0f, suspected>=%.0f' % (CONFIRMED_THRESHOLD, SUSPECTED_THRESHOLD))

print('== [2] dashboard 首页含 AI 决策面板 ==')
r = client.get('/')
html = r.get_data(as_text=True)
for keyword in ('AI 灌溉决策引擎', 'ai-reasons', '病害巡检记录'):
    ok = keyword in html
    print('  %-14s %s' % (keyword, 'OK' if ok else 'MISSING'))
    assert ok, 'dashboard 缺少 ' + keyword
assert 'AI 灌溉决策引擎' in html and 'ai-reasons' in html

print('== [3] 噪声图上传 → 拒识/疑似且不入库 ==')
try:
    from PIL import Image
    import numpy as np
except ImportError:
    print('  SKIP（未装 pillow/numpy，无法合成测试图）')
    sys.exit(0)

rng = np.random.default_rng(42)
noise = rng.integers(0, 256, size=(300, 300, 3), dtype=np.uint8)
buf = io.BytesIO()
Image.fromarray(noise).save(buf, format='JPEG')
buf.seek(0)

conn = sqlite3.connect(os.path.join(BASE, 'sensor.db'))
before = conn.execute('SELECT COUNT(*) FROM disease_records').fetchone()[0]
conn.close()

r = client.post('/api/upload_image', data={
    'image': (buf, 'noise_test.jpg')}, content_type='multipart/form-data')
body = r.get_json()
print('  HTTP %d %s' % (r.status_code, json.dumps(body, ensure_ascii=False)[:220]))
assert r.status_code == 200 and body.get('status') == 'ok'
assert body.get('verdict') in ('suspected', 'rejected'), '噪声图不应被判为 confirmed: %s' % body

conn = sqlite3.connect(os.path.join(BASE, 'sensor.db'))
after = conn.execute('SELECT COUNT(*) FROM disease_records').fetchone()[0]
conn.close()
print('  disease_records: %d -> %d（未入库 %s）' % (before, after, 'OK' if before == after else 'FAIL'))
assert before == after, '疑似/拒识结果不应写入 disease_records'

saved = body.get('saved')
if saved:
    p = os.path.join(BASE, 'uploads', saved)
    if os.path.exists(p):
        os.remove(p)
        print('  （测试图片 %s 已清理）' % saved)

print('P2 PASS')
