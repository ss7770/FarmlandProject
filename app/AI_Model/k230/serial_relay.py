#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K230 串口网关 —— 方案丙（板端 TRANSPORT="serial"）的 PC 端。

板子那边**不再开任何 socket**：推理结论和现场图全部从 USB 串口（就是那根
Type-C 线、CanMV IDE 用的同一根）以文本帧吐出来；本脚本假装自己是板子，
替它 POST 服务端。**服务端一行都不用改**。

用法：
    cd app/AI_Model/k230
    D:\\software\\Python\\Python311\\python.exe serial_relay.py --list
    D:\\software\\Python\\Python311\\python.exe serial_relay.py --port COM5
    D:\\software\\Python\\Python311\\python.exe serial_relay.py --port COM5 --dry-run
    D:\\software\\Python\\Python311\\python.exe serial_relay.py --replay cap.log

为什么要有（2026-10-01 第十三次上板定论）：
    跑过一次 kpu.run() 之后，板子的网络整体不可用 —— v9.x 死在「读响应」、
    v10/v10.1 死在「建连」（连固件的 `connect success` 都不再打印），
    随后 USB 掉线、板子复位。同一段 connect 代码，0.12 秒前的 [selftest] 刚成功过。
    ⇒ 病根不是某一行，是「推理之后不能用网」。那就把网络从板子上整个拿掉：
      板子只 print，PC 代发。

板端帧格式（见 k230_classify.py 的「方案丙：串口输出」段）：
    @@R <uid> <conf100> <manual> <label>      一条结论
    @@B <uid> <nbytes> <nchunks>              图片开始
    @@D <seq> <base64 块>                     图片分块（按 seq 归位，可乱序/可丢）
    @@E <uid> <sum8>                          图片结束 + 校验（字节和 mod 256）

⚠️ 顺序必须是「结论先、图片后」：服务端 utils/edge_raw.py 会等最多
   EDGE_RAW_UID_WAIT_SEC（默认 6 秒）去找 client_uid 对应的记录。
   本脚本按到达顺序串行处理（收到 @@R 就发结论，收到 @@E 才发图），天然满足。

依赖：pyserial（pip install pyserial）。其余全是标准库。
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

SOURCE_ID = "k230-canmv"
DEFAULT_SERVER = "http://127.0.0.1:5000"
DEFAULT_RAW_HOST = "127.0.0.1"
DEFAULT_RAW_PORT = 5001

# 单行最长容忍（base64 块 512 字符，留足余量）。超长基本是串口乱码，直接丢。
MAX_LINE = 8192
# 图片上限：与服务端 config.EDGE_RAW_MAX_BYTES 一个量级。防脏帧把内存吃光。
MAX_IMAGE = 2_000_000


