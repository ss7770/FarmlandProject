# -*- coding: utf-8 -*-
"""K230 卡死定位探针（2026-10-01）

背景：`k230_classify.py` 上板跑到「第 1 轮自动巡检」后卡死、看门狗复位。
      查库发现**结论其实已经入库**（record 67 / 72，与日志里的百分比分毫不差），
      所以卡的是**读响应**那一段 —— 不在发送、也不在推理：

      第一轮（display_mode 填错）：卡在 A4 `pl.get_frame()`，已修（改 "virt"）。
      第二轮（本轮的 27.4%）：推理已能连续出结果，卡在 `[report] POST 上报中…` 之后。
        **根因：urequests 的 `timeout=` 在 CanMV 上不生效，而且它读响应体用的是
        `sock.read()`（无参 = 一直读到 EOF）** —— 两次卡死都是这个机制。
        由此也解释了 63~72 那批记录 `image_path` **全空**：每次都卡在结论的响应读取上，
        压根没跑到抓拍那一步（不是抓拍坏了）。

      本脚本 B/C 段因此**改用"只读响应头"的自实现 HTTP**（与正式脚本 v6 同一套），
      不再依赖 urequests。

这个脚本就是把可疑的两段单独拎出来，**每一步先打印再执行**：
      日志停在哪一行 = 卡在哪一行。不用再猜。

用法：
    1. 拷到板子 /sdcard/（和 model.kmodel / labels.txt 放一起）
    2. CanMV IDE 里改下面的 PROBE 开关
    3. 运行，把完整日志贴出来

    PROBE = "net"      **默认**：D 文件自检 + B 网络往返 + E 数据到没到
                      —— 全程**不碰相机**，一定能跑到底
    PROBE = "shot"    只测抓拍链路（PipeLine → snapshot → 转 RGB → 编码 → base64）
    PROBE = "sock"    只测最底层 socket 分层（含 **C8 四条**：setblocking 会不会被 sleep 冲掉）
    PROBE = "arrival" 只测"数据到底有没有到板端"（POST 后先睡 2 秒再收）

⚠️⚠️ 2026-10-02 起 `PROBE="shot"` 这一路**已废**（`k230_probe.py:391+`）：
      它测的抓拍链路 = `snapshot(CHN0) → to_rgb888(x_scale=…) → 编码`，
      而这条路的 `to_rgb888` 那一步会让**板子直接掉电**（P9/P10 实测两次，无回溯）。
      A6/A7 的判读表（本文件 957 行附近）随之作废。
      —— 现行抓拍路径见 `k230_classify.py::_grab_shot_raw()`（零转换，直接编码），
         验证它的探针是 `probes/p10_shot_paths.py` / `probes/p12_shot_channel.py`。
      本文件仍**可用**的部分：`net` / `sock` / `arrival` 三段（不碰相机）。
    PROBE = "files"   只做板上脚本自检（确认是不是最新版 v10.0）
    PROBE = "full"    以上全测，**相机放最后**（万一卡在 A4，前面的结论已经拿到了）

【第九次上板（2026-10-01 18:0x）定论：**v9 读通了，但"不设超时"是没有退路**】
    ✅ v9 上板**第一次读通了响应**（同一轮里连续四次，九轮里头一回）：
        [http] recv #1 -> 161B，累计 161B，含空行=True      ← 结论 POST（429 throttled）
        [http] recv #1 -> 137B，累计 137B，含空行=True      ← 本脚本 B2 GET /command
        [http] recv #1 -> 186B，累计 186B，含空行=True      ← B5 POST（record_id=112）
        [http] recv #1 -> 139B，累计 139B，含空行=True      ← B6 GET /last-record
      ⇒ **"不 settimeout"这条是对的**（第八轮的判定成立），settimeout 确实不该碰。
    ❌ 但代价：同一次上板里**第 2 次 POST 永久卡死**（日志停在
      "先睡 0.35s 再阻塞读响应头..." 之后再无一行）。v9 用的是**阻塞 recv**，
      "数据不来"就是无限等；而这块板子上"数据不来"是真实存在的 ——
      同一次 probe 里 B3 / B4 都是：
          connect to server faild!          ← 固件打印
          OSError: [Errno 107] ENOTCONN（12.00s / 13.00s 才失败）
      ⇒ **WiFi 链路会偶发抖动** ⇒ 纯阻塞读法迟早卡死，且必须人工断电。
    ⇒ v9.1 读法（本探针与正式脚本 `k230_classify.py` 共用同一套）：
        ① 仍然**绝不 settimeout / 不用 uselect**（poll 那条路是坏的）；
        ② 改用 `setblocking(False)` + 轮询 ⇒ recv 没数据时**立刻返回**，结构上不会挂死；
        ③ **每次 recv 之前都重设一次非阻塞** —— v8.1 正是"setblocking → sleep(0.25) → recv"
           卡死的，高度怀疑**这块固件的 `setblocking` 会被 `time.sleep()` 冲掉**；
           把重设放到紧邻 recv 的位置（中间不夹 sleep）就把这条路堵死了；
        ④ 空返回**不当 EOF**：只有"已经读到过字节"之后再连续空 3 次，才算响应发完；
        ⑤ 用 `ticks_ms` 自己数**时间预算**（`HEAD_BUDGET` 等），到点打印
           "已收 N 字节"正常返回 —— 这一轮废了，但板子活着，下一轮照常。
    ⚠️ 本脚本的 **C8** 专测 ③ 那个假设（setblocking 会不会被 sleep 冲掉）。
       v9.3 起扩成**四条**，且**最可能挂的 C8b 放最后**（挂住也不丢前面的结论）：
           C8a  setblocking → recv                     （v8 姿势，无 sleep）
           C8d  sleep → setblocking → recv             （★ v9.2 `_read_head` 的真实顺序）
           C8c  setblocking → sleep → 重设 → recv      （v9.2 规则③）
           C8b  setblocking → sleep → recv             （v8.1 姿势，怀疑会挂 → 放最后）

【第十次上板（2026-10-01 18:0x）定论：**v9.1 全灭的根因是计时，不是 socket**】
    现象：v9.1 下 6 个请求**全部**报同一句，而且都发生在"发完只过了 0.3s"的时候：
        [http] 读头超时：已收 0B，空转 0 次（预算 4.0s 用尽）
    判读："空转 0 次" ⇒ 循环在**第一次判断**就 break 了 ⇒ **一次 recv 都没执行过**
        （所以 setblocking / settimeout 压根没机会表现，也就读不到任何字节）。
    根因：预算计时用的是 `time.time()`。板上它返回 RTC epoch ≈ 276111872（**秒**），
        这个量级下浮点分辨率约 32 秒 ⇒ `time.time() + 4.0` 被**抹平成 `time.time()` 本身**
        ⇒ `time.time() >= deadline` 第一次判断就成立 ⇒ 立刻"预算用尽"。
    ⇒ v9.2：预算 / 排程一律 `ticks_ms` + `ticks_diff`；`time.time()` 只留给 `_BOOT_TAG`。
    另：C8 这次没跑（跑的是 net）。现在也**不必**急着跑了 —— v9.1 根本没执行到 recv，
        "setblocking 被 sleep 冲掉"既没被证实也没被证伪，但它不是这次的卡点。

【第十一次上板（2026-10-01 18:1x）定论：**计时修复生效，但主脚本换了个地方挂**】
    ✅ probe（v9.2）B2~B6 **五条全通**，全部 `recv #1 ...（空转 0 次）`：
           B2 GET /command  130B 200   B3 GET /ping?size=700  116B 200
           B4 POST 不带 brief  865B 200   B5 POST 带 brief  161B **429（预期节流）**
           B6 GET /last-record  139B 200（record_id=120）
       ⇒ `ticks_ms` 这条修复是对的，干净环境（不碰相机）下读头毫无问题。
    ❌ 但正式脚本 `k230_classify.py`（banner 显示 v9.2 无误）**又挂**，而且这次
       **USB 直接断开（"叮咚"）**：
           [http] 已发出 301B（已带 brief 头），先睡 0.35s 再非阻塞读响应头...
           ← 之后再无一行，随后掉线
       注意：第九轮 v9 纯阻塞卡死时**没有**掉线 ⇒ 这次很可能是**固件崩了/复位了**。
    ⚠️ 为什么"说不通"（正是要钉的点）：
       ① `_read_head` 在 report 里是 `verbose=True` 调的，成功必打 `recv #1 -> N B`、
          失败必打 `读头超时：已收 0B，空转 N 次` —— **两条都没有** ⇒ 不是"预算用尽"，
          而是**卡在 recv 里出不来**；
       ② 同一次运行里，**前面那次 `poll_command` 的读头是静默成功的**
          （v9.1 会打「读头超时 + 响应头不完整」，v9.2 这两行都没出现）
          ⇒ 同一份代码、同一个进程里 recv 一会儿有效一会儿挂死；
       ③ probe 用**同一套 `_read_head`** 5 次全通。
       ⇒ 唯一没验过的差异变量是：**相机 + KPU + 一轮推理在场**（probe 全程不碰相机）。
         所以正式脚本 v9.3 加了一段 `SELFTEST_SOCK`：**在相机/KPU 就绪之后、主循环之前**
         跑一次 C8d（sleep → setblocking → recv，故意不发请求）—— 这是唯一能解释矛盾的实验。

注意：
    - "net" 会在库里留一条记录（默认 confidence=50，未达确诊线 → 会被 TTL 自动清理）。
      不想留就把 NET_WRITE_RECORD 改成 False（那也就测不到 POST 了）。
    - 这个脚本**不加载 kmodel、不跑推理**，所以很快；它只回答"卡在哪一步"。
    - 全程不用 try 包住关键调用 —— 要的就是让异常/卡顿原样暴露出来。
    - ⚠️ 时间戳用 `time.ticks_ms()`（单调时钟）：第九次上板发现
      `time.time()` 会在 **WiFi/DHCP 之后跳变**（B2 之后突然变成 276111872.0s），
      因为 RTC 是连上网才被同步的。用 ticks 就不会再出现那种"时间倒流/暴涨"的假象。
"""
import time
import gc

