import os
from flask import Blueprint, request, jsonify, send_from_directory
from datetime import datetime
from utils.db import get_db_connection

capture_bp = Blueprint('capture', __name__, url_prefix='/api')
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
# 文档 v1.4：识别置信度达到该百分比才写入 disease_records，否则只存图不打扰用户
CONFIDENCE_THRESHOLD = 60.0

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
    result = _try_inference(path)
    if result:
        if result['confidence'] < CONFIDENCE_THRESHOLD:
            # 置信度不足：不写记录、不触发 APP 通知（文档 v1.4 §3.2）
            return jsonify({'status': 'ok', 'saved': name,
                            'msg': 'image saved (confidence %.1f%% < %.0f%%, not recorded)'
                                   % (result['confidence'], CONFIDENCE_THRESHOLD),
                            'disease': result['disease'], 'confidence': result['confidence']})
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO disease_records (disease, confidence, location, timestamp, image_path, source) "
            "VALUES (?, ?, ?, ?, ?, 'auto')",
            (result['disease'], result['confidence'], 'ESP32-CAM',
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'uploads/' + name))
        conn.commit(); conn.close()
        return jsonify({'status': 'ok', 'saved': name, 'disease': result['disease'],
                        'confidence': result['confidence']})
    return jsonify({'status': 'ok', 'saved': name, 'msg': 'image saved (inference unavailable, see server console)'})

def _try_inference(path):
    """复用 AI_Model/model.tflite 做服务端推理；缺库或出错则返回 None 优雅降级"""
    try:
        import numpy as np
        from PIL import Image
        # 模型在 app/AI_Model/：uploads -> Python_Backend -> app
        model_path = os.path.abspath(os.path.join(UPLOAD_DIR, '..', '..', 'AI_Model', 'model.tflite'))
        labels_path = os.path.abspath(os.path.join(UPLOAD_DIR, '..', '..', 'AI_Model', 'labels.txt'))
        try:
            import tflite_runtime.interpreter as tfl
            interp = tfl.Interpreter(model_path=model_path)
        except ImportError:
            import tensorflow as tf
            interp = tf.lite.Interpreter(model_path=model_path)
        labels = [l.strip() for l in open(labels_path, encoding='utf-8') if l.strip()]
        img = Image.open(path).convert('RGB').resize((224, 224))
        x = (np.asarray(img, dtype=np.float32) / 255.0)[None, ...]
        interp.allocate_tensors()
        inp = interp.get_input_details()[0]; out = interp.get_output_details()[0]
        interp.set_tensor(inp['index'], x); interp.invoke()
        y = interp.get_tensor(out['index'])[0].astype(np.float32)
        # Keras 模型最后一层通常已带 softmax，输出总和≈1；
        # 此时直接取 max 当置信度（二次 softmax 会把 90% 压成 ~6%）。
        # 仅当输出是原始 logits（总和不等于 1）时才需要 softmax。
        if abs(float(y.sum()) - 1.0) < 0.01:
            prob = y
        else:
            e = np.exp(y - y.max()); prob = e / e.sum()
        idx = int(prob.argmax())
        return {'disease': labels[idx] if idx < len(labels) else str(idx),
                'confidence': round(float(prob[idx]) * 100, 1)}
    except Exception:
        import traceback
        traceback.print_exc()   # 别把真实错误吞掉，打到控制台方便排查
        return None

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
    files.sort(reverse=True)
    name = files[0]

    info = _latest_inference_cache.get(name)
    if info is None:
        info = _try_inference(os.path.join(UPLOAD_DIR, name)) or {}
        _latest_inference_cache[name] = info

    resp = {'status': 'ok', 'name': name, 'url': '/api/uploads/' + name}
    if info.get('disease'):
        resp['disease'] = info['disease']
        resp['confidence'] = info['confidence']
    return jsonify(resp)

# {文件名: {'disease':..,'confidence':..}}，仅用于 /api/uploads/latest
_latest_inference_cache = {}

@capture_bp.route('/uploads/<path:name>')
def serve_upload(name):
    """让浏览器能直接访问 uploads/ 下的巡检图片（看板缩略图用）"""
    return send_from_directory(UPLOAD_DIR, name)
