import socket, sqlite3, time

ESP_IP = '192.168.57.182'   # 换成 ESP 实际 IP
ESP_PORT = 8288

conn = sqlite3.connect('sensor.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS data
                (id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
                temp INTEGER, humi INTEGER, light INTEGER, soil INTEGER, water INTEGER)''')
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
                        cursor.execute("INSERT INTO data (temp,humi,light,soil,water) VALUES (?,?,?,?,?)",
                                       (temp,humi,light,soil,water))
                        conn.commit()
                        print(f"[存入] {temp}°C, {humi}%, {light}Lux, {soil}%, {water}")
                    except Exception as e:
                        print(f"[解析失败] {line} | {e}")
    except Exception as e:
        print(f"[连接失败] {e}，3秒后重试")
        time.sleep(3)