import network

# ==================================================================
# 配置
# ==================================================================
PROBE = "net"                  # "files" / "net" / "sock" / "arrival" / "shot" / "full"

WIFI_SSID = "ka"
WIFI_PASS = "kaito7777"

BASE_URL = "http://192.168.57.97:5000/api/edge"
SERVER_URL = BASE_URL + "/disease"
IMAGE_URL = BASE_URL + "/image"
CMD_URL = BASE_URL + "/command"
PING_URL = BASE_URL + "/ping"                 # 仅排查用：可指定响应体大小
LAST_URL = BASE_URL + "/last-record"          # 兜底取 record_id
SOURCE_ID = "k230-probe"

# ---- v10：原始 TCP 图片通道（图片不再走 HTTP，改用单向 TCP 推）----
# 协议见 Python_Backend/utils/edge_raw.py；板端实现是 k230_classify.py::send_image_raw。
# ⚠️ 端口必须与服务端 config.EDGE_RAW_PORT 一致。
_RAW_HP = BASE_URL.split("://", 1)[-1].split("/", 1)[0]
RAW_HOST = _RAW_HP.split(":")[0]
RAW_PORT = 5001

# ⚠️⚠️ 显示模式 —— 这是"卡在 A4 get_frame()"的头号嫌疑，务必看清：
#     "virt" = IDE 缓冲区显示（**没接任何物理屏时就用这个**，画面显示在 CanMV IDE 里）
#     "lcd"  = MIPI 小屏（ST7701），得真的插着屏
#     "hdmi" = HDMI 显示器（LT9611），得真的接显示器
#     **没有 "none" 这个值**（以前的注释写错了，会走到未定义分支）。
#
#   为什么填错就卡死（不是报错，是**无限等待**）：
#     PipeLine.create() 把 sensor 的 **CHN0（display_size，默认 1920x1080 YUV420SP）
#     绑定到显示层**。显示层没有真实硬件/没人消费时，CHN0 的帧吐不出去，
#     整个 ISP→DMA 流水线就停在那儿，**CHN2（给 AI 的那路）跟着也拿不到帧**，
#     于是 get_frame()（内部就是 sensor.snapshot(chn=CAM_CHN_ID_2)）永远不返回。
#     而 create() 只是"配置"，所以它总是秒过 —— 这就是"A3 顺利、A4 卡死"的原因。
DISPLAY_MODE = "virt"
IMG_SIZE = 224

# CHN0 的分辨率（既给显示、也是抓拍原图的来源）。None = 用 PipeLine 默认（1920x1080）。
# 若换了 "virt" 后 get_frame() 还是卡、或报内存不足，改成 [640,480] 再试一次：
# 这样 CHN0 只输出 640x480，抓拍原图更小、buffer 压力低得多（我们并不需要 1080p 预览）。
DISPLAY_SIZE = None

SHOT_SCALE = 0.5               # 抓拍缩放（和正式脚本第一档一致）
SHOT_QUALITY = 60              # JPEG 质量

NET_WRITE_RECORD = True        # net 段是否真的 POST 一条记录

# ==================================================================
# 工具
# ==================================================================
# ⚠️ 时间戳用**单调时钟**（ticks_ms），不用 time.time()：
#    第九次上板发现 `time.time()` 会在 WiFi/DHCP 之后跳变 —— B2 之前还是 [4.0s]，
#    B2 之后突然变成 [276111872.0s]，因为 RTC 是连上网才被同步的。
#    这会让人误以为"某一步跑了 8 年"，把判读带偏。
try:
    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
except AttributeError:                 # PC 端 CPython 没有 ticks_ms
    def _ticks_ms():
        return int(time.time() * 1000)

    def _ticks_diff(a, b):
        return a - b

_T0 = _ticks_ms()


def _elapsed():
    """从脚本启动到现在的秒数（单调，不受 RTC 同步影响）。"""
    return _ticks_diff(_ticks_ms(), _T0) / 1000.0


def step(msg):
    """先打印再执行：卡死时这一行就是"最后遗言"。"""
    print("[%8.1fs] %s" % (_elapsed(), msg))


def mark(msg):
    print("[%8.1fs]   -> %s" % (_elapsed(), msg))


def head(title):
    print("\n" + "=" * 46)
    print("  " + title)
    print("=" * 46)


# ==================================================================
# 极简 HTTP 客户端（与正式脚本 k230_classify.py 同一套实现）
# ==================================================================
# ⚠️ 探针**故意不用 urequests** —— 它正是头号怀疑对象：
#    ① `timeout=` 在 CanMV 上不生效（记录已入库、板子却卡死，异常都不抛）；
#    ② 读响应体是 `sock.read()`（无参 = 一直读到 EOF），最容易挂死。
# 这里每一步都 print，并且**只读响应头**（`\r\n\r\n` 有终止符，读到就停）。
def _split_url(url):
    rest = url.split("://", 1)[-1]
    hostport, _, path = rest.partition("/")
    host, _, ps = hostport.partition(":")
    return host, (int(ps) if ps else 80), "/" + path


def _send_all(sock, data):
    try:
        sock.sendall(data)
        return
    except AttributeError:
        pass
    pos = 0
    total = len(data)
    while pos < total:
        sent = sock.send(data[pos:])
        if not sent:
            raise OSError("socket.send 返回 0")
        pos += sent


_NB_MODE = None          # None=未探测 / True=非阻塞可用 / False=setblocking 不可用


