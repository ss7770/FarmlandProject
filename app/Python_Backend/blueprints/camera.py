"""D200 网络摄像头接入（实时预览 / 抓拍识别 / 自动巡检）

设计要点（为什么这么做，改代码前先看这段）：
1. **只连一条**：D200 出厂固件 HTTP 服务并发≈1，多连接会互相挤掉导致画面卡死。
   所有取帧都走 utils.d200_cam.get_cam() 这一条常驻连接。
2. **复用同一条识别链路**：抓到的帧交给 utils.inference（与 ESP32-CAM / APP 手动上传
   完全相同的三档拒识判定与 TFLite 模型），不另起一套，避免阈值不一致。
3. **确诊才入库**：只有 confirmed（>=75%）才写 disease_records，
   APP 的 /api/disease/latest 30 秒轮询会自动弹通知 + TTS 播报，APP 侧零改动。
4. **图片滚动清理**：自动巡检每 5 分钟存一张，只保留最新 D200_KEEP_FRAMES 张，
   防止 uploads 无限膨胀。

接口：
    GET  /api/camera/status           连接状态 + 巡检状态
    GET  /api/camera/snapshot         最新一帧 JPEG（看板/APP 显示）
    GET  /api/camera/stream           MJPEG 实时推流（浏览器 <img> 可直接用）
    GET  /api/camera/latest           最近一次巡检结果（JSON）
    POST /api/camera/capture          立即抓拍 + 识别（返回三档判定）
    GET  /api/camera/inspect          查询自动巡检开关
    POST /api/camera/inspect          运行时开关/改间隔 {"enabled":true,"interval":300}
"""
import glob
import os
import threading
import time
from datetime import datetime

from flask import Blueprint, Response, jsonify, request

import config
from utils.db import insert_disease_record
from utils.d200_cam import get_cam
from utils.inference import describe_result, try_inference

camera_bp = Blueprint('camera', __name__, url_prefix='/api/camera')

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')

# 超过这个年龄的缓存帧视为"已经过期"，不再当作实时画面使用（秒）
FRAME_MAX_AGE = int(getattr(config, 'D200_FRAME_MAX_AGE', 15))

# ---- 运行状态（进程内） ----
_lock = threading.Lock()
_inspect_cfg = {
    'enabled': bool(getattr(config, 'D200_ENABLED', True)),
    'interval': int(getattr(config, 'D200_INSPECT_INTERVAL', 300)),
}
_state = {
    'inspection_count': 0,
    'last_ts': '',
    'last_result': None,     # 最近一次巡检的完整结果（含三档判定）
    'last_image': '',        # 最近一次巡检保存的文件名
    'started_at': '',
    'thread_alive': False,
}
_thread = None


