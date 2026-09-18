from flask import Blueprint, request, jsonify
from utils.db import get_db_connection
from datetime import datetime

disease_bp = Blueprint('disease', __name__, url_prefix='/api/disease')

def _ensure_table():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS disease_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disease TEXT NOT NULL,
            confidence REAL NOT NULL,
            location TEXT DEFAULT '',
            timestamp TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

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

    return jsonify({'status': 'ok', 'records': [dict(r) for r in rows]})

@disease_bp.route('/latest', methods=['GET'])
def latest_records():
    """APP 轮询接口（文档 v1.4 §3.3）：只返回 id > after_id 的新记录"""
    after_id = request.args.get('after_id', default=0, type=int)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM disease_records WHERE id > ? ORDER BY id ASC LIMIT 20",
        (after_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return jsonify({'status': 'ok', 'records': [dict(r) for r in rows]})