def _set_nonblocking(sock):
    """把 socket 切到非阻塞；成功返回 True。

    ⚠️ **每次 recv 之前都要重新调一次**（见文件头与 C8；C8d 就是这条的真实顺序）：
       v8.1 是 "setblocking → `time.sleep(0.25)` → recv" **卡死**的，而 v8 是
       "setblocking → **立刻** recv"（不卡、只是误把空当 EOF）。两者只差中间那个 sleep
       ⇒ 高度怀疑**这块固件的 `setblocking` 会被 `time.sleep()` 冲掉**。
       把重设放在**紧邻 recv** 的位置（中间不夹 sleep）就把这条路堵死了。
    ⚠️ 这里**不是 settimeout**：settimeout 会让 recv 走 uselect.poll，那是六~八轮的根因。
    """
    try:
        sock.setblocking(False)
        return True
    except Exception:
        pass
    try:
        sock.setblocking(0)
        return True
    except Exception:
        pass
    return False


def _read_head(sock, verbose=True, limit=1024, settle=0.35, budget=4.0):
    """先睡 `settle`，再用 **setblocking(False) 轮询**把响应头读完（v9.3）。

    ⚠️⚠️ 这里**绝不能出现 `settimeout` / `uselect`**（六~八轮的根因，见文件头）。
       允许、而且**必须**用的是 `setblocking(False)` —— 少了它就退回"阻塞等待"，
       而这块板子上"响应不来"是真实存在的（第九次上板 B3/B4 的 ENOTCONN 就是证据），
       一卡就得人工断电。

    判读：
        `recv #1 -> 3xx B，含空行=True`        → 正常（settle 睡完一次拿全）
        `recv #N ...（空转 K 次）`             → 响应"边等边到"，轮询兜住了
        `读头超时：已收 0B，空转 N 次`          → 响应压根没来（网络抖动 / 服务端）
        `!! setblocking 不可用`                → 固件连非阻塞都不给（会明说）
    """
    global _NB_MODE
    if settle > 0:
        time.sleep(settle)          # 只等这一下：等的是"服务端已经在写响应"
    # ★★ 预算计时必须用**单调时钟 ticks_ms**，绝不能用 time.time()：
    #    板上 time.time() ~2.7e8 秒，浮点在这个量级分辨率 ~32 秒
    #    ⇒ `time.time() + budget` 被抹平 ⇒ 第一次判断就"用尽"、一次 recv 都没做。
    t0 = _ticks_ms()
    budget_ms = int(budget * 1000)
    buf = b""
    n = 0            # 成功读到数据的次数
    spins = 0        # "没数据"的轮数（= 日志里的"空转"）
    quiet = 0        # 已收到字节之后的连续空次数
    while b"\r\n\r\n" not in buf:
        if _ticks_diff(_ticks_ms(), t0) >= budget_ms:
            mark("  [http] 读头超时：已收 %dB，空转 %d 次（预算 %.1fs 用尽）"
                 % (len(buf), spins, budget))
            break
        nb = _set_nonblocking(sock)          # ★ 每次 recv 之前重设非阻塞
        if _NB_MODE is None:
            _NB_MODE = nb
            if not nb:
                mark("  [http] !! setblocking 不可用 —— 只能退回阻塞 recv（有挂死风险）")
        try:
            piece = sock.recv(limit)
        except OSError:
            spins += 1
            time.sleep(0.05)
            continue
        except Exception as e:
            mark("  [http] recv 异常 %s: %s（已收 %dB）" % (type(e).__name__, e, len(buf)))
            break
        if not piece:
            if buf:
                # 已经读到过内容 ⇒ 这个空更可能是"响应发完了"（真 EOF）
                quiet += 1
                if quiet >= 3:
                    mark("  [http] recv 连续空 %d 次（响应已发完），累计 %dB"
                         % (quiet, len(buf)))
                    break
            spins += 1
            time.sleep(0.05)
            continue
        quiet = 0
        n += 1
        buf += piece
        mark("  [http] recv #%d -> %dB，累计 %dB，含空行=%s（空转 %d 次）"
             % (n, len(piece), len(buf), b"\r\n\r\n" in buf, spins))
        if len(buf) >= 4096:
            break
    return buf


def http(method, url, body_bytes=None, timeout=6, trace=False, brief=True,
         settle=0.35, budget=4.0):
    """**不设任何 socket 超时**：先睡 `settle`，再用 setblocking(False) 轮询读响应头。

    `timeout=` 这个参数名保留只是为了少改调用点，**已经不再传给 socket**
    （v9 起不 settimeout，见文件头）。要调"睡多久 / 最多轮多久"用 `settle=` / `budget=`。

    brief=True 给请求带上 `X-Edge-Brief: 1`。探针特意保留 brief=False 的开关，
    用来跑"和旧版完全一样的请求形态"做对照。

    读不到完整头时返回 **(0, {})**（不抛异常）—— 探针要的是"把事实打印出来"，
    不是中断，所以后面的段还能继续跑。
    """
    try:
        import usocket as socket
    except ImportError:
        import socket

    host, port, path = _split_url(url)
    if body_bytes is None:
        body_bytes = b""
    req = ("%s %s HTTP/1.0\r\nHost: %s:%d\r\nConnection: close\r\n"
           % (method, path, host, port)).encode()
    if body_bytes:
        req += b"Content-Type: application/json\r\n"
        req += ("Content-Length: %d\r\n" % len(body_bytes)).encode()
    if brief:
        req += b"X-Edge-Brief: 1\r\n"
    req += b"\r\n" + body_bytes

    s = socket.socket()
    try:
        if trace:
            step("  [http] socket() + connect（**不 settimeout**，v9.3）")
        s.connect(socket.getaddrinfo(host, port)[0][-1])
        if trace:
            step("  [http] send %d 字节（brief=%s）" % (len(req), brief))
        _send_all(s, req)
        if trace:
            step("  [http] 先睡 %.2fs，再 setblocking(False) 轮询读响应头（预算 %.1fs）"
                 % (settle, budget))
        head_buf = _read_head(s, verbose=trace, settle=settle, budget=budget)
    finally:
        try:
            s.close()
        except Exception:
            pass

    if b"\r\n\r\n" not in head_buf:
        mark("  [http] 响应头不完整（%d 字节）→ 返回 (0, {{}}) 继续跑" % len(head_buf))
        return 0, {}
    lines = head_buf.split(b"\r\n\r\n")[0].split(b"\r\n")
    if not lines or b" " not in lines[0]:
        mark("  [http] 状态行异常: %r" % lines[0][:40])
        return 0, {}
    code = int(lines[0].split(b" ")[1])
    headers = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(b":")
        if k:
            headers[k.strip().lower().decode()] = v.strip().decode()
    if trace:
        mark("  [http] 响应头 %d 字节，HTTP %d" % (len(head_buf), code))
    return code, headers


