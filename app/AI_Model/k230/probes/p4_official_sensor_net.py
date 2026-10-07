# -*- coding: utf-8 -*-
# 探针 P4：官方 camera.py 的极简采集路径（Sensor + snapshot，**不用 PipeLine**）+ 网络
# 问题：官方采集方式占用的 media 资源和 PipeLine 完全不同（单通道 RGB565 vs
#       CHN0 显示 + CHN2 AI planar）。P1（PipeLine 取帧）若挂而这里活，
#       ⇒ 病灶钉死在 PipeLine 的 media 配置上，正式脚本可考虑改采集路径。
# 判读：与 P1 对照看：P1 挂 + P4 过 = PipeLine 配置的锅；P1、P4 都过 = 采集路径无关。
#       ⚠️ 本探针不含 KPU —— 它只钉"采集路径"这一个变量。

import time, network, gc, socket
from media.sensor import *
from media.media import *
import image

HOST, PORT, PATH = "192.168.57.97", 5000, "/api/edge/ping"
SSID, PASS = "ka", "kaito7777"
SNAP_N, ROUNDS, GAP_MS = 5, 10, 2000
TAG = "P4"

REQ = ("GET %s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n"
       % (PATH, HOST)).encode()


def tms():
    return time.ticks_ms()


def elapsed(t0):
    return time.ticks_diff(tms(), t0) / 1000.0


def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("[%s] wifi 连接 %s ..." % (TAG, SSID))
        wlan.connect(SSID, PASS)
        t0 = tms()
        while not wlan.isconnected():
            if time.ticks_diff(tms(), t0) > 15000:
                raise RuntimeError("wifi 15 秒没连上")
            time.sleep_ms(200)
    print("[%s] wifi OK IP=%s" % (TAG, wlan.ifconfig()[0]))
    return wlan


def _pull(s):
    try:
        return s.recv(256)
    except OSError:
        return b""


def net_round():
    t0 = tms()
    s = socket.socket()
    try:
        s.connect(socket.getaddrinfo(HOST, PORT)[0][-1])
        s.send(REQ)
        time.sleep_ms(150)
        s.setblocking(False)
        buf = b""
        t1 = tms()
        while b"\r\n\r\n" not in buf and time.ticks_diff(tms(), t1) < 4000:
            buf += _pull(s)
            time.sleep_ms(2)
        head = buf.split(b"\r\n", 1)[0][:36]
        return (b" 200 " in head), elapsed(t0), head
    finally:
        try:
            s.close()
        except Exception:
            pass


print("=" * 52)
print(" 探针 P4：官方极简采集（无 PipeLine / 无 KPU）+ 网络")
print("=" * 52)
wlan = wifi_connect()

print("[%s] 官方式 Sensor 初始化（FHD RGB565，单通道）..." % TAG)
sensor = Sensor()
sensor.reset()
sensor.set_framesize(Sensor.FHD)
sensor.set_pixformat(Sensor.RGB565)
MediaManager.init()
sensor.run()
print("[%s] sensor 就绪，snapshot %d 次" % (TAG, SNAP_N))
for i in range(SNAP_N):
    t0 = tms()
    img = sensor.snapshot()
    jb = 0
    try:
        img.compress(quality=60)          # 官方 OpenMV 风格 JPEG 压缩（探针只验证能压）
        jb = img.size()
    except Exception as e:
        print("[%s]   JPEG 压缩不可用（不影响本探针目的）: %s" % (TAG, e))
    print("[%s] snapshot %d/%d ✔ %.2fs %s"
          % (TAG, i + 1, SNAP_N, elapsed(t0), ("jpeg≈%dB" % jb) if jb else ""))
    time.sleep_ms(200)
print("[%s] 采集就绪（sensor 保持活着），开始网络循环" % TAG)

stat = [0, 0]
t0_all = tms()
for i in range(1, ROUNDS + 1):
    try:
        ok, dt, head = net_round()
    except Exception as e:
        ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
    stat[0] += 1
    stat[1] += 1 if ok else 0
    print("[%s] 第 %02d/%d 轮 %s %.2fs（距采集结束 %.1fs）  %s"
          % (TAG, i, ROUNDS, "✔" if ok else "✘", dt, elapsed(t0_all), head))
    if i < ROUNDS:
        time.sleep_ms(GAP_MS)

# 收尾：停掉 sensor / media —— 否则下一个采集类探针会撞 "OSError: sensor(2) is already inited"
try:
    sensor.stop()
    MediaManager.deinit()
    print("[%s] sensor 已停止（下一个探针可以不用按 reset）" % TAG)
except Exception as e:
    print("[%s] sensor 收尾失败（照旧按 reset 再跑下一个）: %s" % (TAG, e))

print("-" * 52)
print("[%s] 网络通过 = %d/%d" % (TAG, stat[1], stat[0]))
print("[%s] 结论 = %s（请与 P1 的结果对照判读）" % (
    TAG,
    "官方采集路径下网络正常" if stat[1] == stat[0]
    else "官方采集路径网络也坏 —— 病灶不在 PipeLine 配置，在 media 层更深处"))
