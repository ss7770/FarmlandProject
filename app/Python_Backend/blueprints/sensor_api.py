from flask import Blueprint, jsonify, request
from utils.db import get_db_connection
from utils.sensor import fetch_lastest_data

# 统一传感器数据入口（P1）：
# - Android APP / data_collect.py 等任何端，都通过 POST /api/sensor 写入 sensor.db
# - 数据带时间戳持续入库，作为土壤墒情预测模型的训练数据源
sensor_bp = Blueprint('sensor_api', __name__, url_prefix='/api/sensor')

# 字段合法性范围：与 STM32 上报协议（TEMP/HUMI/LIGHT/SOIL/WATER）一一对应
_FIELD_RULES = {
    'temp': (-50, 100),    # 温度 °C（DHT11 实际 0~50，放宽便于调试）
    'humi': (0, 100),      # 空气湿度 %
    'light': (0, 65535),   # 光照 Lux（BH1750 16 位上限）
    'soil': (0, 100),      # 土壤湿度 %
    'water': (0, 4095),    # 水位 ADC 原始值（12 位 ADC）
}


@sensor_bp.route('', methods=['POST'])
def receive_sensor_data():
    """POST /api/sensor
    body: {"temp":25,"humi":60,"light":500,"soil":35,"water":2048}
    可选: {"device":"stm32-01"}
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({'status': 'error', 'msg': 'request body must be a JSON object'}), 400

    values = {}
    for field, (lo, hi) in _FIELD_RULES.items():
        if field not in payload:
            return jsonify({'status': 'error', 'msg': 'missing field: ' + field}), 400
        try:
            v = int(payload[field])
        except (TypeError, ValueError):
            return jsonify({'status': 'error',
                            'msg': 'field %s must be an integer' % field}), 400
        if v < lo or v > hi:
            return jsonify({'status': 'error',
                            'msg': 'field %s out of range [%d, %d]' % (field, lo, hi)}), 400
        values[field] = v

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO data (temp,humi,light,soil,water) VALUES (?,?,?,?,?)",
        (values['temp'], values['humi'], values['light'], values['soil'], values['water'])
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()

    return jsonify({'status': 'ok', 'id': new_id, 'saved': values})


@sensor_bp.route('/latest', methods=['GET'])
def latest_sensor_data():
    """GET /api/sensor/latest —— 最新一条传感器数据（含服务器落库时间戳）"""
    try:
        row = fetch_lastest_data()
    except Exception:
        return jsonify({'status': 'error', 'msg': 'no sensor data yet'}), 404
    return jsonify({'status': 'ok', 'data': row})
