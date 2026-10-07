# -*- coding: utf-8 -*-
"""K230 现场图的**原始 TCP 接收通道**（v10，2026-10-01）

为什么要有这条通道（动它之前先读完）：

    板端 HTTP 那套（`_http` + `_read_head`）走了十几轮都没稳定下来，现象收敛成一句话：
        「**发** 一直没问题，**读响应** 在跑过一次 KPU 推理之后会挂死。」
    证据（v9.3 上板）：
        probe 干净脚本里 net 5/5、sock C1~C8 全通 —— socket 栈本身健康；
        主脚本 `[selftest]` 也通 —— 相机/KPU 在场、但没推理时非阻塞也有效；
        可启动时那次 `/command` 轮询（推理**前**）读响应成功，
        而 `[infer]` 之后 `report()` 的读响应就再也没回来过。
    ⇒ 只要设计里还留着"等一个响应"，就还会挂。**图片这条路最容易挂，因为它还要多等一次。**

    所以图片改成**单向推送**：板端连上来 → 发一段极简头 + 原始 JPEG → 关连接。
    全程**没有 recv**，服务端**一个字节都不回**（回什么都不需要读，因为压根不读）。

协议（刻意做得极简，板端解析成本≈0）：

    EIMG1 <uid> <nbytes> <source>\\n
    <nbytes 个原始 JPEG 字节>

    uid     —— 板端每轮生成的关联 id，与 `/api/edge/disease` 请求体里的 client_uid 相同；
               服务端靠它找到刚入库的那条记录（不需要板端读回 record_id）。
    nbytes  —— 后面 JPEG 的精确长度（原始字节，**不是 base64**）。
               不走 base64 的理由：省 33% 流量、省板端一次 b64 编码（CPU + 一份大字符串）。

为什么不用 HTTP：HTTP 的"用完即关"要求客户端知道什么时候读完；
    而板端读不动。这里改成"长度自己声明、发完即关"，服务端按长度收满就结束 —— 谁都不等谁。

⚠️ 服务端**绝不回包**：板端 `close()` 时接收缓冲区是空的，不会触发 RST，
   也就不会把已经发出去的数据打断（这是"单向"能成立的前提，别好心加一句 ACK）。
"""

import os
import socket
import threading
import time
from datetime import datetime

import config
from utils.db import find_record_id_by_uid, set_record_image

MAGIC = b'EIMG1'
MAX_HEADER = 512                 # 头行长度上限（防脏连接把内存堆爆）
DEFAULT_PORT = 5001


def save_jpeg_bytes(raw, source):
    """把**原始 JPEG 字节**落到 uploads/，返回相对路径（失败返回空串）。

    校验口径与 `blueprints/edge.py::_save_base64_image` 完全一致（那边解码后也走这里），
    保证"HTTP base64 通道"和"原始 TCP 通道"写出来的文件长得一样、看板/APP 都认。
    """
    if not raw or len(raw) < 100 or len(raw) > 8_000_000:
        return ''
    if not raw.startswith(b'\xff\xd8'):          # 不是 JPEG，直接丢
        return ''

    upload_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
    try:
        os.makedirs(upload_dir, exist_ok=True)
    except OSError:
        return ''

    safe_src = ''.join(c for c in (source or '') if c.isalnum() or c in '-_') or 'k230'
    # 带微秒：同一秒内连传两张也不会互相覆盖（HTTP 通道也用这个函数）
    name = 'edge_%s_%s.jpg' % (safe_src, datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    try:
        with open(os.path.join(upload_dir, name), 'wb') as f:
            f.write(raw)
    except OSError:
        return ''
    return 'uploads/' + name


def _remove_upload_file(image_path):
    """删掉刚写盘的图（记录没找到时别留孤儿文件）。失败静默。"""
    try:
        p = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            image_path)
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass


def _recv_exact(conn, n, first=b''):
    """收满 n 个字节；中途对端关闭就返回已收到的（长度不足由调用方判断）。

    `first` 是"读头行时顺带多读进来的那截"，直接算进总数，不再重复收。
    """
    data = bytearray(first)
    while len(data) < n:
        try:
            chunk = conn.recv(min(8192, n - len(data)))
        except (OSError, socket.timeout):
            break
        if not chunk:
            break
        data += chunk
    return bytes(data)


