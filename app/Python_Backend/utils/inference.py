"""服务端 TFLite 病害识别 + 三档拒识判定（P2，多个入口共用）

同一个识别内核被三条链路复用：
    - /api/upload_image   ESP32-CAM / APP 手动拍照上传
    - /api/camera/capture D200 网络摄像头抓帧
    - 自动巡检线程         D200 定时抓帧

三档阈值（开发文档 v2.2 §4.4）：
    >= CONFIRMED_THRESHOLD   confirmed 确诊：写入 disease_records
    >= SUSPECTED_THRESHOLD   suspected 疑似：不入库，提示"建议重拍"
    <  SUSPECTED_THRESHOLD   rejected  拒识：不入库，提示"请对准叶片重拍"

阈值与判定逻辑集中在此处，任何端都不要再自己写一份。
"""
import os
import threading

# app/Python_Backend/utils/inference.py -> Python_Backend -> app
_APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL_PATH = os.path.join(_APP_DIR, 'AI_Model', 'model.tflite')
LABELS_PATH = os.path.join(_APP_DIR, 'AI_Model', 'labels.txt')

CONFIRMED_THRESHOLD = 75.0
SUSPECTED_THRESHOLD = 45.0

VERDICT_CONFIRMED = 'confirmed'
VERDICT_SUSPECTED = 'suspected'
VERDICT_REJECTED = 'rejected'

# 给前端 / APP 直接展示的中文结论
VERDICT_TEXT = {
    VERDICT_CONFIRMED: '已确诊，已写入巡检记录',
    VERDICT_SUSPECTED: '疑似，建议对准病叶重拍确认',
    VERDICT_REJECTED: '无法可靠识别，请对准叶片重新拍摄',
}


def classify_verdict(confidence):
    """confidence 为 0~100 百分数，返回 confirmed / suspected / rejected"""
    if confidence >= CONFIRMED_THRESHOLD:
        return VERDICT_CONFIRMED
    if confidence >= SUSPECTED_THRESHOLD:
        return VERDICT_SUSPECTED
    return VERDICT_REJECTED


def _get_interpreter():
    """懒加载 + 进程内缓存解释器。

    原来每次调用都重建 Interpreter（要重新读 9MB 模型 + allocate_tensors，约 1~2 秒）；
    D200 自动巡检 / 手动抓拍 / APP 上传都会调用，缓存后第一次之后基本无感。
    TFLite Interpreter 不是线程安全的，所以 invoke 时用 _INFER_LOCK 串行化。
    """
    global _interp, _labels
    with _LOAD_LOCK:
        if _interp is None:
            try:
                import tflite_runtime.interpreter as tfl
                _interp = tfl.Interpreter(model_path=MODEL_PATH)
            except ImportError:
                import tensorflow as tf
                _interp = tf.lite.Interpreter(model_path=MODEL_PATH)
            _interp.allocate_tensors()
            with open(LABELS_PATH, encoding='utf-8') as f:
                _labels = [l.strip() for l in f if l.strip()]
        return _interp, _labels


_interp = None
_labels = []
_LOAD_LOCK = threading.Lock()
_INFER_LOCK = threading.Lock()


def try_inference(source):
    """服务端推理入口。

    source: 图片文件路径(str) 或 图片字节(bytes/bytearray, 如 D200 抓到的 JPEG)
    返回 {'disease': str, 'confidence': float(0~100)}；缺库/出错返回 None（优雅降级）
    """
    try:
        import io
        import numpy as np
        from PIL import Image

        if isinstance(source, (bytes, bytearray)):
            img = Image.open(io.BytesIO(bytes(source)))
        else:
            img = Image.open(source)
        img = img.convert('RGB').resize((224, 224))
        x = (np.asarray(img, dtype=np.float32) / 255.0)[None, ...]

        interp, labels = _get_interpreter()
        inp = interp.get_input_details()[0]
        out = interp.get_output_details()[0]
        with _INFER_LOCK:
            interp.set_tensor(inp['index'], x)
            interp.invoke()
            y = interp.get_tensor(out['index'])[0].astype(np.float32)
        # Keras 模型最后一层通常已带 softmax，输出总和≈1；
        # 此时直接取 max 当置信度（二次 softmax 会把 90% 压成 ~6%）。
        # 仅当输出是原始 logits（总和不等于 1）时才需要 softmax。
        if abs(float(y.sum()) - 1.0) < 0.01:
            prob = y
        else:
            e = np.exp(y - y.max())
            prob = e / e.sum()
        idx = int(prob.argmax())
        return {'disease': labels[idx] if idx < len(labels) else str(idx),
                'confidence': round(float(prob[idx]) * 100, 1)}
    except Exception:
        import traceback
        traceback.print_exc()   # 别把真实错误吞掉，打到控制台方便排查
        return None


def describe_result(result):
    """把推理结果 + 三档判定打包成统一响应片段（给各接口复用）"""
    if not result:
        return {}
    verdict = classify_verdict(result['confidence'])
    return {
        'verdict': verdict,
        'verdict_text': VERDICT_TEXT[verdict],
        'disease': result['disease'],
        'confidence': result['confidence'],
    }
