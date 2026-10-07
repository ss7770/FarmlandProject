import sqlite3
from datetime import datetime

import config


def get_db_connection():
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------------
# 建表 + 补列（老库升级用）
# ------------------------------------------------------------------
_SCHEMA_READY = False

_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS disease_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        disease TEXT NOT NULL,
        confidence REAL NOT NULL,
        location TEXT DEFAULT '',
        timestamp TEXT NOT NULL,
        image_path TEXT DEFAULT '',
        source TEXT DEFAULT 'manual',
        client_uid TEXT DEFAULT ''
    )
"""

# 后加的列：老库（只建过前 5 列的）要能自动补上，否则 INSERT 会直接报 no such column。
# 为什么用 ALTER TABLE 而不是重建：记录是现场数据，重建会丢。
_EXTRA_COLUMNS = (
    ('image_path', "TEXT DEFAULT ''"),
    ('source', "TEXT DEFAULT 'manual'"),
    ('client_uid', "TEXT DEFAULT ''"),
)


def ensure_schema():
    """建表 + 给老库补列（幂等，进程内只跑一次）。

    为什么需要它（2026-10-01）：
        disease_records 的表结构是"用着用着加列"长大的（先 image_path、再 source、
        现在 client_uid）。老库里少了某一列时，INSERT 会报 `no such column`，
        而且报错点在写入路径上 —— 表现为"设备一直在上报，看板永远没新记录"，
        很难一眼看出是表结构问题。所以写入前统一兜一次。
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(_TABLE_SQL)
        cols = {r[1] for r in cur.execute("PRAGMA table_info(disease_records)")}
        for name, ddl in _EXTRA_COLUMNS:
            if name not in cols:
                cur.execute("ALTER TABLE disease_records ADD COLUMN %s %s" % (name, ddl))
        conn.commit()
    finally:
        conn.close()
    _SCHEMA_READY = True


def insert_disease_record(disease, confidence, location='', image_path='',
                          source='auto', client_uid=''):
    """把一条病害识别结果写入 disease_records（各识别入口共用）。

    image_path 用相对 Python_Backend 的路径（如 'uploads/xxx.jpg'），
    前端通过 /api/uploads/<name> 访问。
    client_uid 是**设备端自带的关联 id**（可选）：K230 的结论和图片分两次请求上报，
    图片上传时拿它来配对（见 find_record_id_by_uid）。
    返回新记录 id。
    """
    ensure_schema()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO disease_records "
        "(disease, confidence, location, timestamp, image_path, source, client_uid) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (disease, float(confidence), location,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'), image_path, source,
         client_uid or ''))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def get_disease_record(record_id):
    """按 id 取一条记录；不存在返回 None。"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM disease_records WHERE id = ?", (int(record_id),))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def find_record_id_by_uid(client_uid, max_age_sec=900):
    """按设备端上报的 `client_uid` 找记录 id（图片补传配对用）。找不到返回 None。

    为什么要有（2026-10-01 v9）：
        K230 的结论和图片是**两次独立请求**（大包上行不稳，见 blueprints/edge.py）。
        图片本来靠"结论响应头里的 record_id"来定位记录，而那块板子读响应头不稳，
        一旦读不到，图片就彻底没法定记录 —— 表现就是"记录一直在涨、APP 上一张图都没有"。
        现在两次请求都带同一个 client_uid，服务端自己配对，**板端读不到响应也无所谓**。

    选取规则（宽进严出）：
        ① 只认 max_age_sec 内的记录 —— 板子重启后 uid 可能撞号，加时间窗就无害了；
        ② 优先挑 image_path 还是空的那条（一张图贴一条记录）；
        ③ 没有空的就返回最新那条（重复贴同一张图，好过一张图都没有）。
    """
    uid = (client_uid or '').strip()
    if not uid:
        return None
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(seconds=float(max_age_sec))
              ).strftime('%Y-%m-%d %H:%M:%S')
    ensure_schema()
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, image_path FROM disease_records "
        "WHERE client_uid = ? AND timestamp >= ? ORDER BY id DESC LIMIT 20",
        (uid, cutoff))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        return None
    for r in rows:
        if not (r['image_path'] or '').strip():
            return r['id']
    return rows[0]['id']


def set_record_image(record_id, image_path):
    """给**已入库**的记录补上 image_path（UPDATE，不是第二次 INSERT）。

    为什么需要单独一个函数：K230 的结论和图片是分两次请求上报的
    （大包上行不稳，见 blueprints/edge.py::attach_image 的说明），
    结论先入库拿到 record_id，图片随后补传时只能 UPDATE。
    返回 True 表示确实更新到了一行（record_id 不存在则为 False）。
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE disease_records SET image_path = ? WHERE id = ?",
                (image_path, int(record_id)))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def purge_unconfirmed(ttl_min, confirmed_threshold=None, upload_dir=None):
    """删除"未确诊 + 已过期"的记录，连同它们的图片文件。返回 (删除条数, 删除的文件名列表)。

    为什么需要清理（2026-10-01 起）：
        记录策略从"只入确诊"改成"拍到就入库"，未确诊的会以巡检节奏（10 秒一轮）持续累积。
        它们的价值是"短期可追溯"（看看设备到底拍到了什么），过期就没必要留了。

    判定口径：
        confidence < confirmed_threshold  → 未确诊
        timestamp < now - ttl_min         → 已过期
    timestamp 存的是 '%Y-%m-%d %H:%M:%S' 本地时间字符串，字典序即时间序，
    所以直接拿格式化后的 cutoff 做字符串比较即可，不用引入时间解析。

    图片删除：只删 image_path 指向 uploads/ 下的文件；删不掉只记数不上抛
    （记录已经删了，留个孤儿文件不致命，不该因此让整次清理失败）。
    """
    import os
    from datetime import datetime, timedelta

    if confirmed_threshold is None:
        from utils.inference import CONFIRMED_THRESHOLD
        confirmed_threshold = CONFIRMED_THRESHOLD
    if upload_dir is None:
        # utils/db.py -> utils -> Python_Backend/uploads
        upload_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')

    cutoff = (datetime.now() - timedelta(minutes=float(ttl_min))).strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, image_path FROM disease_records "
        "WHERE confidence < ? AND timestamp < ?",
        (float(confirmed_threshold), cutoff))
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return 0, []

    ids = [r['id'] for r in rows]
    removed_files = []
    for r in rows:
        p = (r['image_path'] or '').strip()
        if not p:
            continue
        name = p.split('/')[-1]
        try:
            fp = os.path.join(upload_dir, name)
            if os.path.isfile(fp):
                os.remove(fp)
                removed_files.append(name)
        except OSError:
            pass

    cur.execute(
        "DELETE FROM disease_records WHERE id IN (%s)" % ','.join('?' * len(ids)), ids)
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted, removed_files
