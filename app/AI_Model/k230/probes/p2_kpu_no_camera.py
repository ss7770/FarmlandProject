# -*- coding: utf-8 -*-
# 探针 P2：只跑 KPU 推理（用造出来的假输入），**不开相机、不起 PipeLine**，网络还活不活？
# 问题：kpu.run() 本身会不会把网络搞死？把"KPU"和"相机+PipeLine"两个变量拆开。
# 判读：P0/P1 过 + 这里挂  => kpu.run 本身致命（固件级，基本只能走串口）；
#       这里过、P3 挂     => 相机+KPU 组合才触发，继续跑 P4/P5/P6 细分。

import time, network, gc, socket
import nncase_runtime as nn
import ulab.numpy as np

HOST, PORT, PATH = "192.168.57.97", 5000, "/api/edge/ping"
SSID, PASS = "ka", "kaito7777"
KMODEL_PATH = "/sdcard/model.kmodel"
INFER_ROUNDS, ROUNDS, GAP_MS = 3, 10, 2000
TAG = "P2"

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


def top1(row):
    best = 0
    for i in range(1, len(row)):
        if row[i] > row[best]:
            best = i
    return best


print("=" * 52)
print(" 探针 P2：只跑 KPU（假输入，无相机 / 无 PipeLine）+ 网络")
print("=" * 52)
wlan = wifi_connect()

print("[%s] 加载 %s ..." % (TAG, KMODEL_PATH))
kpu = nn.kpu()
try:
    kpu.load_kmodel(KMODEL_PATH)
except Exception:
    with open(KMODEL_PATH, "rb") as f:
        kpu.load_kmodel(f.read())
print("[%s] kpu 就绪（无相机）" % TAG)

# 造一张全 0 的假图（值域 0~1，形状和正式脚本 preprocess 的输出一致）
# ⚠️ 这块固件的 ulab **没有 `np.float32` 属性**（实测 AttributeError）：
#    用 `float` / `np.float` 即可 —— K230 是单精度，等价 float32。
x = np.zeros((1, 224, 224, 3), dtype=np.float)
print("[%s] 假输入 dtype=%s shape=%s itemsize=%s"
      % (TAG, getattr(x, "dtype", "?"), x.shape, getattr(x, "itemsize", "?")))
for i in range(1, INFER_ROUNDS + 1):
    t0 = tms()
    kpu.set_input_tensor(0, nn.from_numpy(x))
    kpu.run()
    row = kpu.get_output_tensor(0).to_numpy()[0]
    print("[%s] 推理 %d/%d ✔ %.2fs top1=类%d (%.1f%%) mem_free=%d"
          % (TAG, i, INFER_ROUNDS, elapsed(t0), top1(row),
             float(row[top1(row)]) * 100.0, gc.mem_free()))
    time.sleep_ms(500)
print("[%s] 推理全部结束，开始网络循环" % TAG)

stat = [0, 0]
t0_all = tms()
for i in range(1, ROUNDS + 1):
    try:
        ok, dt, head = net_round()
    except Exception as e:
        ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
    stat[0] += 1
    stat[1] += 1 if ok else 0
    print("[%s] 第 %02d/%d 轮 %s %.2fs（距最后推理 %.1fs）  %s"
          % (TAG, i, ROUNDS, "✔" if ok else "✘", dt, elapsed(t0_all), head))
    if i < ROUNDS:
        time.sleep_ms(GAP_MS)

print("-" * 52)
print("[%s] 网络通过 = %d/%d" % (TAG, stat[1], stat[0]))
print("[%s] 结论 = %s" % (
    TAG,
    "kpu.run 本身无罪（假输入推理后网络照常），继续跑 P3（相机+KPU 组合）"
    if stat[1] == stat[0]
    else "kpu.run 一跑网络就死（连相机都没开）—— 固件级问题，基本只能走串口"))
