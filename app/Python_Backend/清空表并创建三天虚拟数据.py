import sqlite3
import random
import math
from datetime import datetime, timedelta

# 连接数据库
conn = sqlite3.connect('sensor.db')
cursor = conn.cursor()

# 可选：清空旧数据（如果需要从头开始，取消下面注释）
cursor.execute("DELETE FROM data")

# ===== 配置参数 =====
num_days = 3
minutes_interval = 10  # 每10分钟一条
num_points = int(num_days * 24 * 60 / minutes_interval)  # 3*24*60/10 = 432

# 起始时间：三天前
base_time = datetime.now() - timedelta(days=num_days)

print(f"生成 {num_points} 条数据，覆盖过去 {num_days} 天，每 {minutes_interval} 分钟一条...")

for i in range(num_points):
    t = base_time + timedelta(minutes=i * minutes_interval)
    hour = t.hour
    day = t.day  # 用于产生不同天的细微差异

    # ===== 1. 温度 =====
    # 日变化：白天高（14点最高），夜晚低（4点最低）
    temp_base = 22 + 8 * math.sin(math.pi * (hour - 6) / 12)  # 6~18点上升，18~6点下降
    # 每天平均气温略有波动（模拟天气变化）
    daily_offset = 2 * math.sin(day)  # 不同天温度略有差异
    temp = temp_base + daily_offset + random.uniform(-1.2, 1.2)
    
    # 偶尔极端尖峰（2%概率）
    if random.random() < 0.02:
        temp += random.uniform(4, 8) if random.random() < 0.5 else random.uniform(-4, -8)
    
    temp = round(temp, 1)

    # ===== 2. 湿度 =====
    # 与温度负相关，温度高湿度低
    humi_base = 75 - 0.6 * (temp - 18)
    humi = humi_base + random.uniform(-4, 4)
    humi = max(20, min(95, humi))
    humi = round(humi, 1)

    # ===== 3. 光照 =====
    if 6 <= hour <= 18:
        light = 800 * math.sin(math.pi * (hour - 6) / 12) + random.uniform(-80, 80)
        light = max(50, min(1200, light))
    else:
        light = random.uniform(0, 20)
    light = round(light, 1)

    # ===== 4. 土壤湿度 =====
    # 基础趋势：前三天缓慢下降（模拟蒸发），但每天有灌溉事件
    soil_base = 55 - 0.3 * (i / 100)  # 缓慢下降
    soil = soil_base + random.uniform(-3, 3)
    # 每天早晨（6~8点）可能发生灌溉（概率40%）
    if 6 <= hour <= 8 and random.random() < 0.4:
        soil += random.uniform(8, 16)
    # 偶尔随机灌溉（5%概率）
    if random.random() < 0.05:
        soil += random.uniform(10, 20)
    soil = max(20, min(80, soil))
    soil = round(soil, 1)

    # ===== 5. 水位 =====
    # 受降雨和灌溉影响，有缓慢波动
    water_base = 1800 + 150 * math.sin(i / 60)  # 几天内缓慢波动
    water = water_base + random.uniform(-100, 100)
    # 降雨事件（2%概率）
    if random.random() < 0.02:
        water += random.uniform(300, 600)
    # 日常蒸发（缓慢下降）
    water -= 0.5  # 每10分钟下降0.5，模拟蒸发
    water = max(500, min(3500, water))
    water = round(water, 1)

    # 插入数据
    cursor.execute("""
        INSERT INTO data (timestamp, temp, humi, light, soil, water)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (t.strftime("%Y-%m-%d %H:%M:%S"), temp, humi, light, soil, water))

conn.commit()
conn.close()

print(f"✅ 成功插入 {num_points} 条数据（覆盖 {num_days} 天，每 {minutes_interval} 分钟一条）")
print("数据特征概览：")
print("- 温度：日变化（18~32℃），含随机波动和偶发尖峰")
print("- 湿度：与温度负相关（40~85%）")
print("- 光照：昼高夜低（0~1200 Lux）")
print("- 土壤湿度：缓慢下降，早晨灌溉事件（25~75%）")
print("- 水位：缓慢波动，偶有降雨事件（800~3000）")
print("\n现在刷新你的网页，查看三天的完整趋势！")