# ==================================================================
# A. 抓拍链路
# ==================================================================
def probe_shot():
    head("A. 抓拍链路（PipeLine → snapshot → to_rgb888 → 编码 → base64）")

    step("A1 导入 media.sensor ...")
    from media.sensor import CAM_CHN_ID_0
    mark("CAM_CHN_ID_0 = %s" % CAM_CHN_ID_0)

    step("A2 构造 PipeLine(rgb888p_size=[%d,%d], display_mode=%r, display_size=%s) ..."
         % (IMG_SIZE, IMG_SIZE, DISPLAY_MODE, DISPLAY_SIZE))
    from libs.PipeLine import PipeLine
    if DISPLAY_SIZE is None:
        pl = PipeLine(rgb888p_size=[IMG_SIZE, IMG_SIZE], display_mode=DISPLAY_MODE)
    else:
        pl = PipeLine(rgb888p_size=[IMG_SIZE, IMG_SIZE], display_mode=DISPLAY_MODE,
                      display_size=DISPLAY_SIZE)

    step("A3 pl.create() ...  ← 这步只做配置，通常秒过；它过了不等于显示通道是好的")
    pl.create()
    mark("PipeLine 就绪")

    step("A4 pl.get_frame()（AI 通道 CHN2）...")
    print("      ↑ 若这一行之后长时间没有输出，就是它：AI 通道拿不到帧。")
    print("        原因基本只有一个 —— display_mode 与实际显示设备不匹配：")
    print("        没接屏却填 'lcd'/'hdmi' 时，CHN0 绑到一个不存在的显示层上，")
    print("        帧没人消费，整条 sensor 流水线堵住，CHN2 自然永远等不到帧。")
    print("        改用 DISPLAY_MODE='virt'（IDE 缓冲区）后重跑即可；")
    print("        若仍卡，改成 'hdmi' 再试一次（这条以前能跑通），或把 DISPLAY_SIZE 设成 [640,480]。")
    frame = pl.get_frame()
    try:
        mark("frame.shape = %s" % (frame.shape,))
    except Exception as e:
        mark("frame 无 shape（%s）" % e)

    step("A5 pl.sensor.snapshot(chn=CHN0) ...  ← 如果卡在这里：抓拍通道就是罪魁祸首")
    try:
        img = pl.sensor.snapshot(chn=CAM_CHN_ID_0)
    except Exception as e:
        mark("！！snapshot 抛异常: %s: %s" % (type(e).__name__, e))
        img = None
    if img is not None:
        try:
            mark("snapshot OK，%dx%d" % (img.width(), img.height()))
        except Exception as e:
            mark("snapshot 返回了对象但没有 width()/height()（%s），很可能是 ndarray 而不是 image.Image"
                 % type(e).__name__)

    if img is None:
        step("A6/A7 跳过（没拿到抓拍帧）")
        return pl, None

    step("A6 img.to_rgb888(x_scale=%.2f, y_scale=%.2f) ...  ← 如果卡在这里：YUV→RGB 转换有问题"
         % (SHOT_SCALE, SHOT_SCALE))
    rgb = None
    for name in ("to_rgb888", "to_rgb565"):
        fn = getattr(img, name, None)
        if fn is None:
            mark("%s 不存在，跳过" % name)
            continue
        try:
            rgb = fn(x_scale=SHOT_SCALE, y_scale=SHOT_SCALE)
            mark("%s(x_scale=, y_scale=) OK -> %s" % (name, type(rgb).__name__))
            break
        except TypeError as e:
            mark("%s 不接受 x_scale/y_scale（%s），试位置参数" % (name, e))
            try:
                rgb = fn(SHOT_SCALE, SHOT_SCALE)
                mark("%s(scale, scale) OK" % name)
                break
            except Exception as e2:
                mark("%s(scale, scale) 也不行: %s: %s" % (name, type(e2).__name__, e2))
                rgb = None
        except Exception as e:
            mark("%s 抛异常: %s: %s" % (name, type(e).__name__, e))
            rgb = None
    if rgb is None:
        step("A6 失败，改用『先转 RGB 再在 RGB 图上 copy(x_scale)』兜底 ...")
        for name in ("to_rgb888", "to_rgb565"):
            fn = getattr(img, name, None)
            if fn is None:
                continue
            try:
                base = fn()
                mark("%s() 原尺寸 OK" % name)
                cp = getattr(base, "copy", None)
                rgb = cp(x_scale=SHOT_SCALE, y_scale=SHOT_SCALE) if cp else base
                mark("copy(x_scale=) OK -> %s" % type(rgb).__name__)
                break
            except Exception as e:
                mark("%s 兜底失败: %s: %s" % (name, type(e).__name__, e))
                rgb = None

    if rgb is None:
        step("A7 跳过（转 RGB 全失败）")
        return pl, None

    try:
        mark("rgb 尺寸 %dx%d" % (rgb.width(), rgb.height()))
    except Exception:
        pass

    step("A7 编码 JPEG：to_jpeg → compress → save 挨个试（q=%d）..." % SHOT_QUALITY)
    raw = None
    for how in ("to_jpeg", "compress", "save"):
        if not hasattr(rgb, how):
            mark("%s 不存在" % how)
            continue
        try:
            if how == "save":
                path = "/sdcard/_probe_shot.jpg"
                rgb.save(path, quality=SHOT_QUALITY)
                with open(path, "rb") as f:
                    raw = f.read()
                mark("save() -> %s，%d 字节" % (path, len(raw)))
            else:
                out = getattr(rgb, how)(quality=SHOT_QUALITY)
                raw = bytes(out)
                mark("%s() -> %d 字节" % (how, len(raw)))
            break
        except Exception as e:
            mark("%s 失败: %s: %s" % (how, type(e).__name__, e))
            raw = None

    if not raw:
        step("A8 跳过（编码全失败）")
        return pl, None
    if raw[:2] != b'\xff\xd8':
        mark("⚠️ 前两字节是 %s，不是 JPEG 魔数 ffd8 —— 编出来的不是 JPEG" % (raw[:2],))
    else:
        mark("✅ JPEG 魔数正确")

    step("A8 base64 编码 ...")
    try:
        import ubinascii
    except ImportError:
        import binascii as ubinascii
    b64 = ubinascii.b2a_base64(raw).decode().strip()
    mark("base64 %d 字符（正式脚本上限 60000）" % len(b64))

    step("A9 保存调试图到 /sdcard/_probe_shot.jpg 供拔卡肉眼确认（有没有花屏/四份）...")
    try:
        rgb.save("/sdcard/_probe_shot.jpg", quality=SHOT_QUALITY)
        mark("已保存")
    except Exception as e:
        mark("保存失败: %s: %s" % (type(e).__name__, e))

    step("A 段全部走通 ✅  抓拍链路没问题 —— 那卡死就在网络段，接着看 B")
    return pl, b64


# ==================================================================
# B. 网络往返
# ==================================================================
_WIFI_FAILED = []          # 记一次失败次数，避免每段都重连一次把时间全耗在 WiFi 上


def _wifi_up():
    """确保 WiFi 已连（B/C/E 段都用到）。返回 wlan 或 None。

    ⚠️ 两个上板踩过的坑，都写在这里免得白等：
      ① **已经连着就别重连**。重连会先断开、再走一遍 DHCP，慢且容易失败；
         第八次上板 probe 卡在 `run connect failed` 26 秒就是这个原因。
      ② **上一轮卡死之后，WiFi 状态可能是坏的**（板子卡在 socket 系统调用里时
         并没有复位，网卡状态没被清理）。这时重连基本不会成功 ——
         正确做法是**断电重上电**（或按复位键），再跑本脚本。
    """
    wlan = network.WLAN(network.STA_IF)
    if wlan.isconnected():
        mark("已连接 IP=%s（复用现有连接，不重连）" % (wlan.ifconfig()[0],))
        return wlan
    if len(_WIFI_FAILED) >= 2:
        mark("！！已经是第 %d 次 WiFi 连接失败，不再重试" % len(_WIFI_FAILED))
        mark("   ⇒ **请把板子断电重上电（或按复位）后再跑**：")
        mark("     板子卡死过的话，网卡状态是脏的，重连不会好，只会白等。")
        return None

    wlan.active(True)
    for attempt in (1, 2):
        step("WiFi 连接 %s（第 %d 次）..." % (WIFI_SSID, attempt))
        try:
            wlan.connect(WIFI_SSID, WIFI_PASS)
        except Exception as e:
            mark("！！wlan.connect 抛异常: %s: %s" % (type(e).__name__, e))
        t0 = _ticks_ms()
        while not wlan.isconnected():
            if _ticks_diff(_ticks_ms(), t0) / 1000.0 > 15:
                break
            time.sleep(0.5)
        if wlan.isconnected():
            mark("已连接 IP=%s（%.1fs）" % (wlan.ifconfig()[0], _ticks_diff(_ticks_ms(), t0) / 1000.0))
            return wlan
        mark("！！第 %d 次超时（日志里的 `run connect failed` 是固件打的，不是脚本打的）"
             % attempt)
        time.sleep(1)
    _WIFI_FAILED.append(1)
    mark("！！WiFi 连不上 → 后面的网络段全部跳过")
    mark("   ⇒ 先确认 PC 上的热点 'ka' 开着；若刚卡死过，**断电重上电**再跑。")
    return None


