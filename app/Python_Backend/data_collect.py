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
                )''')
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