# ================= 巡检 =================
def run_inspection(trigger='auto'):
    """抓一帧 → 保存 → TFLite 识别 → 三档判定 → 确诊则入库。返回结果 dict。"""
    cam = get_cam()
    jpg = cam.latest(timeout=8, max_age=FRAME_MAX_AGE)
    if not jpg:
        st = cam.status()
        return {'status': 'error', 'msg': 'camera offline',
                'detail': st.get('last_error') or 'no frame received',
                'host': st.get('host'), 'connected': st.get('connected'),
                'trigger': trigger, 'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = datetime.now().strftime('d200_%Y%m%d_%H%M%S.jpg')
    path = os.path.join(UPLOAD_DIR, name)
    with open(path, 'wb') as f:
        f.write(jpg)

    info = try_inference(jpg)          # 直接喂字节，不再读一遍文件
    out = {'status': 'ok', 'trigger': trigger, 'image': name,
           'url': '/api/uploads/' + name, 'image_bytes': len(jpg),
           'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    if info:
        out.update(describe_result(info))
        out['recorded'] = False
        if out['verdict'] == 'confirmed':
            out['record_id'] = insert_disease_record(
                info['disease'], info['confidence'],
                location='D200-CAM', image_path='uploads/' + name, source='d200')
            out['recorded'] = True
    else:
        out['msg'] = 'inference unavailable (see server console)'

    _prune_frames()
    with _lock:
        _state['inspection_count'] += 1
        _state['last_ts'] = out['timestamp']
        _state['last_result'] = out
        _state['last_image'] = name
    return out


def _prune_frames():
    """只保留最新 N 张 d200_*.jpg"""
    keep = int(getattr(config, 'D200_KEEP_FRAMES', 50))
    files = sorted(glob.glob(os.path.join(UPLOAD_DIR, 'd200_*.jpg')))
    for old in files[:-keep] if len(files) > keep else []:
        try:
            os.remove(old)
        except OSError:
            pass


def _inspect_loop():
    with _lock:
        _state['started_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _state['thread_alive'] = True
    first = True
    try:
        while True:
            # 首轮只等 30 秒（尽快给出第一次巡检结果），之后按配置间隔
            target = 30 if first else max(30, int(_inspect_cfg['interval']))
            first = False
            for _ in range(target):
                time.sleep(1)
                if not _inspect_cfg['enabled']:
                    break
            if not _inspect_cfg['enabled']:
                continue
            try:
                res = run_inspection(trigger='auto')
                if res.get('status') == 'ok':
                    print('[D200] 巡检 #%d -> %s %s%% (%s)'
                          % (_state['inspection_count'], res.get('disease'),
                             res.get('confidence'), res.get('verdict')))
                else:
                    print('[D200] 巡检失败：%s' % res.get('msg'))
            except Exception as e:
                print('[D200] 巡检异常：%s: %s' % (type(e).__name__, e))
    finally:
        with _lock:
            _state['thread_alive'] = False


def init_camera():
    """惰性启动摄像头拉流线程 + 自动巡检线程（幂等）。配置关闭时直接返回。"""
    global _thread
    if not getattr(config, 'D200_ENABLED', True):
        return False
    cam = get_cam()
    cam.start()
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_inspect_loop, name='d200-inspect', daemon=True)
        _thread.start()
    return True


# ================= 接口 =================
@camera_bp.route('/status')
def camera_status():
    init_camera()          # 兜底：任何一次访问都保证后台线程在跑
    cam = get_cam()
    st = cam.status()
    with _lock:
        st['inspect'] = dict(_inspect_cfg)
        st['state'] = {k: v for k, v in _state.items()}
    return jsonify({'status': 'ok', 'camera': st})


@camera_bp.route('/snapshot')
def camera_snapshot():
    init_camera()
    cam = get_cam()
    jpg = cam.latest(timeout=4, max_age=FRAME_MAX_AGE)
    if not jpg:
        st = cam.status()
        return jsonify({'status': 'error', 'msg': 'camera offline',
                        'detail': st.get('last_error') or
                                  ('frame stale %ss' % st.get('last_frame_age_sec'))}), 503
    return Response(jpg, mimetype='image/jpeg',
                    headers={'Cache-Control': 'no-store, max-age=0'})


@camera_bp.route('/stream')
def camera_stream():
    """MJPEG 推流：看板用 <img src="/api/camera/stream"> 就能看到画面。

    每帧都来自常驻连接的内存缓存，不会额外连 D200。
    """
    init_camera()

    def gen():
        cam = get_cam()
        seq = -1
        idle = 0
        hard_deadline = time.time() + 1800      # 单连接最长 30 分钟，避免僵尸流
        while time.time() < hard_deadline:
            new_seq, frame = cam.wait_frame(seq, timeout=10)
            # 关键：用"帧序号有没有前进"判断是否真有新帧，而不是判断 frame 是否为空。
            # D200 掉线/被挤掉时缓存里会一直留着最后一帧，frame 永远非空；
            # 旧写法下 idle 永不累加 → 推流无限重播同一张静止画面 → 前端 <img>
            # 收不到 error 也就不会重连，表现就是"看板画面冻结、刷新也没用"。
            if not frame or new_seq == seq:
                idle += 1
                if idle > 6:                    # 约 60 秒没有新帧 → 结束，前端会自动重连
                    break
                continue
            seq = new_seq
            idle = 0
            yield (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                   + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n')

    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame',
                    headers={'Cache-Control': 'no-store, max-age=0'})


@camera_bp.route('/latest')
def camera_latest():
    with _lock:
        res = _state['last_result']
        count = _state['inspection_count']
        last_ts = _state['last_ts']
    if not res:
        return jsonify({'status': 'empty', 'msg': '尚无巡检结果',
                        'inspection_count': count})
    return jsonify({'status': 'ok', 'inspection_count': count,
                    'last_ts': last_ts, 'result': res})


@camera_bp.route('/capture', methods=['POST'])
def camera_capture():
    """手动抓拍识别（看板按钮 / APP 调用）"""
    init_camera()
    body = request.get_json(silent=True) or {}
    res = run_inspection(trigger=body.get('trigger', 'manual'))
    code = 200 if res.get('status') == 'ok' else 503
    return jsonify(res), code


@camera_bp.route('/inspect', methods=['GET', 'POST'])
def camera_inspect_config():
    if request.method == 'GET':
        with _lock:
            return jsonify({'status': 'ok', 'inspect': dict(_inspect_cfg),
                            'state': {k: v for k, v in _state.items()}})

    body = request.get_json(silent=True) or {}
    if 'enabled' in body:
        _inspect_cfg['enabled'] = bool(body['enabled'])
        if _inspect_cfg['enabled']:
            init_camera()
    if 'interval' in body:
        try:
            _inspect_cfg['interval'] = max(30, int(body['interval']))
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'msg': 'interval must be an integer (seconds)'}), 400
    with _lock:
        return jsonify({'status': 'ok', 'inspect': dict(_inspect_cfg)})