# ----------------------------------------------------------------------
# 帧解析（纯逻辑，可离线跑 —— 就是 --replay 走的那条路）
# ----------------------------------------------------------------------
class FrameAssembler:
    """把串口行拼成事件。**只认 `@@` 开头的行**。

    其余输出（启动 banner、`[infer] …`、`[shot] …`、异常打印）全部忽略 ——
    所以 IDE 日志混在中间没关系。
    """

    def __init__(self):
        self.img = None            # 正在收的图片帧
        self.stats = {"result": 0, "image": 0, "drop": 0}

    # --- 内部 ---
    def _drop(self, why):
        self.stats["drop"] += 1
        self.img = None
        return [("warn", "图片帧丢弃：%s" % why)]

    def feed(self, line):
        """喂一行（已 strip 掉 \\r\\n）。返回事件列表。

        事件：
            ("result", {"uid","label","conf","manual"})
            ("image",  {"uid","jpeg"})
            ("warn",   "…")            —— 只打印，不影响后续
        """
        line = line.strip()
        if not line.startswith("@@"):
            return []

        # ---- @@R <uid> <conf100> <manual> <label> ----
        if line.startswith("@@R "):
            p = line.split(" ", 4)          # label 放最后：最多切 4 次，标签含空格也不散
            if len(p) < 5:
                return [("warn", "@@R 字段不足：%r" % line[:80])]
            try:
                conf = float(p[2])
                manual = int(p[3])
            except ValueError:
                return [("warn", "@@R 数值解析失败：%r" % line[:80])]
            out = []
            if conf <= 1.0 or conf > 100.0:
                # 服务端口径是 0~100 百分数，且**明确拒收 <=1.0**（板上二次 softmax
                # 把 90% 压成 6%、或忘了 ×100，都长这样）。这里**只告警、照转发** ——
                # 权威判定在服务端（它拒了会回 400，日志里看得见），relay 不擅自丢数据。
                out.append(("warn", "@@R 置信度 %.4f 不在 0~100（板上漏乘 100？"
                                    "服务端会按非法拒收）：%r" % (conf, line[:80])))
            self.stats["result"] += 1
            out.append(("result", {"uid": p[1], "conf": conf,
                                   "manual": bool(manual), "label": p[4]}))
            return out

        # ---- @@B <uid> <nbytes> <nchunks> ----
        if line.startswith("@@B "):
            p = line.split()
            if len(p) != 4:
                return [("warn", "@@B 字段数不对：%r" % line[:80])]
            try:
                nbytes = int(p[2])
                nchunks = int(p[3])
            except ValueError:
                return [("warn", "@@B 数值解析失败：%r" % line[:80])]
            if nbytes <= 0 or nbytes > MAX_IMAGE or nchunks <= 0 or nchunks > 100000:
                return [("warn", "@@B 长度异常 nbytes=%d nchunks=%d" % (nbytes, nchunks))]
            if self.img is not None:
                # 上一张没收完（板子复位 / 丢帧）—— 直接换成新的，不报错
                self.stats["drop"] += 1
            self.img = {"uid": p[1], "nbytes": nbytes, "nchunks": nchunks,
                        "chunks": {}}
            return []

        # ---- @@D <seq> <base64> ----
        if line.startswith("@@D "):
            if self.img is None:
                return [("warn", "@@D 出现在 @@B 之前，忽略")]
            p = line.split(" ", 2)
            if len(p) < 3:
                return [("warn", "@@D 字段不足：%r" % line[:80])]
            try:
                seq = int(p[1])
            except ValueError:
                return [("warn", "@@D seq 不是数字：%r" % line[:80])]
            self.img["chunks"][seq] = p[2].strip()
            return []

        # ---- @@E <uid> <sum8> ----
        if line.startswith("@@E "):
            if self.img is None:
                return [("warn", "@@E 出现在 @@B 之前，忽略")]
            p = line.split()
            img, self.img = self.img, None
            if len(p) < 3:
                return self._drop("@@E 字段不足：%r" % line[:80])
            uid = p[1]
            try:
                sum8 = int(p[2]) & 0xFF
            except ValueError:
                sum8 = -1
            if uid != img["uid"]:
                return self._drop("uid 不匹配（开头 %s / 结尾 %s）" % (img["uid"], uid))
            if len(img["chunks"]) != img["nchunks"]:
                return self._drop("只收到 %d/%d 块" % (len(img["chunks"]), img["nchunks"]))
            try:
                b64 = "".join(img["chunks"][i] for i in range(img["nchunks"]))
            except KeyError:
                return self._drop("块序号不连续（缺块，多半是串口丢行）")
            try:
                jpeg = base64.b64decode(b64, validate=True)
            except Exception as e:
                return self._drop("base64 解码失败：%s" % e)
            if len(jpeg) != img["nbytes"]:
                return self._drop("解码后 %d 字节 ≠ 声明 %d" % (len(jpeg), img["nbytes"]))
            if not jpeg.startswith(b"\xff\xd8"):
                return self._drop("内容不是 JPEG")
            if sum8 >= 0 and (sum(jpeg) & 0xFF) != sum8:
                return self._drop("校验和不对（%d ≠ %d）"
                                  % (sum(jpeg) & 0xFF, sum8))
            self.stats["image"] += 1
            return [("image", {"uid": uid, "jpeg": jpeg})]

        return []


