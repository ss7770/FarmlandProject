# -*- coding: utf-8 -*-
"""
离线语音识别接口（Vosk，2026-10-07）。

    POST /api/asr          请求体 = WAV（16kHz / 16bit / 单声道）→ {"ok": true, "text": "..."}
    GET  /api/asr/status   模型是否就绪（APP 侧与人工排查都用得上，不触发加载）

为什么要有它：
    小慧对话框的语音输入原本走安卓原生 SpeechRecognizer，但那台设备上
    ① 内嵌识别恒回 ERROR_RECOGNIZER_BUSY（识别服务在、但干不了活）；
    ② 系统语音界面不存在（RecognizerIntent 抛 ActivityNotFoundException）。
    两条原生路都是死的（已按官方文档确认包可见性不是原因）。
    于是改成「APP 只负责录音 → WAV 传到这里 → PC 用 Vosk 离线识别」：
    不依赖设备的语音服务，也不需要外网。

模型：vosk-model-small-cn-0.22（约 42MB），放在 app/Python_Backend/models/ 下。
    ⚠️ 模型没准备好时本接口返回 **503**（不是 500），APP 据此提示"语音功能未配置"。
    懒加载：第一次请求才 Model(...)（约 1~2 秒），之后常驻内存复用。
"""
import io
import json
import os
import threading
import time
import wave

from flask import Blueprint, jsonify, request

import config

asr_bp = Blueprint('asr', __name__, url_prefix='/api')

# 后端根目录（blueprints/ 的上一级）：模型路径按它拼绝对路径，
# 这样无论从哪个工作目录启动 app.py，都能找到模型
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_model = None
_model_error = None
_model_lock = threading.Lock()


def _model_path():
    return os.path.join(_BASE_DIR, config.ASR_MODEL_DIR)


def _get_model():
    """懒加载 Vosk 模型（进程内只加载一次）。返回 (model, error)。"""
    global _model, _model_error
    if _model is not None or _model_error is not None:
        return _model, _model_error
    with _model_lock:
        if _model is not None or _model_error is not None:
            return _model, _model_error
        path = _model_path()
        if not os.path.isdir(path):
            _model_error = '模型目录不存在：%s' % path
            print('[ASR] ' + _model_error)
            return None, _model_error
        try:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)              # 关掉 vosk 自己的日志，别刷屏
            print('[ASR] 正在加载 Vosk 模型：%s' % path)
            t0 = time.time()
            _model = Model(path)
            print('[ASR] 模型加载完成，用时 %.1fs' % (time.time() - t0))
        except Exception as e:           # 加载失败也要能优雅降级，不能把请求打崩
            _model_error = '模型加载失败：%s' % e
            print('[ASR] ' + _model_error)
    return _model, _model_error


def _extract_pcm(raw):
    """请求体 → (pcm_bytes, sample_rate, error)。

    只认标准 WAV（APP 端就是按这个格式拼的）；裸 PCM 需要带 ?rate=16000。
    """
    if len(raw) >= 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WAVE':
        try:
            with wave.open(io.BytesIO(raw), 'rb') as w:
                if w.getsampwidth() != 2:
                    return b'', 0, 'need_16bit'
                if w.getnchannels() != 1:
                    return b'', 0, 'need_mono'
                return w.readframes(w.getnframes()), w.getframerate(), None
        except Exception as e:
            return b'', 0, 'bad_wav:%s' % e
    try:
        rate = int(request.args.get('rate', '16000'))
    except ValueError:
        rate = 16000
    return raw, rate, None


@asr_bp.route('/asr/status', methods=['GET'])
def asr_status():
    """模型是否就绪（不触发加载）。"""
    path = _model_path()
    return jsonify({
        'enabled': bool(config.ASR_ENABLED),
        'model_dir': path,
        'model_present': os.path.isdir(path),
        'loaded': _model is not None,
        'error': _model_error,
    })


@asr_bp.route('/asr', methods=['POST'])
def asr():
    if not config.ASR_ENABLED:
        return jsonify({'ok': False, 'error': 'disabled'}), 503

    raw = request.get_data()
    if not raw:
        return jsonify({'ok': False, 'error': 'empty'}), 400

    pcm, rate, err = _extract_pcm(raw)
    if err:
        return jsonify({'ok': False, 'error': err}), 400
    if rate != 16000:
        # 识别器创建时就要定采样率，统一要求 16k（APP 端固定录 16k）
        return jsonify({'ok': False, 'error': 'need_16k', 'rate': rate}), 400
    if len(pcm) < 3200:                  # 不足 0.1 秒，没意义，直接回空
        return jsonify({'ok': True, 'text': '', 'note': 'too_short'})

    model, merr = _get_model()
    if model is None:
        return jsonify({'ok': False, 'error': 'model', 'detail': merr}), 503

    t0 = time.time()
    try:
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(model, 16000)
        step = 8000                      # 0.25s 一块地喂，长音频更稳
        for i in range(0, len(pcm) - step + 1, step):
            rec.AcceptWaveform(pcm[i:i + step])
        tail = pcm[(len(pcm) // step) * step:]
        if tail:
            rec.AcceptWaveform(tail)
        text = (json.loads(rec.FinalResult()).get('text') or '')
        # ⚠️ Vosk 的中文结果词之间带空格（"你 好 小 慧"），拼回去才是人话
        text = text.replace(' ', '')
    except Exception as e:
        print('[ASR] 识别失败：%s' % e)
        return jsonify({'ok': False, 'error': 'infer', 'detail': str(e)}), 500

    ms = int((time.time() - t0) * 1000)
    print('[ASR] %.1fs 音频 → 「%s」（耗时 %dms）' % (len(pcm) / 32000.0, text, ms))
    return jsonify({'ok': True, 'text': text, 'ms': ms})
