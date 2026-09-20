# -*- coding: utf-8 -*-
"""
P3 快速验证脚本：D200 网络摄像头接入（实时预览 / 抓拍识别 / 自动巡检）。

用法（在 app/Python_Backend 目录下）：
  python tests/test_p3.py

⚠️ 注意：D200 固件并发≈1。若 Flask 服务正在运行（它占着那条常驻连接），
本脚本进程会抢不到服务位，B 段大概率 SKIP（属正常现象，不算失败）；
跑完整验证前先停掉 Flask，跑完再启动。抢位也可能导致 Flask 的流断开一次，
其后台线程会在 3 秒后自动重连，无需处理。

分两段：
  A 段 离线也能过：接口存在性、无硬件时优雅降级、看板含摄像头面板、
        推理内核与阈值确为共用一份（capture 与 camera 引用的同一个函数对象）
  B 段 需要 D200 在线：/api/camera/status 在线判定、snapshot 出图、
        /api/camera/capture 真实抓帧 + TFLite 识别 + 三档判定
        （测试期间若真的确诊入库，会把这条测试记录与图片一起删掉）

摄像头不在线时 B 段自动 SKIP，不算失败。
"""
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app import app  # noqa: E402
import blueprints.capture as cap  # noqa: E402
import blueprints.camera as cam_mod  # noqa: E402
from utils import inference as inf  # noqa: E402

client = app.test_client()
fails = []


def check(cond, label, extra=''):
    print('  %-46s %s %s' % (label, 'OK' if cond else 'FAIL', extra))
    if not cond:
        fails.append(label)


print('== A 段：接口与共用内核（不依赖硬件） ==')

r = client.get('/api/camera/status')
body = r.get_json() or {}
cam_info = body.get('camera', {})
check(r.status_code == 200 and body.get('status') == 'ok', 'GET /api/camera/status 200',
      'host=%s online=%s' % (cam_info.get('host'), cam_info.get('online')))
check('inspect' in cam_info and 'state' in cam_info, 'status 返回巡检配置与运行状态')

r = client.get('/')
html = r.get_data(as_text=True)
for kw in ('AI 摄像头巡检', 'camera-stream', 'btn-capture'):
    check(kw in html, '看板包含 %s' % kw)

# 阈值/判定必须只有一份实现（P2 定的三档，D200 不能自己另写一套）
check(cap.try_inference is inf.try_inference, 'capture 与 inference 共用 try_inference')
check(cam_mod.try_inference is inf.try_inference, 'camera 与 inference 共用 try_inference')
check(cap.classify_verdict is inf.classify_verdict, 'capture 与 inference 共用 classify_verdict')
check(abs(inf.CONFIRMED_THRESHOLD - 75.0) < 1e-9 and abs(inf.SUSPECTED_THRESHOLD - 45.0) < 1e-9,
      '三档阈值仍为 75 / 45', 'confirmed>=%.0f suspected>=%.0f'
      % (inf.CONFIRMED_THRESHOLD, inf.SUSPECTED_THRESHOLD))
for conf, want in ((90.0, 'confirmed'), (75.0, 'confirmed'), (60.0, 'suspected'),
                   (45.0, 'suspected'), (30.0, 'rejected')):
    check(inf.classify_verdict(conf) == want, 'classify_verdict(%.0f) == %s' % (conf, want))

r = client.get('/api/camera/latest')
check(r.status_code == 200, 'GET /api/camera/latest 200', (r.get_json() or {}).get('status'))

print('== B 段：真实摄像头链路 ==')
st = (client.get('/api/camera/status').get_json() or {}).get('camera', {})
if not st.get('online'):
    print('  SKIP：D200 当前不在线（host=%s，last_error=%s）'
          % (st.get('host'), st.get('last_error') or '暂无'))
    print('  提示：确认板子已 AT+WJAP 连入热点，且 config.D200_HOST 与 AT+WJAP? 的 IP 一致')
else:
    r = client.get('/api/camera/snapshot')
    check(r.status_code == 200 and r.headers.get('Content-Type') == 'image/jpeg',
          'GET /api/camera/snapshot 返回 JPEG', '%d 字节' % len(r.get_data()))
    check(r.get_data()[:2] == b'\xff\xd8', 'snapshot 是合法 JPEG 头（SOI）')

    db = os.path.join(BASE, 'sensor.db')
    conn = sqlite3.connect(db)
    before = conn.execute('SELECT COUNT(*) FROM disease_records').fetchone()[0]
    conn.close()

    r = client.post('/api/camera/capture', json={'trigger': 'test'})
    res = r.get_json() or {}
    print('  capture -> HTTP %d %s' % (r.status_code,
          {k: res.get(k) for k in ('status', 'verdict', 'disease', 'confidence', 'recorded')}))
    check(r.status_code == 200 and res.get('status') == 'ok', 'POST /api/camera/capture 成功抓帧')
    check(res.get('verdict') in ('confirmed', 'suspected', 'rejected'), '返回三档判定',
          str(res.get('verdict')))
    check(res.get('image', '').startswith('d200_'), '图片按 d200_ 前缀落盘', res.get('image', ''))

    conn = sqlite3.connect(db)
    after = conn.execute('SELECT COUNT(*) FROM disease_records').fetchone()[0]
    conn.close()
    if before == after:
        print('  （本次未确诊，未新增记录——符合"确诊才入库"设计）')
    else:
        check(res.get('recorded') is True, '新增记录对应 recorded=True')
        # 清理测试产生的记录与图片，避免污染演示数据
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM disease_records WHERE image_path = ?", ('uploads/' + res['image'],))
        conn.commit()
        conn.close()
        p = os.path.join(BASE, 'uploads', res['image'])
        if os.path.exists(p):
            os.remove(p)
        print('  （测试记录与图片已清理：%s）' % res['image'])

    r = client.get('/api/camera/latest')
    check((r.get_json() or {}).get('status') == 'ok', 'GET /api/camera/latest 有巡检结果')

    cfg = (client.get('/api/camera/inspect').get_json() or {}).get('inspect', {})
    check('enabled' in cfg and 'interval' in cfg, '巡检配置可读',
          'enabled=%s interval=%s' % (cfg.get('enabled'), cfg.get('interval')))

print()
if fails:
    print('P3 FAIL: %d 项未通过 -> %s' % (len(fails), fails))
    sys.exit(1)
print('P3 PASS')