def _case(label, method, url, body=None, timeout=6, brief=True, note=None):
    """跑一个请求，把结论打出来。返回 (code, headers)。不会卡死（走非阻塞轮询）。"""
    step(label)
    if note:
        mark(note)
    t0 = _ticks_ms()
    try:
        code, hdrs = http(method, url, body, timeout=timeout, trace=True, brief=brief)
    except Exception as e:
        mark("！！%s: %s（%.2fs）—— 连不上或发不出去" % (type(e).__name__, e, _ticks_diff(_ticks_ms(), t0) / 1000.0))
        return 0, {}
    dt = _ticks_diff(_ticks_ms(), t0) / 1000.0
    if code:
        mark("✔ HTTP %d，%.2fs —— **通了**" % (code, dt))
    else:
        mark("✘ 没读到响应头，%.2fs —— **卡在这一档**（看上面「已收 N 字节/空转 N 次」）" % dt)
    return code, hdrs


def probe_net(b64=None):
    """B. 网络往返 —— 交叉验证 v8 的核心假说：**"等数据"是坏的、"读已缓冲数据"是好的**。

    前四轮结论与它们的下场（**别再走回头路**）：
        第四轮  recv(256) → 1024      ❌ 症状一个字节都没变
        第五轮  响应 933B → 128B      ❌ 症状一个字节都没变（128B 比能通的 159B 还小）
    真正稳定的规律只有一条：**GET 通、POST 卡** —— 差别是"数据到达的时机"。

    判读顺序：
        B2  GET  小响应     基线，**必须通**（不通 = 网络/服务端有问题，先查那个）
        B3  GET  大响应     GET 也能拿大包 ⇒ 与体积无关
        B4  POST 不带 brief  旧版本形态，纯对照
        B5  POST 带 brief    与 B4 唯一差别是响应大小
        B6  GET  last-record 板端拿不到响应头时的兜底通路
        B7  POST 图片（很大）

    ⚠️ 每一条都会打印 `已收 N 字节 / 空转 N 次`：
        空转 N 很大后拿到   → **数据是边等边到的** —— v8 的假说成立
        已收 0B             → 数据压根没到板端（网络/服务端方向）
    """
    head("B. 网络往返（GET/POST × 响应大小 交叉验证）")

    step("B1 WiFi 连接 %s ..." % WIFI_SSID)
    wlan = _wifi_up()
    if wlan is None:
        return None

    import ujson

    # ---- B2 GET 小响应（基线）----
    code, hdrs = _case("B2 基线：GET %s（响应约 159B，历史上每次都通）" % CMD_URL,
                       "GET", CMD_URL, timeout=5,
                       note="（这条不通就别往下看了，先查网络/服务端）")
    mark("X-Edge-Cmd=%r" % (hdrs.get("x-edge-cmd"),))

    # ---- B3 GET 大响应 ----
    _case("B3 ★ GET %s?size=700（**大响应 + GET**）" % PING_URL,
          "GET", PING_URL + "?size=700", timeout=6,
          note="（若也拿不到 ⇒ 与 HTTP 方法无关，纯粹是「大包在等的时候到」）")

    # ---- B4 POST 不带 brief（旧版本形态，纯对照）----
    record_id = None
    if NET_WRITE_RECORD:
        payload = {"disease": "Probe___no_brief", "confidence": 50.0,
                   "source": SOURCE_ID, "manual": True}
        code, hdrs = _case("B4 ★ POST %s（**不带 brief**）← 旧版本的请求形态" % SERVER_URL,
                           "POST", SERVER_URL, ujson.dumps(payload).encode(),
                           timeout=6, brief=False,
                           note="（旧版本就卡在这一步；现在看它是「拿到」还是「已收 0B」）")
        if hdrs.get("x-edge-record-id"):
            record_id = hdrs.get("x-edge-record-id")
    else:
        mark("B4 跳过（NET_WRITE_RECORD=False）")

    # ---- B5 POST 带 brief ----
    if NET_WRITE_RECORD:
        payload = {"disease": "Probe___brief_ok", "confidence": 50.0,
                   "source": SOURCE_ID, "manual": True}
        code, hdrs = _case("B5 ★ POST %s（**带 X-Edge-Brief**）" % SERVER_URL,
                           "POST", SERVER_URL, ujson.dumps(payload).encode(),
                           timeout=6, brief=True)
        mark("响应头：status=%r verdict=%r accepted=%r record_id=%r"
             % (hdrs.get("x-edge-status"), hdrs.get("x-edge-verdict"),
                hdrs.get("x-edge-accepted"), hdrs.get("x-edge-record-id")))
        if not record_id and hdrs.get("x-edge-record-id"):
            record_id = hdrs.get("x-edge-record-id")
    else:
        mark("B5 跳过（NET_WRITE_RECORD=False）")

    # ---- B6 GET 兜底取 record_id ----
    code, hdrs = _case("B6 GET %s?source=..&after=0（兜底取 record_id）" % LAST_URL,
                       "GET", "%s?source=%s&after=0" % (LAST_URL, SOURCE_ID), timeout=5)
    mark("X-Edge-Record-Id=%r" % (hdrs.get("x-edge-record-id"),))

    # ---- B7 POST 图片 ----
    if record_id and b64:
        _case("B7 POST %s（%d 字节 base64）" % (IMAGE_URL, len(b64)),
              "POST", IMAGE_URL,
              ujson.dumps({"record_id": record_id, "image": b64,
                           "source": SOURCE_ID}).encode(),
              timeout=20)
    else:
        mark("B7 跳过（没有 record_id 或没有图；单跑 PROBE='shot' 会带图）")

    # ---- B8 ★ v10 原始 TCP 图片端口：能不能连上 ----
    # 为什么要单独测：v10 起图片**不走 HTTP 了**，改推 RAW_PORT（默认 5001）。
    #   这个端口连不上 = 图片永远传不上去，而 HTTP 那一侧全通、看起来一切正常
    #   （板端 send_image_raw 只打印一行失败，很容易被忽略）。
    # 副作用：**零**。故意发一个 magic 不对的头行，服务端会打印"协议头不对"并丢弃；
    #   既不会建记录、也不会落文件（测试用 uid 根本不存在）。
    step("B8 ★ 原始 TCP 图片端口 %s:%d（v10 图片主通道）" % (RAW_HOST, RAW_PORT))
    s8 = None
    t8 = _ticks_ms()
    try:
        s8 = socket.socket()
        s8.connect(socket.getaddrinfo(RAW_HOST, RAW_PORT)[0][-1])
        mark("✔ 已连接（%.2fs）—— 端口是通的" % (_ticks_diff(_ticks_ms(), t8) / 1000.0))
        s8.sendall(b"PING1 probe 0 %s\n" % SOURCE_ID.encode())
        mark("✔ 已发探测头行（服务端会记为「协议头不对」并丢弃，无副作用）")
        mark("★ 这一步通了 ⇒ 板端 send_image_raw() 也能把图推上去")
    except Exception as e:
        mark("✘ 连不上 %s:%d —— %s: %s（%.2fs）"
             % (RAW_HOST, RAW_PORT, type(e).__name__, e,
                _ticks_diff(_ticks_ms(), t8) / 1000.0))
        mark("  ⇒ 图片会传不上去。检查：① 服务端启动日志有没有 '[raw] 图片原始 TCP 通道已监听'；")
        mark("     ② 防火墙是否放行 %d/端口；③ config.EDGE_RAW_PORT 与板端 RAW_PORT 是否一致。" % RAW_PORT)
    finally:
        if s8 is not None:
            try:
                s8.close()
            except Exception:
                pass

    step("B 段结束")
    return wlan


