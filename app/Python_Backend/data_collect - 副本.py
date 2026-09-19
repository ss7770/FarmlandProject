import serial
import time
import sqlite3

# 设定端口和波特率
ser = serial.Serial('COM4', 115200, timeout=1)

# 建立数据库
conn =  sqlite3.connect('sensor.db')
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS data
                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
                temp INTEGER,
                humi INTEGER,
                light INTEGER,
                soil INTEGER,
                water INTEGER
import socket
import sqlite3
import threading

# 监听配置
HOST = '0.0.0.0'   # 监听所有网卡
PORT = 8888         # 自定义端口，ESP8266 要连这个

# 建立数据库
conn = sqlite3.connect('sensor.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS data
                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
                temp INTEGER,
                humi INTEGER,
                light INTEGER,
                soil INTEGER,
                water INTEGER
                )''')
conn.commit()

# 数据库锁，防止多线程冲突
db_lock = threading.Lock()


def handle_client(client_sock, addr):
    """处理一个 ESP8266 连接"""
    print(f"[连接] 来自 {addr}")
    buffer = ""
    try:
        while True:
            data = client_sock.recv(1024)
            if not data:
                break
            buffer += data.decode('utf-8', errors='ignore')

            # 按换行符切分，处理粘包
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                line = line.strip()
                if not line:
                    continue

                if 'TEMP' in line:
                    # 去掉前缀 TEMP: 如果存在
                    if ': ' in line and 'TEMP: ' in line:
                        line = line.split('TEMP: ', 1)[1]
                    try:
                        parts = line.split(',')
                        temp = int(parts[0].split(':')[1])
                        humi = int(parts[1].split(':')[1])
                        light = int(parts[2].split(':')[1])
                        soil = int(parts[3].split(':')[1])
                        water = int(parts[4].split(':')[1])

                        with db_lock:
                            cursor.execute(
                                "INSERT INTO data (temp, humi, light, soil, water) VALUES (?, ?, ?, ?, ?)",
                                (temp, humi, light, soil, water))
                            conn.commit()
                        print(f"[存入] {temp}°C, {humi}%, {light}Lux, {soil}%, {water}")
                    except (IndexError, ValueError) as e:
                        print(f"[解析失败] {line} | 原因: {e}")
                else:
                    print(f"[忽略] {line}")
    except ConnectionResetError:
        print(f"[断开] {addr} 强制关闭")
    finally:
        client_sock.close()
        print(f"[关闭] {addr}")


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)
    print(f"[启动] TCP 服务器监听 {HOST}:{PORT}")

    while True:
        client_sock, addr = server.accept()
        t = threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True)
        t.start()


if __name__ == '__main__':
    main()                )''')
conn.commit()

while True:
    line = ser.readline()
    if line:
        text = line.decode().strip()
        
        if 'TEMP' in text:
            if ': ' in text:
                text = text.split(': ', 1)[1]
            parts = text.split(',')
            temp = int(parts[0].split(':')[1])
            print("收到传感器数据:", text)
            # 解析数据，格式是 TEMP:25,HUMI:50,LIGHT:91,SOIL:52,WATER:2000
            temp = int(parts[0].split(':')[1])
            humi = int(parts[1].split(':')[1])
            light = int(parts[2].split(':')[1])
            soil = int(parts[3].split(':')[1])
            water = int(parts[4].split(':')[1])
            
            cursor.execute("INSERT INTO data (temp, humi, light, soil, water) VALUES (?, ?, ?, ?, ?)",
                           (temp, humi, light, soil, water))
            conn.commit()
            print(f"存入: {temp}°C, {humi}%, {light}Lux, {soil}%, {water}")
        else:
            print("忽略:", text)

    time.sleep(0.1)
