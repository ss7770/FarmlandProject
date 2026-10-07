# -*- coding: utf-8 -*-
"""
P2 快速验证脚本（开发文档 v2.2 §4.4 拒识 + AI 决策看板 + 2026-10-01 记录口径调整）。

用法（在 app/Python_Backend 目录下）：
  python tests/test_p2.py
用 Flask test_client 运行，不依赖正在运行的服务，测试产物自动清理：
  [1] classify_verdict 三档边界单测
  [2] dashboard 首页只剩 图表/天气/AI 决策（病害识别入口已移除）；同级看板 /disease 可渲染且含图片/置信度占位
  [3] 噪声图上传 /api/upload_image → 仍按实识别，但**一律入库**（拍到就入库口径）
  [4] 「立即抓拍」指令通道**必须已删除**（v15 反向断言：路由 404 + 前端无 capture 代码）
  [5] 未确诊上报 → 也入库，且展示口径为「置信率不足」（不给病名）
  [6] 未确诊记录过期清理（用临时库，不动真实数据）
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
DB_PATH = os.path.join(BASE, 'sensor.db')

print('== [1] classify_verdict 三档边界 ==')
cases = [(100.0, 'confirmed'), (75.0, 'confirmed'), (74.9, 'suspected'),
         (45.0, 'suspected'), (44.9, 'rejected'), (0.0, 'rejected')]
for conf, want in cases:
    got = classify_verdict(conf)
    print('  %5.1f%% -> %-9s (期望 %s) %s' % (conf, got, want, 'OK' if got == want else 'FAIL'))
    assert got == want
print('  阈值: confirmed>=%.0f, suspected>=%.0f' % (CONFIRMED_THRESHOLD, SUSPECTED_THRESHOLD))

print('== [2] dashboard 首页（图表/天气/AI 决策）+ 同级病害识别看板 /disease ==')
r = client.get('/')
html = r.get_data(as_text=True)
# 2026-10-01 二次调整：病害识别入口卡已从 dashboard 彻底移除（不含任何病害识别元素、不挂跳转），
# 图片/置信度占位等全部实现在同级页 /disease 上。
for keyword in ('AI 灌溉决策引擎', 'ai-reasons'):
    ok = keyword in html
    print('  %-14s %s' % (keyword, 'OK' if ok else 'MISSING'))
    assert ok, 'dashboard 缺少 ' + keyword
for gone in ('db-entry', '病害识别'):
    ok = gone not in html
    print('  %-14s %s' % (gone + ' 已移除', 'OK' if ok else 'STILL THERE'))
    assert ok, 'dashboard 仍残留 ' + gone

r2 = client.get('/disease')
board = r2.get_data(as_text=True)
print('  独立看板 /disease -> HTTP %d' % r2.status_code)
for keyword in ('db-latest', 'db-records', 'db-device-text'):
    ok = keyword in board
    print('  %-14s %s' % (keyword, 'OK' if ok else 'MISSING'))
    assert ok, '/disease 缺少 ' + keyword
board_js = client.get('/static/js/disease_board.js').get_data(as_text=True)
print('  图片/置信度占位 -> %s' % ('OK' if ('db-photo-empty' in board_js and '\'--%\'' in board_js) else 'MISSING'))
assert 'db-photo-empty' in board_js, '/disease 缺少图片占位'
assert '\'--%\'' in board_js, '/disease 缺少置信度占位'

print('== [3] 噪声图上传 → 一律入库（拍到就入库）==')
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

conn = sqlite3.connect(DB_PATH)
before = conn.execute('SELECT COUNT(*) FROM disease_records').fetchone()[0]
conn.close()

r = client.post('/api/upload_image', data={
    'image': (buf, 'noise_test.jpg')}, content_type='multipart/form-data')
body = r.get_json()
print('  HTTP %d %s' % (r.status_code, json.dumps(body, ensure_ascii=False)[:240]))
assert r.status_code == 200 and body.get('status') == 'ok'
assert body.get('verdict') in ('suspected', 'rejected'), '噪声图不应被判为 confirmed: %s' % body

conn = sqlite3.connect(DB_PATH)
after = conn.execute('SELECT COUNT(*) FROM disease_records').fetchone()[0]
conn.close()
print('  disease_records: %d -> %d（拍到就入库 %s）'
      % (before, after, 'OK' if after == before + 1 else 'FAIL'))
assert after == before + 1, '新口径下任何一次上传都应写入记录'
assert body.get('diagnosed') is False, '未确诊的 diagnosed 必须为 false'
assert body.get('display_name') == '置信率不足', '未确诊不得展示病名'

# 清理这条测试记录与图片
saved = body.get('saved')
conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM disease_records WHERE image_path = ?", ('uploads/' + saved,))
conn.commit()
conn.close()
if saved:
    p = os.path.join(BASE, 'uploads', saved)
    if os.path.exists(p):
        os.remove(p)
        print('  （测试图片 %s 已清理）' % saved)

print('== [4] 「立即抓拍」指令通道**必须已删除**（v15 反向断言）==')
# 用户 2026-10-02 拍板：整条链杀死（板端 poll_command / 服务端 /api/edge/command / 前端按钮）。
# 谁把它加回来，这里立刻红。
r = client.post('/api/edge/command', json={'cmd': 'capture'})
print('  POST /api/edge/command -> HTTP %d（期望 404）' % r.status_code)
assert r.status_code == 404, '指令通道不该还存在'
r = client.get('/api/edge/command')
print('  GET  /api/edge/command -> HTTP %d（期望 404）' % r.status_code)
assert r.status_code == 404, '指令通道不该还存在'

import config  # noqa: E402
assert not hasattr(config, 'EDGE_CMD_TTL_SEC'), 'config 里不该再有 EDGE_CMD_TTL_SEC'
print('  config.EDGE_CMD_TTL_SEC  已移除 OK')

for gone in ("id=\"db-btn-capture\"", "getElementById('db-btn-capture')",
             "fetch('/api/edge/command'"):
    ok = gone not in board and gone not in board_js
    print('  %-32s %s' % (gone + ' 不存在', 'OK' if ok else 'STILL THERE'))
    assert ok, '前端还残留 ' + gone

# 服务端也不该再引用被删掉的指令状态
edge_src = open(os.path.join(BASE, 'blueprints', 'edge.py'), encoding='utf-8').read()
for gone in ('_cmd_lock', '_cmd_json', 'EDGE_H_CMD'):
    ok = gone not in edge_src
    print('  %-32s %s' % (gone + ' 不在 edge.py', 'OK' if ok else 'STILL THERE'))
    assert ok, 'edge.py 还残留 ' + gone

print('== [5] 未确诊上报 → 也入库，且不给病名 ==')
r = client.post('/api/edge/disease', json={
    'disease': 'Tomato___Late_blight', 'confidence': 51.2,
    'source': 'p2test', 'timestamp': ''})
d = r.get_json()
print('  上报 -> HTTP %d %s' % (r.status_code, json.dumps(d, ensure_ascii=False)[:220]))
assert r.status_code == 200, '上报被拒：%s' % d
assert d.get('accepted') is True, '拍到就入库：未确诊也应 accepted'
assert d.get('verdict') == 'suspected' and d.get('record_id'), d

r = client.get('/api/disease/overview?limit=1')
latest = r.get_json().get('latest') or {}
print('  overview.latest -> %s' % json.dumps(latest, ensure_ascii=False)[:260])
assert latest.get('diagnosed') is False
assert latest.get('display_name') == '置信率不足', '未确诊不得展示病名'
assert 'Tomato' not in latest.get('display_name', '')

conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM disease_records WHERE source = 'p2test'")
conn.commit()
conn.close()
print('  （测试记录已清理）')

print('== [6] 未确诊记录过期清理（临时库）==')
import tempfile  # noqa: E402

import config  # noqa: E402
from utils import db as dbmod  # noqa: E402

orig_db = config.DATABASE_PATH
fd, tmp_db = tempfile.mkstemp(suffix='.db')
os.close(fd)
try:
    config.DATABASE_PATH = tmp_db
    c = dbmod.get_db_connection()
    c.execute("""CREATE TABLE disease_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, disease TEXT, confidence REAL,
        location TEXT, timestamp TEXT, image_path TEXT, source TEXT)""")
    c.execute("INSERT INTO disease_records (disease,confidence,timestamp,image_path,source)"
              " VALUES ('Old_Unconfirmed',30.0,'2020-01-01 00:00:00','','t')")
    c.execute("INSERT INTO disease_records (disease,confidence,timestamp,image_path,source)"
              " VALUES ('Old_Confirmed',90.0,'2020-01-01 00:00:00','','t')")
    c.commit()
    c.close()

    deleted, files = dbmod.purge_unconfirmed(30)
    c = dbmod.get_db_connection()
    left = [row['disease'] for row in c.execute('SELECT disease FROM disease_records')]
    c.close()
    print('  删除 %d 条，剩余 %s' % (deleted, left))
    assert deleted == 1 and left == ['Old_Confirmed'], '只该删"未确诊且过期"的'
finally:
    config.DATABASE_PATH = orig_db
    try:
        os.remove(tmp_db)
    except OSError:
        pass

print('P2 PASS')
