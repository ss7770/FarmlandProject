# -*- coding: utf-8 -*-
# 探针 P3：完整组合（PipeLine + KPU 真图推理 1 轮）→ 网络每 2 秒探一次、探满 60 秒
# 问题：复现"推理后网络必死"，并精确量出——推理后第几秒死？死一次还是一直死？
# 判读：全程 ✔        => 组合也不死，大脚本死因在别处（回头查 send_round 周边逻辑）；
#       推理后全 ✘    => 与大脚本症状一致，继续跑 P4（官方采集路径）/ P5 / P6；
#       中途板子复位  => 看最后一行打印，那就是崩点，本身就是结论。

import time, network, gc, socket
from libs.PipeLine import PipeLine
import nncase_runtime as nn

HOST, PORT, PATH = "192.168.57.97", 5000, "/api/edge/ping"
SSID, PASS = "ka", "kaito7777"
KMODEL_PATH = "/sdcard/model.kmodel"
WATCH_SEC, GAP_MS = 60, 2000
TAG = "P3"

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


def preprocess(frame):
    """与正式脚本一致的 ulab 套路：(C,H,W) -> (1,H,W,C) float 0~1"""
    sh = frame.shape
    if len(sh) == 4:
        c, h, w = sh[1], sh[2], sh[3]
    else:
        c, h, w = sh[0], sh[1], sh[2]
    x = frame.reshape((c, h * w)).transpose().copy().reshape((h, w, c))
    return x.reshape((1, h, w, c)) / 255.0


def top1(row):
    best = 0
    for i in range(1, len(row)):
        if row[i] > row[best]:
            best = i
    return best


print("=" * 52)
print(" 探针 P3：PipeLine + KPU 真图推理 1 轮 → 网络盯 %d 秒" % WATCH_SEC)
print("=" * 52)
wlan = wifi_connect()

print("[%s] PipeLine 初始化 ..." % TAG)
pl = PipeLine(rgb888p_size=[224, 224], display_mode="virt")
pl.create()
print("[%s] 加载 %s ..." % (TAG, KMODEL_PATH))
kpu = nn.kpu()
try:
    kpu.load_kmodel(KMODEL_PATH)
except Exception:
    with open(KMODEL_PATH, "rb") as f:
        kpu.load_kmodel(f.read())

# 推理 1 轮（真图）
frame = pl.get_frame()
t0 = tms()
kpu.set_input_tensor(0, nn.from_numpy(preprocess(frame)))
kpu.run()
row = kpu.get_output_tensor(0).to_numpy()[0]
print("[%s] 推理 ✔ %.2fs top1=类%d (%.1f%%) mem_free=%d"
      % (TAG, elapsed(t0), top1(row), float(row[top1(row)]) * 100.0, gc.mem_free()))
t_infer = tms()

# 之后每 2 秒探一次网络，盯满 WATCH_SEC
n = 0
while elapsed(t_infer) < WATCH_SEC:
    n += 1
    try:
        ok, dt, head = net_round()
    except Exception as e:
        ok, dt, head = False, -1, ("%s: %s" % (type(e).__name__, e))
    print("[%s] 探测 %02d %s %.2fs（推理后 %.1fs）  %s"
          % (TAG, n, "✔" if ok else "✘", dt, elapsed(t_infer), head))
    time.sleep_ms(GAP_MS)

# 收尾：销毁采集链路 —— 否则下一个采集类探针会撞 "OSError: sensor(2) is already inited"
try:
    pl.destroy()
    print("[%s] PipeLine 已销毁（下一个探针可以不用按 reset）" % TAG)
except Exception as e:
    print("[%s] PipeLine 销毁失败（照旧按 reset 再跑下一个）: %s" % (TAG, e))

print("-" * 52)
print("[%s] 结论 = 盯满 %d 秒完成。把上面每一条 ✔/✘ 连起来看："
      "全 ✔ 组合无罪；全 ✘ 复现『推理后网络死』；有 ✔ 有 ✘ 说明是间歇性的" % (TAG, WATCH_SEC))
