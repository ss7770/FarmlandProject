from .db import get_db_connection
from datetime import datetime, timedelta

def fetch_lastest_data():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM data ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return dict(row)

def fetch_history_data(limit,field_name):
    conn = get_db_connection()
    cur = conn.cursor()

    start_time = datetime.now() - timedelta(hours=limit)

    cur.execute("""SELECT * FROM data WHERE timestamp >= ? ORDER BY timestamp ASC """, (start_time.strftime('%Y-%m-%d %H:%M:%S'),))

    rows = cur.fetchall()
    conn.close()

    data = [dict(row) for row in rows]
    times = [row['timestamp'] for row in data]
    valus = [row[field_name] for row in data]
    
    return {
        'times': times,
        'values': valus
    }