def probe_data_arrival():
    """E. **数据到底有没有到板端** —— 发完 POST **先睡 2 秒**再看。

    这一段现在就是 v9 的读法本身（先睡 → 阻塞 recv），只是把参数放大到"最宽松"：
        sleep 2 秒（远大于正式脚本的 0.35s，确保响应早就到齐了）
        然后**阻塞 recv(2048)** 一次读走
    判读：
        `✔ 拿到 N 字节` + 开头是 `HTTP/1.1 200`  → 数据到了、也读得出来 ⇒ 网络与服务端都正常
        `recv 返回空`                            → 服务端没回就关了（去服务端侧查）
        卡住不返回                               → 才需要考虑"板端只发不读"那套方案
    """
    head("E. 数据到没到板端：POST 后先睡 2 秒再阻塞读（v9 读法）")

    step("E1 确保 WiFi ...")
    if _wifi_up() is None:
        return

    try:
        import usocket as socket
    except ImportError:
        import socket
    import ujson

    host, port, path = _split_url(SERVER_URL)
    payload = {"disease": "Probe___arrival", "confidence": 50.0,
               "source": SOURCE_ID, "manual": True}
    body_bytes = ujson.dumps(payload).encode()
    req = ("POST %s HTTP/1.0\r\nHost: %s:%d\r\nConnection: close\r\n"
           "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n"
           % (path, host, port, len(body_bytes))).encode() + body_bytes

    s = socket.socket()
    try:
        step("E2 socket() + connect（**不 settimeout**）...")
        s.connect(socket.getaddrinfo(host, port)[0][-1])
        mark("已连接")
        step("E3 发出 %d 字节（**不带 brief**，让服务端回大响应）" % len(req))
        _send_all(s, req)
        mark("已发出")

        step("E4 睡 2 秒，让数据安静地进来（这期间一个字节都不读）...")
        time.sleep(2)
        mark("醒来，现在阻塞 recv(2048) ← 卡住就是它")
        t0 = _ticks_ms()
        d = s.recv(2048)
        if not d:
            mark("recv 返回空（对端已关闭且无数据）—— 服务端没回，去服务端侧查")
        else:
            mark("✔ 拿到 %d 字节（%.2fs）—— **数据是到了的、也读得出来**"
                 % (len(d), _ticks_diff(_ticks_ms(), t0) / 1000.0))
            mark("   开头 64 字节: %r" % d[:64])
    except Exception as e:
        mark("！！E 段异常: %s: %s" % (type(e).__name__, e))
    finally:
        try:
            s.close()
        except Exception:
            pass
    step("E 段结束")


def probe_socket_floor():
    """C. 最底层 socket 分层：把「建连 → 发 → 收 → 关」拆成 6 步，卡哪步说哪步。

    🔑 重点看 **C4：只 recv 1 个字节**。
        连 1 字节都收不到   → 问题在 TCP/网络层（对端没回/路由不通/被丢弃）；
        能收到 1 字节但读不完 → 就是"等到 EOF 才算读完"那种等待的锅（urequests 的老毛病）。
    这段不依赖任何 HTTP 客户端库，用的就是裸 socket，排除一切中间层嫌疑。
    """
    head("C. 最底层 socket 分层（connect → send → recv(1) → 读头 → close)")

    step("C0 确保 WiFi ...")
    if _wifi_up() is None:
        return

    try:
        import usocket as socket
    except ImportError:
        import socket

    host, port, path = _split_url(CMD_URL)

    step("C1 socket()（**不 settimeout** —— 铁律；非阻塞只在\"读\"时用，见 C7/C8）...")
    s = socket.socket()
    mark("已创建")

    step("C2 connect(%s:%d) ...  ← 卡这里 = 网络层不通" % (host, port))
    t0 = _ticks_ms()
    try:
        s.connect(socket.getaddrinfo(host, port)[0][-1])
        mark("已连接（%.2fs）" % (_ticks_diff(_ticks_ms(), t0) / 1000.0))
    except Exception as e:
        mark("！！connect 失败: %s: %s" % (type(e).__name__, e))
        return

    req = ("GET %s HTTP/1.0\r\nHost: %s:%d\r\nConnection: close\r\n\r\n"
           % (path, host, port)).encode()
    step("C3 send %d 字节 ..." % len(req))
    t0 = _ticks_ms()
    try:
        _send_all(s, req)
        mark("已发出（%.2fs）" % (_ticks_diff(_ticks_ms(), t0) / 1000.0))
    except Exception as e:
        mark("！！send 失败: %s: %s" % (type(e).__name__, e))

    step("C4 recv(1) —— **只读 1 个字节**  ← 卡这里 = 数据根本没到板端")
    buf = b""
    t0 = _ticks_ms()
    try:
        one = s.recv(1)
        buf = one
        mark("收到 %d 字节 %r（%.2fs）" % (len(one), one, _ticks_diff(_ticks_ms(), t0) / 1000.0))
    except Exception as e:
        mark("！！recv(1) 异常: %s: %s（%.2fs）"
             % (type(e).__name__, e, _ticks_diff(_ticks_ms(), t0) / 1000.0))
    step("C5 继续读到 \\r\\n\\r\\n（头有终止符，不会像读 EOF 那样等下去）...")
    # ⚠️ 注意 C 段用的是 /api/edge/command，它的响应头只有 ~180 字节 —— 一次 recv
    #    就能读完，所以**这一段历史上一直是通的**，看不出问题。
    #    真正会卡的是 /api/edge/disease 的 **271 字节**响应头（跨 256 边界），
    #    那一路在 B 段（probe_net）里验。别拿 C 段通过就断定结论上报也没事。
    # ⚠️⚠️ v15（2026-10-02）：/api/edge/command **已被删除**（「立即抓拍」下线），
    #    这一段现在会拿到 404 —— 上面的历史结论只对旧版本有效，重跑前先换路径。
    t0 = _ticks_ms()
    try:
        while b"\r\n\r\n" not in buf:
            c = s.recv(1024)
            if not c:
                mark("对端关闭(EOF)，累计 %d 字节" % len(buf))
                break
            buf += c
            if len(buf) > 4096:
                mark("超过 4096 字节仍没读到空行，停")
                break
        mark("累计 %d 字节（%.2fs）" % (len(buf), _ticks_diff(_ticks_ms(), t0) / 1000.0))
        for ln in buf.split(b"\r\n"):
            if ln:
                mark("  | %s" % ln.decode("utf-8", "replace"))
    except Exception as e:
        mark("！！读头异常: %s: %s" % (type(e).__name__, e))

    step("C6 close()（不读 body 就关 —— 正式脚本正是这么做的）...")
    t0 = _ticks_ms()
    try:
        s.close()
        mark("已关闭（%.2fs）" % (_ticks_diff(_ticks_ms(), t0) / 1000.0))
    except Exception as e:
        mark("！！close 异常: %s: %s" % (type(e).__name__, e))

    probe_v9_recipe(socket, host, port)

    step("C 段结束")


