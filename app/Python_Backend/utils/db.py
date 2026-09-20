import sqlite3
from datetime import datetime

import config


def get_db_connection():
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def insert_disease_record(disease, confidence, location='', image_path='', source='auto'):
    """把一条病害识别结果写入 disease_records（各识别入口共用）。

    image_path 用相对 Python_Backend 的路径（如 'uploads/xxx.jpg'），
    前端通过 /api/uploads/<name> 访问。
    返回新记录 id。
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO disease_records (disease, confidence, location, timestamp, image_path, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (disease, float(confidence), location,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'), image_path, source))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id
