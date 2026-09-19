# -*- coding: utf-8 -*-
"""
传感器数据采集器（P1 统一入口版）：
连接 ESP-01S TCP 服务器，解析 TEMP/HUMI/LIGHT/SOIL/WATER 文本行后，
优先 POST 到 Flask /api/sensor 统一入库；Flask 不可用时回退直写 sensor.db。
"""
import json
import socket
import sqlite3
import time
import urllib.request

ESP_IP = '192.168.57.182'   # 换成 ESP 实际 IP
ESP_PORT = 8288
FLASK_BASE = 'http://127.0.0.1:5000'   # Flask 后端地址（同机部署默认本机）

conn = sqlite3.connect('sensor.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS data
                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
                temp INTEGER, humi INTEGER, light INTEGER, soil INTEGER, water INTEGER)''')
conn.commit()


def save_via_api(payload):
    """统一入口：POST /api/sensor，成功返回 True"""
    try:
        req = urllib.request.Request(
            FLASK_BASE + '/api/sensor',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST')
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read().decode('utf-8'))
            return body.get('status') == 'ok'
    except Exception as e:
        print(f'[API失败] {e}，回退直写数据库')
        return False


def save_via_db(row):
    """回退路径：Flask 不可用时直写 SQLite"""
    cursor.execute("INSERT INTO data (temp,humi,light,soil,water) VALUES (?,?,?,?,?)", row)
    conn.commit()


while True:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((ESP_IP, ESP_PORT))
        print(f"[已连接] {ESP_IP}:{ESP_PORT}")
        buf = ""
        while True:
            data = s.recv(1024)
            if not data:
                break
            buf += data.decode('utf-8', errors='ignore')
            while '\n' in buf:
                line, buf = buf.split('\n', 1)
                line = line.strip()
                if 'TEMP' in line:
                    try:
                        parts = line.split(',')
                        temp = int(parts[0].split(':')[1])
                        humi = int(parts[1].split(':')[1])
                        light = int(parts[2].split(':')[1])
                        soil = int(parts[3].split(':')[1])
                        water = int(parts[4].split(':')[1])
                        payload = {'temp': temp, 'humi': humi, 'light': light,
                                   'soil': soil, 'water': water, 'device': 'data_collect'}
                        if save_via_api(payload):
                            print(f"[API存入] {temp}°C, {humi}%, {light}Lux, {soil}%, {water}")
                        else:
                            save_via_db((temp, humi, light, soil, water))
                            print(f"[直写入库] {temp}°C, {humi}%, {light}Lux, {soil}%, {water}")
                    except Exception as e:
                        print(f"[解析失败] {line} | {e}")
    except Exception as e:
        print(f"[连接失败] {e}，3秒后重试")
        time.sleep(3)