def probe_v9_recipe(socket, host, port):
    """C7 用**正式脚本那套读法**（v9.3）跑一次 POST，看能不能整段读完响应头。

    v9.3 读法 = 先睡 `settle` + `setblocking(False)` 轮询 + 每次 recv 前重设 + 时间预算。
    这里故意用 POST + **不带 brief**（历史上最容易卡的那种组合），把"读法"单独拎出来验。

    判读：
        `recv #1 -> N B，含空行=True`   → ✔ 读法成立
        `读头超时：已收 0B`             → 响应压根没来（网络抖动 / 服务端）
        `!! setblocking 不可用`         → 固件不给非阻塞 ⇒ 去看 C8 的结论
    """
    step("C7 ★ v9.3 读法实测：POST（不带 brief）+ 先睡 + setblocking 轮询读头")
    mark("（与正式脚本 `k230_classify.py::_read_head` 同一套实现）")

    import ujson
    payload = {"disease": "Probe___v91_recipe", "confidence": 50.0,
               "source": SOURCE_ID, "manual": True}
    body = ujson.dumps(payload).encode()
    path = "/api/edge/disease"
    req = ("POST %s HTTP/1.0\r\nHost: %s:%d\r\nConnection: close\r\n"
           "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n"
           % (path, host, port, len(body))).encode() + body

    s = None
    try:
        s = socket.socket()
        s.connect(socket.getaddrinfo(host, port)[0][-1])
        _send_all(s, req)
        mark("已发出 %d 字节，交给 _read_head(settle=0.5, budget=4.0)…" % len(req))
        t1 = _ticks_ms()
        d = _read_head(s, verbose=True, settle=0.5, budget=4.0)
        if not d:
            mark("✘ 一个字节都没读到 —— 响应压根没来（网络/服务端方向）")
        elif b"\r\n\r\n" not in d:
            mark("✘ 读到 %d 字节但没有空行 —— 响应不完整" % len(d))
        else:
            mark("✔ 拿到完整响应头 %d 字节（%.2fs）—— **v9.3 读法成立**"
                 % (len(d), _ticks_diff(_ticks_ms(), t1) / 1000.0))
            mark("   状态行: %r" % d.split(b"\r\n")[0])
    except Exception as e:
        mark("！！异常: %s: %s" % (type(e).__name__, e))
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    probe_nb_sanity(socket, host, port)


def _secs(t0):
    """`_ticks_ms()` 起点 → 秒（保留两位）。只为把 C8 的每一步打短一点。"""
    return _ticks_diff(_ticks_ms(), t0) / 1000.0


def probe_nb_sanity(socket, host, port):
    """C8 ★★ `setblocking(False)` 到底有没有用？`sleep()` 会不会把它冲掉？（v9.3：四条）

    为什么必须验（八轮全是"赌"输的）：
        v9.1/v9.2 靠"**每次 recv 之前**重设 setblocking(False)"保证不挂死，而这个假设
        是从两个现象**推**出来的（v8 不卡 / v8.1 卡，只差一个 sleep），从没在板上验过。

    做法：建连接后**故意一个字节都不发** —— 服务端在等请求行，不会回任何数据。
    于是 recv 只有两种结局，一次分清：
        立刻返回 b''（或抛 EAGAIN/OSError）  → ✔ 非阻塞有效
        卡住不返回                            → ✘ 非阻塞无效 ⇒ 现行方案也救不了，得换方案

    四条**互相独立**的连接，每种姿势各一条：

        C8a  setblocking(False) → recv
                = v8 的姿势：设完**立刻**收，中间没有 sleep（历史预期：通过）
        C8d  sleep(0.5) → setblocking(False) → recv
                = **v9.2 `_read_head` 的真实顺序**：先睡 settle、再设非阻塞、再收。
                  ★ 这条最贴近板上正在跑的代码，之前从没单独测过。
        C8c  setblocking(False) → sleep(0.5) → **重设** setblocking(False) → recv
                = v9.2 规则③：收前重设。验"重设能不能把被 sleep 冲掉的状态救回来"。
        C8b  setblocking(False) → sleep(0.5) → recv
                = v8.1 的姿势，**怀疑会挂** ⇒ 故意放最后，万一把板子挂住，
                  前面三条的结论已经全部落地了。

    ⚠️ 本段**故意不用 try 包住 recv**：要的就是让"卡住"原样暴露 —— 日志停在哪一行，
       结论就在哪一行。最后一行永远带有"走到哪一步 + setblocking 的返回值"，把日志发回来即可。
    """
    step("C8 ★★ setblocking(False) 有效性（v9.3：四条，最危险的放最后）")
    mark("（故意不发请求：服务端在等请求行，一个字节都不会回；recv 没数据才测得出真假）")

    CASES = (
        ("C8a", "set_recv",
         "setblocking(False) → recv（v8 姿势：设完立刻收，中间无 sleep）"),
        ("C8d", "sleep_set",
         "sleep(0.5) → setblocking(False) → recv（★ v9.2 _read_head 的真实顺序）"),
        ("C8c", "set_sleep_set",
         "setblocking(False) → sleep(0.5) → 重设 setblocking(False) → recv（v9.2 规则③）"),
        ("C8b", "set_sleep",
         "setblocking(False) → sleep(0.5) → recv（v8.1 姿势，**卡住 = sleep 冲掉了非阻塞**）"),
    )

    for tag, mode, desc in CASES:
        step("%s：%s" % (tag, desc))
        s = None
        t0 = _ticks_ms()
        try:
            s = socket.socket()
            s.connect(socket.getaddrinfo(host, port)[0][-1])
            mark("  已连接（%.2fs）" % _secs(t0))

            ok = True
            if mode == "set_recv":
                ok = _set_nonblocking(s)
                mark("  setblocking(False) -> %s（%.2fs）→ 立刻 recv(16)"
                     "   ← 卡住 = 非阻塞完全无效" % (ok, _secs(t0)))
            elif mode == "sleep_set":
                time.sleep(0.5)
                mark("  已睡 0.5s（%.2fs）" % _secs(t0))
                ok = _set_nonblocking(s)
                mark("  setblocking(False) -> %s → 立刻 recv(16)"
                     "   ← 卡住 = 睡完再设也不行" % ok)
            elif mode == "set_sleep_set":
                ok0 = _set_nonblocking(s)
                mark("  setblocking(False) -> %s（%.2fs）" % (ok0, _secs(t0)))
                time.sleep(0.5)
                mark("  已睡 0.5s（%.2fs）" % _secs(t0))
                ok = _set_nonblocking(s)
                mark("  重设 setblocking(False) -> %s → 立刻 recv(16)"
                     "   ← 通过 = 重设能救回被 sleep 冲掉的状态" % ok)
            else:                                   # set_sleep
                ok = _set_nonblocking(s)
                mark("  setblocking(False) -> %s（%.2fs）" % (ok, _secs(t0)))
                time.sleep(0.5)
                mark("  已睡 0.5s（%.2fs）→ 直接 recv(16)"
                     "   ← **卡住 = sleep 把非阻塞冲掉了**" % _secs(t0))

            if not ok:
                mark("  ！！setblocking(False) 抛异常 —— 这块固件不支持非阻塞")
                continue

            tr = _ticks_ms()
            dd = s.recv(16)
            mark("  ✔ recv 返回 %r（%.2fs）—— **非阻塞有效**（返回空 = 此刻没数据，不是 EOF）"
                 % (dd, _secs(tr)))
        except OSError as e:
            mark("  ✔ 抛 OSError(%s)（%.2fs）—— **非阻塞有效**（EAGAIN 语义）"
                 % (e, _secs(t0)))
        except Exception as e:
            mark("  ！！异常: %s: %s（%.2fs）"
                 % (type(e).__name__, e, _secs(t0)))
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass

    step("C8 结束")