# ----------------------------------------------------------------------
# 转发（板子的替身）
# ----------------------------------------------------------------------
class Sender:
    """把事件转发给服务端：结论走 POST，图片走原始 TCP（同一套协议，服务端不用改）。"""

    def __init__(self, server=DEFAULT_SERVER, raw_host=DEFAULT_RAW_HOST,
                 raw_port=DEFAULT_RAW_PORT, source=SOURCE_ID,
                 timeout=10.0, dry_run=False):
        self.server = server.rstrip("/")
        self.raw_host = raw_host
        self.raw_port = raw_port
        self.source = source
        self.timeout = timeout
        self.dry_run = dry_run
        self.ok_uids = set()          # 结论真正入库过的 uid（图片只给这些发）
        self.n_ok = 0
        self.n_img = 0
        self.n_fail = 0

    # --- 结论 ---
    def post_result(self, ev):
        uid = ev["uid"]
        body = json.dumps({
            "disease": ev["label"],
            "confidence": round(ev["conf"], 1),
            "source": self.source,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "manual": ev["manual"],
            "client_uid": uid,
        }).encode("utf-8")

        if self.dry_run:
            print("[relay][dry] 会 POST /api/edge/disease  %s" % body.decode("utf-8"))
            self.ok_uids.add(uid)
            return True

        url = self.server + "/api/edge/disease"
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "ignore")
            data = json.loads(raw or "{}")
            self.ok_uids.add(uid)
            self.n_ok += 1
            print("[relay] 结论入库 uid=%s  %s %.1f%%  verdict=%s record_id=%s"
                  % (uid, ev["label"], ev["conf"],
                     data.get("verdict"), data.get("record_id")))
            return True
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            self.n_fail += 1
            print("[relay] 结论被拒 HTTP %d：%s" % (e.code, detail))
            return False
        except Exception as e:
            self.n_fail += 1
            print("[relay] 结论上报失败（服务端没起？）：%s: %s"
                  % (type(e).__name__, e))
            return False

    # --- 图片 ---
    def push_image(self, ev):
        uid = ev["uid"]
        jpeg = ev["jpeg"]
        if uid not in self.ok_uids:
            print("[relay] uid=%s 的结论没入库，图片不发（服务端按 uid 找不到记录）" % uid)
            return False
        self.ok_uids.discard(uid)

        if self.dry_run:
            print("[relay][dry] 会 TCP %s:%d  发 EIMG1 %s %d（%d 字节）"
                  % (self.raw_host, self.raw_port, uid, len(jpeg), len(jpeg)))
            return True

        try:
            s = socket.create_connection((self.raw_host, self.raw_port),
                                         timeout=self.timeout)
        except Exception as e:
            self.n_fail += 1
            print("[relay] 连原始图片端口 %s:%d 失败：%s: %s"
                  % (self.raw_host, self.raw_port, type(e).__name__, e))
            return False
        try:
            s.sendall(("EIMG1 %s %d %s\n" % (uid, len(jpeg), self.source)).encode())
            s.sendall(jpeg)
            self.n_img += 1
            print("[relay] 图片已推 uid=%s（%d 字节）" % (uid, len(jpeg)))
            return True
        except Exception as e:
            self.n_fail += 1
            print("[relay] 推图失败：%s: %s" % (type(e).__name__, e))
            return False
        finally:
            try:
                s.close()
            except Exception:
                pass


# ----------------------------------------------------------------------
# 主循环
# ----------------------------------------------------------------------
def pump_line(asm, sender, line, quiet=False):
    """一行 -> 若干事件 -> 转发。抽出来是为了 --replay 能复用同一条逻辑。"""
    for kind, payload in asm.feed(line):
        if kind == "result":
            sender.post_result(payload)
        elif kind == "image":
            sender.push_image(payload)
        elif kind == "warn" and not quiet:
            print("[relay] ⚠️ %s" % payload)