def _handle_conn(conn, addr, max_bytes, uid_wait):
    """处理一条连接：读头行 → 按声明长度收原始 JPEG → 贴到记录上。"""
    conn.settimeout(10.0)          # 服务端（CPython）用超时是安全的，板端才不能用
    try:
        # ① 头行：一直读到 '\n'（板端一次 sendall 发过来，基本一次就够）
        buf = b''
        while b'\n' not in buf:
            chunk = conn.recv(256)
            if not chunk:
                return
            buf += chunk
            if len(buf) > MAX_HEADER:
                print('[raw] 头行超长，丢弃来自 %s 的连接' % (addr[0],))
                return

        line, _, rest = buf.partition(b'\n')
        parts = line.decode('ascii', 'ignore').strip().split()
        if len(parts) < 3 or parts[0] != MAGIC.decode():
            print('[raw] 协议头不对（%r），丢弃' % (line[:80],))
            return
        uid = parts[1][:64]
        try:
            nbytes = int(parts[2])
        except ValueError:
            print('[raw] nbytes 不是数字（%r），丢弃' % (parts[2],))
            return
        source = parts[3] if len(parts) > 3 else 'k230'

        if nbytes <= 0 or nbytes > max_bytes:
            print('[raw] 长度异常 %d（上限 %d），丢弃' % (nbytes, max_bytes))
            return

        # ② 图片本体：按声明长度收满
        t0 = time.time()
        raw = _recv_exact(conn, nbytes, first=rest)
        if len(raw) != nbytes:
            print('[raw] uid=%s 只收到 %d/%d 字节，丢弃' % (uid, len(raw), nbytes))
            return
        if not raw.startswith(b'\xff\xd8'):
            print('[raw] uid=%s 内容不是 JPEG，丢弃' % (uid,))
            return

        # ③ 找记录。板端是"结论先发、图片紧跟着发"，两者相隔通常 <300ms，
        #    但服务端写库要排队，所以这里**等一会儿**再放弃（最坏也只是丢一张图）。
        deadline = time.time() + uid_wait
        rid = None
        while True:
            rid = find_record_id_by_uid(uid)
            if rid:
                break
            if time.time() >= deadline:
                break
            time.sleep(0.3)

        if not rid:
            print('[raw] uid=%s 在 %.1fs 内没找到记录（多半被去重/节流挡了），丢弃这张图（%d 字节）'
                  % (uid, uid_wait, nbytes))
            return

        path = save_jpeg_bytes(raw, source)
        if not path:
            print('[raw] uid=%s 写盘失败，丢弃' % (uid,))
            return
        if not set_record_image(rid, path):
            _remove_upload_file(path)
            print('[raw] uid=%s 记录 %s 已不存在，删掉孤儿图' % (uid, rid))
            return

        print('[raw] 补图 record_id=%s <- uid=%s（%d 字节，%.2fs）'
              % (rid, uid, nbytes, time.time() - t0))
    except Exception as e:
        print('[raw] 处理连接异常（不影响其它连接）：%s: %s' % (type(e).__name__, e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _accept_loop(srv, max_bytes, uid_wait):
    while True:
        try:
            conn, addr = srv.accept()
        except Exception:
            time.sleep(0.2)
            continue
        # 每条连接一个线程：找记录可能要等几秒，不能堵住 accept
        threading.Thread(target=_handle_conn,
                         args=(conn, addr, max_bytes, uid_wait),
                         name='edge-raw-conn', daemon=True).start()


def start_raw_image_server():
    """启动图片原始 TCP 接收线程（幂等；端口被占只打印不抛）。"""
    if not getattr(config, 'EDGE_RAW_IMAGE', True):
        print('[raw] 已禁用（config.EDGE_RAW_IMAGE=False）')
        return False

    host = getattr(config, 'EDGE_RAW_HOST', '0.0.0.0')
    port = int(getattr(config, 'EDGE_RAW_PORT', DEFAULT_PORT))
    max_bytes = int(getattr(config, 'EDGE_RAW_MAX_BYTES', 2_000_000))
    uid_wait = float(getattr(config, 'EDGE_RAW_UID_WAIT_SEC', 6.0))

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((host, port))
        srv.listen(8)
    except OSError as e:
        print('[raw] 监听 %s:%d 失败（端口被占？）：%s' % (host, port, e))
        try:
            srv.close()
        except Exception:
            pass
        return False

    threading.Thread(target=_accept_loop, args=(srv, max_bytes, uid_wait),
                     name='edge-raw-image', daemon=True).start()
    print('[raw] 图片原始 TCP 通道已监听 %s:%d（协议 EIMG1，只收不回）' % (host, port))
    return True