def probe_files():
    """D. 板上脚本自检 —— 确认跑在板子上的到底是不是最新版。

    为什么要有这一段（2026-10-01 第三次上板卡死后加的）：
        第三次日志停在 `[report] POST 上报中…` 的下一行 `connect success`，但
        **仓库里任何 .py 都不含 `connect success` 这句打印**，v6 的 report() 在这个位置
        必定会打印 `[report] 响应头到达：…` 或 `[report] 上报失败：…`，二者必居其一。
        症状"长得像 v6 卡死"，很可能是**板上跑的根本不是 v6**（拷漏/IDE 里跑的是旧缓存）。
        所以每次先自检文件，别在版本问题上空转。

    判据：能读到 `_recv_head` / `_set_nonblocking` / `X-Edge-Brief` 这些 v8 特征串。

    ⚠️ 从 CanMV IDE 直接运行脚本时，文件**不在 /sdcard**（IDE 是把源码直接送进板子内存的），
       所以这里显示"不在 /sdcard"是**正常的**，不代表你跑的是旧版 ——
       那种情况下请以上一屏输出的 **boot 横幅版本号** 为准。
    """
    head("D. 板上脚本自检（是不是最新版）")

    try:
        import os
    except ImportError:
        mark("！！板上没有 os 模块，跳过")
        return

    # v9 独有特征串（v9 起才有的读法；v9.0 及更早一个都不会有）
    V9_MARKS = ("_read_head", "_set_nonblocking", "HEAD_BUDGET", "client_uid", "X-Edge-Brief")
    # 旧版的"指纹"：只要代码里还出现这个**调用**，就说明是 v9.0 或更早
    OLD_CALL = ".settimeout("
    # ★ v10 的标志：结论改成"只发不读"、图片改走原始 TCP（第十二次上板的方向切换）
    V10_MARK = "send_image_raw"
    # ★ v9.3 的标志：主脚本里的「相机/KPU 在场的现场自检」段（第十一次上板新增；
    #   探针自己的源码里也有这个词，所以两个文件都能被正确识别成 v9.3）
    V93_MARK = "SELFTEST_SOCK"
    # ★ v9.2 的标志：预算/排程走**单调时钟**（第十次上板的核心修复；
    #   v9.1 及更早这里是 `time.time() >= deadline`）
    V92_MARK = "_ticks_diff(_ticks_ms(), t0) >= budget_ms"
    # v9.1 的标志（用来区分 v9.0 / v9.1）
    V91_MARK = "HEAD_BUDGET"

    try:
        names = os.listdir("/sdcard")
    except Exception as e:
        mark("！！列 /sdcard 失败: %s: %s" % (type(e).__name__, e))
        names = []
    mark("/sdcard 条目 %d 个" % len(names))
    mark("（从 IDE 直接运行时这里显示「不在」是正常的，以 boot 横幅版本号为准）")

    for n in ("k230_classify.py", "k230_probe.py", "k230_step_test.py",
              "model.kmodel", "labels.txt"):
        p = "/sdcard/" + n
        if names and n not in names:
            mark("×  %-22s 不在 /sdcard（走 IDE 运行则属正常）" % n)
            continue
        try:
            sz = os.stat(p)[6]
        except Exception as e:
            mark("×  %-22s stat 失败: %s" % (n, e))
            continue
        if not n.endswith(".py"):
            mark("·  %-22s %d 字节" % (n, sz))
            continue
        try:
            with open(p) as f:
                src = f.read()
        except Exception as e:
            mark("×  %-22s 读取失败: %s" % (n, e))
            continue
        # 只看代码里有没有那个调用，避免文档里提一句就把新版判成旧版
        code = _strip_comments(src)
        if OLD_CALL in code:
            mark("✘  %-22s %d 字节  **旧版**（代码里还有 %s，请重新拷贝 v10.0）"
                 % (n, sz, OLD_CALL))
            continue
        hits = [m for m in V9_MARKS if m in src]
        if len(hits) >= 3:
            if V10_MARK in src:
                ver = "v10.0"         # ★ 结论只发不读 + 图片原始 TCP（当前版本）
            elif V93_MARK in src:
                ver = "v9.3"          # 带"相机/KPU 现场自检"
            elif V92_MARK in src:
                ver = "v9.2"          # 预算用单调时钟
            elif V91_MARK in src:
                ver = "v9.1"          # 有预算、但用 time.time() 数 → 板上会"0 空转即超时"
            else:
                ver = "v9.0"
            mark("✔  %-22s %d 字节  %s（命中 %s）" % (n, sz, ver, ",".join(hits)))
        else:
            mark("✘  %-22s %d 字节  **旧版**（v9 特征命中 %d/%d，请重新拷贝 v10.0）"
                 % (n, sz, len(hits), len(V9_MARKS)))

    step("D 段结束")


def _strip_comments(src):
    """粗略去掉注释和字符串字面量，只留"代码"——只用来判断某个**调用**在不在代码里。

    为什么需要：新版文件里到处都写着"不 settimeout"这类说明文字（注释、docstring，
    连启动横幅的 print 文案里都有），直接 `'.settimeout(' in src` 会把它们也匹配上，
    反而把新版判成旧版。
    """
    out = []
    in_str = None
    i = 0
    n = len(src)
    while i < n:
        if in_str:                       # 三引号块（docstring）内部：整块跳过
            if src.startswith(in_str, i):
                i += len(in_str)
                in_str = None
                continue
            i += 1
            continue
        if src.startswith('"""', i) or src.startswith("'''", i):
            in_str = src[i:i + 3]
            i += 3
            continue
        if src[i] == '#':                # 行注释
            while i < n and src[i] != '\n':
                i += 1
            continue
        if src[i] in ('"', "'"):         # 单/双引号字符串字面量
            q = src[i]
            i += 1
            while i < n:
                if src[i] == '\\':
                    i += 2
                    continue
                if src[i] == q:
                    i += 1
                    break
                i += 1
            continue
        out.append(src[i])
        i += 1
    return ''.join(out)


# ==================================================================
# 主流程
# ==================================================================
def main():
    print("K230 卡死定位探针  PROBE=%r" % PROBE)
    print("目标服务端 %s" % BASE_URL)
    print("★ v10 起真正的上报链路**不再读响应**（结论只发不读、图片走原始 TCP 单向推）")
    print("  所以本探针读响应那几段（C7/C8）只用于验证「指令轮询」这条仅存的读通道")
    print("读响应方式：**不 settimeout** + 先睡 + setblocking(False) 轮询，预算用 ticks_ms（v9.3）")
    print("  ⇒ settimeout 会让 recv 走坏的 uselect.poll（六~八轮的坑）；")
    print("    而纯阻塞 recv 又会在\"响应不来\"时永久卡死（第九轮的坑）—— 所以走非阻塞轮询")
    print("  ⇒ v9.2：预算**不能**用 time.time()（RTC epoch + 浮点会把 4.0s 抹平成 0s，")
    print("    第一次判断就'用尽'、一次 recv 都没做 —— 第十次上板的全灭就是这个）")
    print("  ⇒ v10 新增 **B8**：直接探原始 TCP 图片端口 %s:%d（通了图才传得上去）"
          % (RAW_HOST, RAW_PORT))
    pl = None
    b64 = None
    try:
        # D 段放最前：万一板上脚本是旧版，后面几段的结论全部不可信，
        # 先把这个前提钉死，省得拿着旧版的日志分析新版的代码。
        if PROBE in ("files", "net", "sock", "arrival", "full"):
            probe_files()
            gc.collect()
        if PROBE in ("net", "full"):
            probe_net(b64)
            gc.collect()
        if PROBE in ("sock", "full"):
            probe_socket_floor()
            gc.collect()
        if PROBE in ("arrival", "full"):
            probe_data_arrival()
            gc.collect()
        # ⚠️ 相机**放最后**：A 段是最可能卡住的一段（A4 靠 display_mode 吃饭），
        #    放最后就保证"万一卡在 A4，前面的结论已经全部拿到了"。
        if PROBE in ("shot", "full"):
            pl, b64 = probe_shot()
            gc.collect()
            if b64 and PROBE == "full":
                step("相机拿到了 base64，补跑一次带图的 B7 ...")
                probe_net(b64)
    except KeyboardInterrupt:
        print("\n[probe] 已手动停止")
    except Exception as e:
        # 不吞异常：把类型和消息打出来，这是探针最想拿到的东西
        print("\n！！！！探针异常终止: %s: %s" % (type(e).__name__, e))
    finally:
        print("\n[probe] 全部结束（共 %.1fs）" % (_elapsed(),))
        if pl is not None:
            step("pl.destroy() 中… ← 卡这里 = 显示通道收尾问题（display_mode 该用 'virt'）")
            try:
                pl.destroy()
                mark("destroy OK")
            except Exception as e:
                mark("destroy 失败: %s: %s" % (type(e).__name__, e))
        gc.collect()


if __name__ == "__main__":
    main()