def run_serial(args, asm, sender):
    import serial                                   # 只在这里 import，--replay 不需要
    from serial.tools import list_ports

    port = args.port
    if not port:
        cands = [p.device for p in list_ports.comports()]
        if len(cands) == 1:
            port = cands[0]
            print("[relay] 只有一个串口，自动选中 %s" % port)
        else:
            print("[relay] 请用 --port 指定串口。当前可用：")
            for p in list_ports.comports():
                print("   %-10s %s" % (p.device, p.description))
            return 2

    ser = serial.Serial(port, args.baud, timeout=0.2)
    try:
        ser.dtr = False        # 别因为打开串口把板子顶复位
    except Exception:
        pass
    print("[relay] 已打开 %s @ %s（USB-CDC 实际不看波特率）" % (port, args.baud))
    print("[relay] 服务端 %s ；图片端口 %s:%d ；dry_run=%s"
          % (sender.server, sender.raw_host, sender.raw_port, args.dry_run))
    print("[relay] 等板子启动……（Ctrl+C 退出）")

    buf = b""
    n_lines = 0
    last_report = time.time()
    while True:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk
            if len(buf) > MAX_LINE and b"\n" not in buf:
                print("[relay] ⚠️ 单行超长（%d 字节）无换行，清空缓冲" % len(buf))
                buf = b""
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                n_lines += 1
                pump_line(asm, sender, line.decode("utf-8", "ignore"))
        elif buf:
            # 串口静默但缓冲里还有半行 —— 先留着，等下一段补齐
            pass

        if args.stats and time.time() - last_report >= 30:
            last_report = time.time()
            print("[relay] 心跳：行=%d 结论=%d 图=%d 丢帧=%d 失败=%d"
                  % (n_lines, asm.stats["result"], asm.stats["image"],
                     asm.stats["drop"], sender.n_fail))


def run_replay(args, asm, sender):
    """离线回放一段抓来的串口日志（没有板子也能验解析/转发逻辑）。"""
    print("[relay] 回放 %s" % args.replay)
    with open(args.replay, "rb") as f:
        for raw in f:
            pump_line(asm, sender, raw.decode("utf-8", "ignore"))
    print("[relay] 回放结束：结论=%d 图=%d 丢帧=%d"
          % (asm.stats["result"], asm.stats["image"], asm.stats["drop"]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="K230 串口网关（方案丙）：板子只 print，本脚本替它发服务端")
    ap.add_argument("--port", help="串口号，如 COM5 / /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200,
                    help="波特率（USB-CDC 通常忽略，默认 115200）")
    ap.add_argument("--server", default=DEFAULT_SERVER,
                    help="Flask 根地址（默认 %s）" % DEFAULT_SERVER)
    ap.add_argument("--raw-host", default=DEFAULT_RAW_HOST, help="图片端口主机")
    ap.add_argument("--raw-port", type=int, default=DEFAULT_RAW_PORT, help="图片端口")
    ap.add_argument("--dry-run", action="store_true",
                    help="只解析并打印，不真的发（先用它确认帧对不对）")
    ap.add_argument("--replay", help="回放一个串口日志文件，不开串口")
    ap.add_argument("--stats", action="store_true", help="每 30 秒打一次心跳统计")
    ap.add_argument("--list", action="store_true", help="列出本机串口后退出")
    args = ap.parse_args(argv)

    if args.list:
        try:
            from serial.tools import list_ports
        except ImportError:
            print("没装 pyserial：pip install pyserial")
            return 2
        ports = list(list_ports.comports())
        if not ports:
            print("没找到任何串口。板子插了吗？驱动装了吗？")
        for p in ports:
            print("  %-10s %s  [%04x:%04x]" % (p.device, p.description,
                                               p.vid or 0, p.pid or 0))
        return 0

    asm = FrameAssembler()
    sender = Sender(server=args.server, raw_host=args.raw_host,
                    raw_port=args.raw_port, dry_run=args.dry_run)

    if args.replay:
        return run_replay(args, asm, sender)
    try:
        return run_serial(args, asm, sender)
    except KeyboardInterrupt:
        print("\n[relay] 已停止。累计：结论 %d / 图 %d / 失败 %d"
              % (sender.n_ok, sender.n_img, sender.n_fail))
        return 0


if __name__ == "__main__":
    sys.exit(main())
