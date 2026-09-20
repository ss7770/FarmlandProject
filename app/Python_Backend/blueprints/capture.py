import os
from flask import Blueprint, request, jsonify, send_from_directory
from datetime import datetime
from utils.db import get_db_connection, insert_disease_record
# 识别内核与三档阈值集中放在 utils/inference.py，与 D200 摄像头链路共用
from utils.inference import (try_inference, classify_verdict, describe_result,
                             CONFIRMED_THRESHOLD, SUSPECTED_THRESHOLD)

capture_bp = Blueprint('capture', __name__, url_prefix='/api')
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')

# 兼容旧调用名（历史代码/测试脚本用过 _try_inference）
_try_inference = try_inference


def _ensure_columns():
    conn = get_db_connection(); cur = conn.cursor()
    for sql in ("ALTER TABLE disease_records ADD COLUMN image_path TEXT DEFAULT ''",
                "ALTER TABLE disease_records ADD COLUMN source TEXT DEFAULT 'manual'"):
        try: cur.execute(sql)
        except Exception: pass  # 已存在
    conn.commit(); conn.close()

_ensure_columns()

@capture_bp.route('/upload_image', methods=['POST'])
def upload_image():
    if 'image' not in request.files:
        return jsonify({'status': 'error', 'msg': 'no image file'}), 400
    file = request.files['image']
    if not file.filename:
        return jsonify({'status': 'error', 'msg': 'empty filename'}), 400

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = datetime.now().strftime('cap_%Y%m%d_%H%M%S.jpg')
    path = os.path.join(UPLOAD_DIR, name)
    file.save(path)

    # 推理可选：装了 tensorflow/pillow 就自动识别入库，否则只存图
    result = try_inference(path)
    if result:
        resp = {'status': 'ok', 'saved': name}
        resp.update(describe_result(result))
        if resp['verdict'] == 'confirmed':
            insert_disease_record(result['disease'], result['confidence'],
                                  location='ESP32-CAM', image_path='uploads/' + name,
                                  source='auto')
        else:
            # 疑似 / 拒识：不写记录、不触发 APP 通知，只回结论供 APP 提示重拍
            resp['msg'] = ('%s (%.1f%%, thresholds: suspected>=%.0f, confirmed>=%.0f, not recorded)'
                           % (resp['verdict'], result['confidence'],
                              SUSPECTED_THRESHOLD, CONFIRMED_THRESHOLD))
        return jsonify(resp)
    return jsonify({'status': 'ok', 'saved': name, 'msg': 'image saved (inference unavailable, see server console)'})

@capture_bp.route('/uploads/latest')
def latest_upload():
    """最近拍摄兜底：返回 uploads/ 里最新的图片名 + 识别结果。
    预置进 uploads 的图片没有 disease_records 记录，
    APP 的“最近拍摄”区域在无记录时走这个接口取图并展示识别结果。
    推理结果按文件名缓存（图片内容不变，30 秒轮询不必反复推理）。"""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    files = [f for f in os.listdir(UPLOAD_DIR)
             if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    if not files:
        return jsonify({'status': 'empty'})
    # 按修改时间排序（不是文件名字典序）：D200 巡检帧 d200_*.jpg 与手动上传 cap_*.jpg
    # 前缀不同，字典序会让"最近拍摄"取错图
    files.sort(key=lambda f: os.path.getmtime(os.path.join(UPLOAD_DIR, f)), reverse=True)
    name = files[0]

    info = _latest_inference_cache.get(name)
    if info is None:
        info = try_inference(os.path.join(UPLOAD_DIR, name)) or {}
        _latest_inference_cache[name] = info

    resp = {'status': 'ok', 'name': name, 'url': '/api/uploads/' + name}
    if info.get('disease'):
        resp.update(describe_result(info))
    return jsonify(resp)

# {文件名: {'disease':..,'confidence':..}}，仅用于 /api/uploads/latest
_latest_inference_cache = {}

@capture_bp.route('/uploads/<path:name>')
def serve_upload(name):
    """让浏览器能直接访问 uploads/ 下的巡检图片（看板缩略图用）"""
    return send_from_directory(UPLOAD_DIR, name)
