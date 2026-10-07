from flask import Blueprint, request, jsonify
from utils.db import get_db_connection, ensure_schema
from utils.inference import CONFIRMED_THRESHOLD, classify_verdict
from datetime import datetime

disease_bp = Blueprint('disease', __name__, url_prefix='/api/disease')
def _ensure_table():
    """启动时把表结构对齐到最新（含给老库补 image_path / source / client_uid 列）。

    具体口径在 utils/db.py::ensure_schema —— 这里只是"进程启动就跑一次"的挂钩，
    免得第一次上报时才发现少列（那时的报错在写入路径上，很难定位）。
    """
    ensure_schema()

_ensure_table()

@disease_bp.route('/record', methods=['POST'])
def add_record():
    data = request.get_json(silent=True)
    if not data or 'disease' not in data or 'confidence' not in data:
        return jsonify({'status': 'error', 'msg': 'disease and confidence are required'}), 400

    disease = str(data['disease'])
    try:
        confidence = float(data['confidence'])
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'msg': 'confidence must be a number'}), 400

    location = str(data.get('location', ''))
    timestamp = str(data.get('timestamp', '')) or datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO disease_records (disease, confidence, location, timestamp) VALUES (?, ?, ?, ?)",
        (disease, confidence, location, timestamp)
    )
    conn.commit()
    conn.close()

    return jsonify({'status': 'ok'})

@disease_bp.route('/records', methods=['GET'])
def list_records():
    limit = request.args.get('limit', default=50, type=int)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM disease_records "
        "ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    conn.close()

    return jsonify({'status': 'ok', 'records': [_row_to_dict(r) for r in rows]})

@disease_bp.route('/latest', methods=['GET'])
def latest_records():
    """APP 轮询接口（文档 v1.4 §3.3）：只返回 id > after_id 的新记录。

    输出结构与 /overview 一致（走 _row_to_dict），所以 APP 那边可以统一按
    diagnosed / display_name 渲染——未确诊的记录不给病名、不播报。
    """
    after_id = request.args.get('after_id', default=0, type=int)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM disease_records WHERE id > ? ORDER BY id ASC LIMIT 20",
        (after_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return jsonify({'status': 'ok', 'records': [_row_to_dict(r) for r in rows]})


# 来源标签：'小区分，不必特意强调' —— 前端/APP 用这个词表做低调小标签，
# 不区分"设备型号"，只区分"数据是怎么来的"（自动巡检 / 人为上传）。
SOURCE_TEXT = {
    'k230': '巡检',        # K230 边缘自动巡检
    'auto': '上传',        # 网页/APP 上传照片识别
    'manual': '手动',      # 手动补录
    'd200': '巡检',        # 历史遗留数据，一并归入"巡检"
}

# 未达确诊线的统一文案（2026-10-01：拍到就入库后，这类记录会大量出现）。
# 口径：展示但不诊断——不给病名，只说明"置信率不足"。
UNCONFIRMED_TEXT = '置信率不足'


def source_text(src):
    """来源 -> 低调小标签文案。

    多机位时 source 可能是 'k230-a' 这种带后缀的，按下划线/横线前缀归并，
    这样加了机位也不用改词表。
    """
    s = (src or '').strip()
    if s in SOURCE_TEXT:
        return SOURCE_TEXT[s]
    for sep in ('-', '_'):
        if sep in s:
            head = s.split(sep, 1)[0]
            if head in SOURCE_TEXT:
                return SOURCE_TEXT[head]
    return '巡检' if s.startswith('k230') else '上传'


def _row_to_dict(row):
    """disease_records 行 -> 统一输出结构（补来源标签、图片 URL、展示口径）。

    展示口径为什么放在服务端算（2026-10-01）：
        "未确诊的不做诊断、只显示'置信率不足'"这条规则 Web 和 App 都要遵守，
        各自实现一遍必然漂移。这里一次性算好 diagnosed / verdict / display_name，
        两端直接拿来渲染，改口径只改这一处。
    """
    d = dict(row)
    src = d.get('source') or 'manual'
    d['source'] = src
    d['source_text'] = source_text(src)
    img = d.get('image_path') or ''
    # image_path 存的是 'uploads/xxx.jpg'；访问路径是 '/api/uploads/xxx.jpg'
    d['image_url'] = ('/api/uploads/' + img.split('/')[-1]) if img else ''

    # 置信度归一化：兼容历史遗留的 0~1 小数（现在统一 0~100 百分数）
    try:
        conf = float(d.get('confidence') or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if 0 < conf <= 1.0:
        conf *= 100.0
    d['confidence'] = round(conf, 1)

    d['verdict'] = classify_verdict(conf)
    d['diagnosed'] = conf >= CONFIRMED_THRESHOLD
    # 未达确诊线的不展示病名（不做诊断），只提示"置信率不足"
    d['display_name'] = (d.get('disease') or '--') if d['diagnosed'] else UNCONFIRMED_TEXT
    return d


@disease_bp.route('/overview', methods=['GET'])
def overview():
    """病害识别看板聚合接口（Web 独立看板 + APP 统一页共用，一次请求渲染整页）。

    为什么要有这个接口：新看板要同时显示"设备在不在线 / 最新一次结果 / 历史记录列表"，
    分开打三个接口（/api/edge/status + /api/disease/records + /api/uploads/latest）
    会让 Web 和 APP 各写一套拼装逻辑，容易不一致。这里统一在服务端拼好。

    参数：
        limit  记录列表条数，默认 12

    返回：
        {
          "status": "ok",
          "edge":    { online, last_ts, report_count, accepted_count, ... },   # 设备状态
          "latest":  { disease, confidence, verdict, image_url, ... } | null,  # 最新一条记录
          "records": [ {...带 source_text / image_url...} ]                    # 记录列表
        }
    """
    limit = request.args.get('limit', default=12, type=int)
    if limit < 1:
        limit = 1
    elif limit > 100:
        limit = 100

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM disease_records ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    conn.close()

    records = [_row_to_dict(r) for r in rows]
    latest = records[0] if records else None

    # 设备状态：直接复用 edge 模块的进程内状态，避免两处口径不一致
    edge = {}
    try:
        from blueprints.edge import snapshot
        edge = snapshot()
    except Exception as e:
        edge = {'online': False, 'error': '%s: %s' % (type(e).__name__, e)}

    return jsonify({
        'status': 'ok',
        'edge': edge,
        'latest': latest,
        'records': records,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
