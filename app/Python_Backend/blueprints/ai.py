# -*- coding: utf-8 -*-
"""
AI 灌溉建议接口（P1，开发文档 §5.2/5.4）。

GET /api/ai/recommendation：
- 融合 传感器现状 + 墒情预测（ai/soil_predictor）+ 天气 + 最新病害识别记录
- 输出 Recommendation JSON：风险等级 / 是否建议灌溉 / 目标湿度 / 建议运行时长 / 决策理由
- 架构铁律：AI 只有建议权（ai_role=advisory），STM32 安全层持有否决权
  （水位联锁、滞回、最小启停时间、最长运行保护），即使采纳建议也不越过安全层
"""
from datetime import datetime, timedelta

from flask import Blueprint, jsonify

from ai import soil_predictor
from utils import fetch_weather
from utils.db import get_db_connection
from utils.sensor import fetch_lastest_data

ai_bp = Blueprint('ai', __name__, url_prefix='/api/ai')

# 与 STM32 g_IrrConfig 默认值对齐（APP 修改阈值仅作用于下位机，这里为建议口径）
SOIL_START = 10   # 低于此值建议灌溉（%）
SOIL_TARGET = 25  # 建议达到的土壤湿度（%）
# 经验速率：水泵运行约 1 秒提升土壤湿度 0.08%（按 30s≈+2.4% 校准，可现场微调）
PUMP_RATE_PER_SEC = 0.08


def _latest_disease(hours=24):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM disease_records ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    rec = dict(row)
    try:
        ts = datetime.strptime(rec.get('timestamp', ''), '%Y-%m-%d %H:%M:%S')
        if datetime.now() - ts > timedelta(hours=hours):
            return None  # 太旧的记录不参与决策
    except (TypeError, ValueError):
        return None
    return rec


def _build_recommendation(forecast, weather, disease):
    soil_now = forecast.get('soil_now')
    pred30 = forecast.get('soil_30', soil_now)
    pred60 = forecast.get('soil_60', soil_now)
    worst = min(v for v in (soil_now, pred30, pred60) if v is not None)

    reasons = []
    if forecast.get('stale'):
        reasons.append('注意：传感器数据已陈旧（最后上报 %s），建议检查数据链路'
                       % forecast.get('last_ts', '?'))
    if forecast.get('method') == 'linear_fallback':
        reasons.append('墒情预测退化为线性外推（模型未就绪或历史不足）')
    else:
        reasons.append('模型预测未来 30/60 分钟土壤湿度为 %.1f%% / %.1f%%'
                       % (pred30, pred60))

    risk = '低'
    if worst < 8:
        risk = '高'
    elif worst < 12:
        risk = '中'
    reasons.append('当前土壤湿度 %.1f%%，最小值（含预测）%.1f%% → 风险等级 %s'
                   % (soil_now, worst, risk))

    should_irrigate = worst < SOIL_START
    if should_irrigate:
        reasons.append('预测墒情将跌破 %d%% 启动阈值，建议提前灌溉（预测式灌溉）' % SOIL_START)
    else:
        reasons.append('墒情在安全区间，暂无需灌溉')

    # 天气融合：预报有降雨 → 建议暂缓，靠降雨自然补水
    if isinstance(weather, dict) and 'reason' not in weather:
        desc = str(weather.get('weather', ''))
        rain = weather.get('rain24h')
        if '雨' in desc or (isinstance(rain, (int, float)) and rain and rain > 0):
            if should_irrigate:
                reasons.append('预报 %s（24h 降雨 %s mm），建议暂缓灌溉，优先等待降雨'
                               % (desc, rain))
                should_irrigate = False
            else:
                reasons.append('预报 %s，未来墒情有望自然补充' % desc)
    else:
        reasons.append('天气服务不可用，本建议未纳入天气因素')

    # 病害融合：检出病害 → 提示避免叶面喷灌类操作
    if disease and not str(disease.get('disease', '')).endswith('healthy'):
        conf = float(disease.get('confidence', 0))
        if conf <= 1.0:
            conf *= 100  # 兼容 0~1 存储；库里现行为 0~100
        reasons.append('近期检出 %s（置信度 %.0f%%），建议避免提高叶面湿度，加强通风'
                       % (disease.get('disease'), conf))

    deficit = max(0.0, SOIL_TARGET - worst)
    pump_seconds = 0
    if should_irrigate:
        pump_seconds = int(min(max(deficit / PUMP_RATE_PER_SEC, 30), 120))  # 对齐下位机 30~120s 保护
        reasons.append('建议小水量分阶段灌溉：目标湿度 %d%%，预计运行 %d 秒'
                       % (SOIL_TARGET, pump_seconds))

    return {
        'risk_level': risk,
        'should_irrigate': should_irrigate,
        'target_soil': SOIL_TARGET,
        'suggested_pump_seconds': pump_seconds,
        'horizon_min': 60,
        'reasons': reasons,
        'ai_role': 'advisory',
        'safety_note': 'AI 仅有建议权；STM32 安全层持有否决权（水位联锁/滞回/最短最长运行保护），'
                       '采纳建议也须经下位机安全校验后执行',
    }


@ai_bp.route('/recommendation', methods=['GET'])
def recommendation():
    try:
        latest = fetch_lastest_data()
    except Exception:
        return jsonify({'status': 'error', 'msg': 'no sensor data in db'}), 404

    try:
        forecast = soil_predictor.predict_from_db()
    except Exception as e:
        forecast = {'method': 'unavailable', 'stale': True, 'error': str(e),
                    'soil_now': latest.get('soil')}

    try:
        weather = fetch_weather()
    except Exception:
        weather = None

    disease = _latest_disease()

    return jsonify({
        'status': 'ok',
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'sensor': latest,
        'forecast': forecast,
        'weather': weather,
        'disease': disease,
        'recommendation': _build_recommendation(forecast, weather, disease),
    })
