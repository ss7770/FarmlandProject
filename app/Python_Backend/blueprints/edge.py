"""K230 边缘推理结论接收（方案 B，2026-10-01）

架构为什么长这样（改代码前先读这段）：

    旧架构（D200，已淘汰）：
        摄像头 --MJPEG 拉流--> PC(Flask) --TFLite--> 入库
        问题：① 板子固件并发≈1，拉流期间再抓拍会把采集链路压死；
              ② 板子会假死（帧率正常但内容逐字节不变），只能断电重启；
              ③ 每帧 60KB 走两遍 WiFi，延迟 1~3 秒。

    新架构（K230，本文）：
        K230(摄像头 + KPU 推理) --HTTP POST JSON 结论--> Flask --> 入库 --> APP 通知
        好处：① 不过图片，几字节 vs 每帧 60KB；
              ② K230 自带算力，断网也能本地识别；
              ③ 没有"谁占着连接"的问题，天然多机位可扩展。

    代价：K230 跑的是 kmodel，PC 跑的是 TFLite，**两套模型**。

服务端为什么还要再判一次（重要）：
    K230 端已经有自己的置信度阈值，但那套阈值是在设备上调的、我们看不见也管不着。
    这里**再判一次**（复用 utils/inference.py 的三档口径），是为了：
        ① 兜住设备端阈值配错/被改的情况；
        ② 明确"什么进库"的唯一权威在服务端，换设备/换模型不用重新论证入库规则；
        ③ 设备端只传原始置信度，阈值口径单一来源。
    代价：两端阈值都改才算真正改口径，改的时候别只改一边。

接口：
    POST /api/edge/disease    接收一条推理结论（小包，几百字节，必须稳）
    POST /api/edge/image      给已入库的记录补传现场图（base64 大包，**已降级为备用**）
    GET  /api/edge/status     查询边缘设备上报状态（看板用）
    GET  /api/edge/ping       **仅排查用**：连通性探针
    GET  /api/edge/last-record **兜底用**：取最新记录 id（板端读不到响应头时的第 1 条退路）

⚠️ 【v15 2026-10-02】原 `POST/GET /api/edge/command`（「立即抓拍」指令通道）**已整条删除**：
    板端 `poll_command()`、前端 capture 代码、`config.EDGE_CMD_TTL_SEC` 一并下线。
    本文件现在是**纯被动接收**——不给设备下发任何东西。别再加回来。

⚠️ v10（2026-10-01）**图片主通道换成了原始 TCP**（见 utils/edge_raw.py）：
    板端跑过一次 KPU 推理之后，"读响应"会挂死（probe 干净环境下全通、
    主脚本一读就死 —— 详见 app/AI_Model/k230/README.md §7）。图片这条路要多等一次响应，
    所以最先牺牲：历史现象就是"记录一直在涨、APP 一张图都没有"。
    现在板端连上 TCP 端口、发 `EIMG1 <uid> <nbytes> <source>\n` + 原始 JPEG 就关连接，
    **一个 recv 都不做**，挂死点从结构上消失。
    本文件里的 `POST /api/edge/image`（base64）保留给 PC 脚本/老设备，功能不变。

设备端配对（2026-10-01 v9）：
    板端每轮生成一个 `client_uid`，`/disease` 和 `/image` 两次请求都带上它。
    这样即便板端读不到 `/disease` 的响应头（拿不到 record_id），图片也能贴对记录
    —— 否则就是"记录一直涨、APP 一张图都没有"。第 2 条退路见 /image 的说明。

为什么结论和图片是两个接口（2026-10-01 实测）：
    板子一次 POST 带上 198KB 图片时，urequests 报 `list index out of range`
    （= 读响应状态行读到空行，服务端一个字节都没回）。服务端没事，
    是 K230 的上行 socket（走核间通信）扛不住大包。
    拆开之后结论路径永远是小包，图片失败只影响"这条有没有图"。
    /api/edge/disease 仍兼容请求体里的 image 字段（PC/其它调用方可能还在用），
    但 K230 板端已改成走 /api/edge/image 单独传。
"""

import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

import config
from utils.db import (
    find_record_id_by_uid, get_db_connection, insert_disease_record, set_record_image,
)
from utils.inference import (
    CONFIRMED_THRESHOLD, SUSPECTED_THRESHOLD, VERDICT_TEXT, classify_verdict,
)

edge_bp = Blueprint('edge', __name__, url_prefix='/api/edge')

# ---- 运行状态（进程内，重启即清空） ----
_lock = threading.Lock()
_state = {
    'report_count': 0,      # 累计收到的合法上报条数
    'accepted_count': 0,    # 其中实际入库的条数
    'rejected_count': 0,    # 因未达确诊阈值而不入库的条数
    'dup_count': 0,         # 被判重复丢弃的条数
    'last_ts': '',          # 最近一次上报时间
    'last_result': None,    # 最近一次上报的完整处理结果
    'last_source': '',      # 最近一次上报的设备标识
    'last_image': '',       # 最近一张上报图片（可选字段，K230 可以选择也存图）
    '_last_accept_ts': 0.0,     # 节流用（单调时钟）
    '_last_key': '',            # 去重用
    '_last_key_ts': 0.0,
}

def _norm_source(source):
    """把设备上报的 source 归一化成适合入库的来源标识。

    默认 'k230'；只保留字母数字与 -_（防止往库里写脏字符串）。
    多机位时设备端传 'k230-a' / 'k230-b' 之类即可区分。
    """
    s = ''.join(c for c in (source or '') if c.isalnum() or c in '-_')
    return s or 'k230'


def _fnum(v, default=None):
    """宽松地把输入转成 float；转不了返回 default（设备端可能传字符串）"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _sane_confidence(c):
    """置信度合法性：必须落在 0~100。越界说明设备端单位搞错了（传了 0~1 或百分数×100）。

    这个护栏是吃过亏才加的——PC 端曾经出现过"二次 softmax 把 90% 压成 6%"的量纲错误，
    设备端传 0~1 的小数是最容易发生的同类事故，必须挡住，否则要么永远不入库、
    要么全被判成拒识，而且**看板上不会报错**，只表现为"识别一直没结果"。
    """
    if c is None:
        return None, 'confidence 缺失或不是数字'
    if c < 0:
        return None, 'confidence < 0，量纲可疑'
    if c <= 1.0:
        return None, ('confidence=%s 疑似 0~1 小数，请乘以 100 后上报（服务端统一用 0~100 百分数）' % c)
    if c > 100:
        return None, 'confidence > 100，量纲可疑'
    return round(c, 1), None


@edge_bp.after_request
def _force_close(resp):
    """边缘接口的响应一律 `Connection: close`。

    为什么（2026-10-01 板端卡死排查）：K230 上的 urequests 读响应是
    「一直读到 EOF」，它不像 CPython requests 那样严格按 Content-Length 截断。
    如果服务端走 HTTP/1.1 keep-alive 不关连接，板端就会一直等 EOF →
    表现为**看板/库里记录已经写入，板子却"卡死"**（结论其实送达了）。
    显式关连接，让板端一定能读到 EOF 正常返回。

    ⚠️ 2026-10-01 第五次上板补充：**HTTP/1.0 请求不要再自己加这个头**。
    werkzeug 对 HTTP/1.0 请求本来就会加 `Connection: close`，我们又加一次 →
    响应头里连着两行同名头（实测就是两行），白送 19 字节。
    而"响应越小越安全"正是这一轮的核心结论（见 _edge_json 的体积说明），
    所以这里只在 HTTP/1.1 且尚未设置时才补。

    只影响 /api/edge/*，浏览器的看板请求不受影响。
    """
    try:
        proto = (request.environ.get('SERVER_PROTOCOL') or '').upper()
    except Exception:
        proto = ''
    if proto != 'HTTP/1.0' and 'Connection' not in resp.headers:
        resp.headers['Connection'] = 'close'
    return resp


# 关键字段的响应头名。板端只读这几个头，**不读响应体**。
EDGE_H_STATUS = 'X-Edge-Status'
EDGE_H_VERDICT = 'X-Edge-Verdict'
EDGE_H_ACCEPTED = 'X-Edge-Accepted'
EDGE_H_RECORD_ID = 'X-Edge-Record-Id'
# 板端用这个头跟服务端要"极短响应"（体积说明见 _edge_json）
EDGE_H_BRIEF = 'X-Edge-Brief'


def _want_brief():
    """板端是否要求"极短响应"？头 `X-Edge-Brief: 1` 或 `?brief=1`。

    只认板端主动要求 —— 看板/APP/PC 脚本不带这个头，照旧拿完整 JSON，零影响。
    """
    try:
        if (request.headers.get(EDGE_H_BRIEF) or '').strip().lower() in ('1', 'true', 'yes'):
            return True
        return (request.args.get('brief') or '') == '1'
    except Exception:
        return False


def _edge_json(payload, status=200):
    """边缘接口统一出口：返回 JSON 的**同时把关键字段镜像到响应头**。

    为什么要把结果放头里（2026-10-01 上板实测，别改回去）：
        K230 的 urequests 读响应体用的是 `sock.read()`（无参 = 一直读到 EOF），
        板上的 `timeout=` 参数**不生效** —— 实测"记录已经写进库了，板子却卡死"，
        日志停在 `[report] POST 上报中…` 后面。而且它读 body 时若服务端半关闭、
        或响应体超出板端缓冲，就会永久阻塞，异常也不抛，最后看门狗复位。

    新口径（v6）：板端用自实现的 HTTP 客户端（见 k230_classify.py 的 _http），
        **读完响应头就立刻关连接，绝不读 body**。响应头以小包返回、且以 `\\r\\n\\r\\n`
        结尾有明确终止符，读到就停，绝不会挂死。所以关键结果必须走头：
            X-Edge-Status     ok / error / ...
            X-Edge-Verdict    confirmed / suspected / rejected / duplicate / throttled
            X-Edge-Accepted   1 / 0
            X-Edge-Record-Id  入库主键（accepted 时才有）

    ⚠️ 关于"响应体积"（2026-10-01 第八次上板后**更正过**，别再照旧结论改代码）：
        第五~七轮一度认定"响应越小越稳"（GET 253B 通 / POST 933B 卡），并据此把响应压到 ~185B。
        但第六轮把 POST 响应压到 **128B**（比"每次都通"的 GET 还小）**照样卡死** ⇒ 体积不是根因。
        真因在板端：`sock.settimeout()` 一设，`recv()` 就走 `uselect.poll`，而 poll 在这块固件上
        不可靠（详见 `app/AI_Model/k230/README.md` §7.3 与 `k230_classify.py` 的 v9 命门）。
        所以 `X-Edge-Brief` 从"保命手段"降级为"顺手省几十字节"：
        **留着没坏处，但不要再为它牺牲信息**。

    真正必须保留的是**把结果镜像到响应头** —— 板端只读响应头、绝不读 body（见上）。
    写回 `X-Edge-Record-Id` 尤其重要：图片补传靠它，读不到时板端还能用 `client_uid` 兜底。

    响应体照旧返回完整 JSON —— 看板、APP、PC 端脚本（urllib/requests）都照常用，
    对它们来说这只是多几个头，零影响。
    """
    resp = jsonify(payload)
    try:
        resp.headers[EDGE_H_STATUS] = str(payload.get('status') or '')
        if payload.get('verdict') is not None:
            resp.headers[EDGE_H_VERDICT] = str(payload.get('verdict'))
        if payload.get('accepted') is not None:
            resp.headers[EDGE_H_ACCEPTED] = '1' if payload.get('accepted') else '0'
        if payload.get('record_id') is not None:
            resp.headers[EDGE_H_RECORD_ID] = str(payload.get('record_id'))
    except Exception:
        pass    # 头写不进去也不影响响应体，绝不因此让请求失败
    if _want_brief():
        # 板端一个字节 body 都不读 —— 那干脆别发（8 字节 vs 几百字节，
        # 直接决定它能不能一次收完）。信息全在上面的 X-Edge-* 头里。
        try:
            resp.data = b'{"ok":1}'
        except Exception:
            pass
    return resp, status


@edge_bp.route('/disease', methods=['POST'])
def report_disease():
    """接收 K230 的一条推理结论。

    请求体（JSON）：
        {
          "disease":    "Potato___Late_blight",   # 必填，类别名（与 labels.txt 同命名体系）
          "confidence": 88.5,                     # 必填，0~100 百分数
          "source":     "k230",                   # 可选，设备标识，多机位时用来区分
          "timestamp":  "2026-10-01 09:30:00",    # 可选，设备侧时间（缺省用服务端时间）
          "image":      "<base64 jpeg>",          # 可选，有就存盘（看板缩略图，便于人工复核）
          "verdict":    "confirmed",              # 可选，设备端自己的判定，仅作记录/比对用
          "manual":     false,                    # 可选，true = 人按按钮触发的采集（跳过去重）
          "client_uid": "k230-canmv-1234-7"       # 可选，设备端关联 id（图片配对用，见下）
        }

    返回：
        {"status":"ok", "accepted":true, "verdict":"confirmed", ...}
        accepted=true 表示已写入 disease_records，APP 会在 30 秒内弹通知 + TTS。

    入库口径（2026-10-01 起）：**拍到就入库**——config.RECORD_ALL=True 时不再要求确诊，
    任何一次采集都会留一条记录（含未确诊），未确诊的由后台 TTL 清理回收。
    看板/APP 对未确诊记录只显示"置信率不足"，不展示病名（不做诊断）。

    client_uid（2026-10-01 v9 新增）：设备端每轮自己生成一个 id，结论和图片两次请求都带上它。
    因为 K230 读响应头不稳，`record_id` 有可能传不回来 —— 有了 uid，图片照样能贴对记录
    （见 /image 的说明与 utils/db.find_record_id_by_uid）。
    """
    if not getattr(config, 'EDGE_ENABLED', True):
        return _edge_json({'status': 'error', 'msg': 'edge endpoint disabled',
                           'detail': 'config.EDGE_ENABLED=False'}, 503)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _edge_json({'status': 'error', 'msg': 'body must be a JSON object'}, 400)

    disease = str(body.get('disease') or '').strip()
    if not disease:
        return _edge_json({'status': 'error', 'msg': 'disease is required'}, 400)

    confidence, err = _sane_confidence(_fnum(body.get('confidence')))
    if err:
        return _edge_json({'status': 'error', 'msg': err,
                           'detail': '服务端与设备端统一使用 0~100 百分数'}, 400)

    source = str(body.get('source') or 'k230').strip()[:32]
    ts_field = str(body.get('timestamp') or '').strip()
    # manual=True 表示这是"人主动要的"一次采集，不是自动巡检轮次。
    # 区别有二：① 绕过去重（人要了就该出一条，哪怕和上一轮一模一样）；
    # ② 日志和响应里标注出来，便于在记录里区分"设备自己拍的"和"人要它拍的"。
    # ⚠️ v15（2026-10-02）「立即抓拍」指令通道已删除 ⇒ 板端主循环不再传 manual=True，
    #    这个字段现在只由 probes/ 手动调用使用，保留是为了协议不回退、方便人工排查。
    manual = bool(body.get('manual'))
    # 设备端自带关联 id：图片补传靠它配对（板端读不到响应头也能贴图，见 /image）。
    client_uid = str(body.get('client_uid') or '').strip()[:64]

    now = time.time()
    with _lock:
        # ① 节流：设备端脚本写错成死循环上报时，别把后端和数据库打爆
        #
        # ⚠️ v10（2026-10-01）：**manual=True 不再受节流限制**。
        #    因为板端从 v10 起「只发不读」—— 它收不到 429，被节流就等于
        #    **记录和图一起静默丢掉**，而人主动要的那一次却什么都没发生。
        #    （v15 起「立即抓拍」通道已删除，manual 只由 probes 使用；
        #      该豁免保留 —— 人工排查时本就不该被节流挡住。）
        gap = now - _state['_last_accept_ts']
        min_gap = float(getattr(config, 'EDGE_MIN_INTERVAL', 5))
        if not manual and _state['_last_accept_ts'] and gap < min_gap:
            return _edge_json({'status': 'error', 'msg': 'too frequent',
                               'verdict': 'throttled',
                               'detail': '距上次上报仅 %.1fs，最小间隔 %.0fs'
                                         % (gap, min_gap)}, 429)

        # ② 去重：同一结论在窗口内重复上报只算一次
        #    病叶在镜头前不动时，K230 每轮巡检都会得出完全一样的结果，
        #    不去重的话库里会被同一条刷满（D200 时代就犯过这个错：13 条一模一样的记录）
        #    —— 但**手动触发不去重**：人按了按钮就该有反馈，哪怕结果和上一条完全相同。
        key = '%s|%s' % (disease, confidence)
        dedup = float(getattr(config, 'EDGE_DEDUP_SEC', 120))
        if not manual and key == _state['_last_key'] and (now - _state['_last_key_ts']) < dedup:
            _state['dup_count'] += 1
            _state['report_count'] += 1
            _state['last_ts'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            return _edge_json({'status': 'ok', 'accepted': False, 'verdict': 'duplicate',
                               'msg': '与上次相同结论，%ds 内视为重复，未入库' % int(dedup),
                               'disease': disease, 'confidence': confidence,
                               'device_verdict': body.get('verdict')})

    # ③ 服务端二次三档判定（权威口径）
    verdict = classify_verdict(confidence)

    # ④ 图片可选：有就存。
    #    原来只在确诊时存盘（怕非确诊的图把 uploads 撑爆），现在记录本身就全存了，
    #    图再卡确诊就会出现"有记录没图"的怪现象；体积问题交给后台 TTL 清理一起回收。
    image_path = ''
    b64 = body.get('image')
    if b64:
        image_path = _save_base64_image(b64, source)

    # ⑤ 入库：默认"拍到就入库"（config.RECORD_ALL=True），不再卡确诊线。
    #    未确诊的记录由 utils/db.purge_unconfirmed 到期清理。
    record_all = bool(getattr(config, 'RECORD_ALL', True))
    accepted = False
    record_id = None
    if record_all or verdict == 'confirmed':
        try:
            record_id = insert_disease_record(
                disease, confidence,
                location=source,
                image_path=image_path,
                # 存设备上报的来源标识（默认 'k230'）。
                # 原来硬编码成 'k230' 会把多机位压成同一个值，
                # 看板/APP 的来源小标签就分不出是哪台设备。
                # 归一化：只保留字母数字和 -_，避免脏数据写进库。
                source=_norm_source(source),
                client_uid=client_uid,
            )
            accepted = True
        except Exception as e:
            return _edge_json({'status': 'error', 'msg': 'db insert failed',
                               'detail': '%s: %s' % (type(e).__name__, e)}, 500)

    result = {
        'status': 'ok',
        'accepted': accepted,
        'record_id': record_id,
        'verdict': verdict,
        'verdict_text': VERDICT_TEXT[verdict],
        'disease': disease,
        'confidence': confidence,
        'source': source,
        'manual': manual,
        'device_verdict': body.get('verdict'),
        'client_uid': client_uid or None,
        'thresholds': {'suspected': SUSPECTED_THRESHOLD, 'confirmed': CONFIRMED_THRESHOLD},
        'device_timestamp': ts_field or None,
        'server_timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    if not accepted:
        result['msg'] = '未入库（RECORD_ALL=%s，verdict=%s）' % (record_all, verdict)
    elif verdict != 'confirmed':
        result['msg'] = ('已入库 record_id=%s，但置信度 %.1f%% 未达确诊线 %.0f%%：'
                         '记录与图片保留 %d 分钟后自动清理'
                         % (record_id, confidence, CONFIRMED_THRESHOLD,
                            int(getattr(config, 'UNCONFIRMED_TTL_MIN', 30))))

    with _lock:
        _state['report_count'] += 1
        if accepted:
            _state['accepted_count'] += 1
        else:
            _state['rejected_count'] += 1
        _state['last_ts'] = result['server_timestamp']
        _state['last_result'] = result
        _state['last_source'] = source
        _state['last_image'] = image_path
        _state['_last_accept_ts'] = now
        _state['_last_key'] = key
        _state['_last_key_ts'] = now

    print('[K230] %s %s%% -> %s%s%s' % (
        disease, confidence, verdict,
        ' [手动]' if manual else '',
        (' (record_id=%s)' % record_id) if accepted else ' (不入库)'))
    return _edge_json(result)


def _public_url(image_path):
    """'uploads/edge_k230_xxx.jpg' -> '/api/uploads/edge_k230_xxx.jpg'（与 disease.py 口径一致）"""
    return ('/api/uploads/' + image_path.split('/')[-1]) if image_path else ''


def _remove_image_file(image_path):
    """删掉刚写盘的图（记录不存在时避免留孤儿文件）。失败静默。"""
    import os
    try:
        p = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            image_path)
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass


@edge_bp.route('/image', methods=['POST'])
def attach_image():
    """给**已入库**的记录补传一张现场图。

    为什么要把图片从结论请求里拆出来（2026-10-01 实测教训，别改回去）：
        K230 把一张 198KB 的 JPEG（base64 27 万字符）塞进结论请求一次 POST 时，
        板端 urequests **拿不到任何响应**，只报 `list index out of range`
        ——那是 urequests 读响应状态行读到空行时的报错，意味着服务端一个字节都没回。
        服务端本身没问题：PC 端用 urllib 传 423KB base64 完全正常，
        瓶颈在 K230 上行走核间通信 socket，大包不稳（同类现象见 canmv_k230 issue #54）。
    拆开之后：
        结论请求永远只有几百字节 → 必然成功 → 记录、APP 通知都不受影响；
        图片单独传 → 失败最多是"这条记录没图"，不会连记录一起丢。

    请求体：
        {"record_id": 47, "image": "<base64 jpeg>", "source": "k230-canmv"}
        {"client_uid": "k230-canmv-1234-7", "image": "..."}   ← record_id 可以不给

    定位规则（v9 起，两条路任选其一）：
        ① 给了 record_id 且记录存在 → 直接 UPDATE；
        ② 没给 / 给了但不存在 → 用 `client_uid` 找（板端读不到结论响应头时走这条）。
           之所以必须留这条路：板端读响应头不稳，record_id 有可能永远传不回来，
           那样就会变成"记录一直在涨、APP 一张图都没有"。

    返回：
        {"status":"ok","record_id":47,"image_url":"/api/uploads/edge_....jpg"}
        record_id 同时镜像到响应头 X-Edge-Record-Id（板端只读头就能补上自己缺失的 id）。
    """
    if not getattr(config, 'EDGE_ENABLED', True):
        return _edge_json({'status': 'error', 'msg': 'edge endpoint disabled'}, 503)

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _edge_json({'status': 'error', 'msg': 'body must be a JSON object'}, 400)

    b64 = body.get('image')
    if not b64:
        return _edge_json({'status': 'error', 'msg': 'image 必填'}, 400)

    try:
        record_id = int(body.get('record_id'))
    except (TypeError, ValueError):
        record_id = None

    client_uid = str(body.get('client_uid') or '').strip()[:64]
    resolved_by = 'record_id'
    if record_id is None or not _record_exists(record_id):
        # 回头按 uid 找（板端读不到响应头时唯一的定位手段）
        rid = find_record_id_by_uid(client_uid) if client_uid else None
        if rid is None:
            if record_id is None:
                return _edge_json({'status': 'error',
                                   'msg': 'record_id 与 client_uid 至少要给一个（且能定位到记录）',
                                   'client_uid': client_uid or None}, 400)
            return _edge_json({'status': 'error',
                               'msg': 'record_id=%s 不存在' % record_id}, 404)
        resolved_by = 'client_uid'
        record_id = rid

    source = str(body.get('source') or 'k230').strip()[:32]
    image_path = _save_base64_image(b64, source)
    if not image_path:
        return _edge_json({'status': 'error',
                           'msg': '图片解码/存盘失败（非 JPEG、过小或过大）'}, 400)

    if not set_record_image(record_id, image_path):
        _remove_image_file(image_path)     # 记录不存在，别留孤儿文件
        return _edge_json({'status': 'error',
                           'msg': 'record_id=%s 不存在' % record_id}, 404)

    print('[K230] 补图 record_id=%s -> %s（按 %s 定位）'
          % (record_id, image_path, resolved_by))
    return _edge_json({'status': 'ok', 'record_id': record_id,
                       'resolved_by': resolved_by,
                       'image_url': _public_url(image_path)})


def _record_exists(record_id):
    """记录是否存在。用于"给了 record_id 但库里没有"时改走 uid 兜底。"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('select 1 from disease_records where id = ?', (int(record_id),))
        hit = cur.fetchone() is not None
        conn.close()
        return hit
    except Exception:
        return False


@edge_bp.route('/ping', methods=['GET'])
def ping():
    """**仅排查用**：连通性 + 响应体积探针，无任何副作用。

    为什么需要它（2026-10-01 第五次上板排查）：
        板端表现是"GET 响应 253 字节能通、POST 响应 933 字节必卡"。
        要分清到底是**体积**的锅还是 **POST/请求体**的锅，就得让 GET 也能返回大响应：
            GET /api/edge/ping           → 极小响应（连通性基线）
            GET /api/edge/ping?size=700  → 约 700 字节响应体（复刻"大响应"）
        若 `?size=700` 也卡 → 与 HTTP 方法无关，就是体积问题（→ 用 brief 修）；
        若它照常通 → 那问题在"带请求体的 POST"上，方向完全不同。
    """
    try:
        n = int(request.args.get('size') or 0)
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(4096, n))
    return _edge_json({'status': 'ok', 'pad': 'x' * n})


@edge_bp.route('/last-record', methods=['GET'])
def last_record():
    """**给板端兜底用**：取最新一条记录的 id（可按机位过滤）。

    板端正常从 POST 响应头 `X-Edge-Record-Id` 拿 record_id；
    万一哪天又读不到响应头，就退一步走这个 **GET** 把 id 捞回来 ——
    图片补传因此还能走通（GET 通道是实测每次都通的，见 _edge_json 的体积说明）。

    参数：
        source=k230-canmv   可选，按机位过滤（写进 location 的那份）
        after=81            可选，只要 **id 大于** 它的；没有更新的就返回 null。
                            板端靠这个保证"只认比我已知的更新的记录"，
                            避免把新拍的图贴到旧记录上。

    返回：{"status":"ok","record_id":82} 或 {"status":"ok","record_id":null}
    """
    src = _norm_source(request.args.get('source'))
    try:
        after = int(request.args.get('after') or 0)
    except (TypeError, ValueError):
        after = 0

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        if src and src != 'unknown':
            cur.execute(
                'select id from disease_records where id > ? and location like ? '
                'order by id desc limit 1', (after, '%' + src + '%'))
        else:
            cur.execute('select id from disease_records where id > ? '
                        'order by id desc limit 1', (after,))
        row = cur.fetchone()
        conn.close()
    except Exception as e:
        return _edge_json({'status': 'error', 'msg': str(e)}, 500)

    rid = row[0] if row else None
    return _edge_json({'status': 'ok', 'record_id': rid})


def _save_base64_image(b64, source):
    """把设备端回传的 base64 JPEG 存到 uploads/，返回相对路径（失败返回空串）。

    用途：给记录留一张现场图，方便看板和 APP 显示缩略图、让人能人工复核
    "AI 到底看到了什么"。不传就只入库结论，记录功能不受影响。
    校验：必须是 JPEG（魔数 ffd8）、100B ~ 8MB，否则丢弃。

    ⚠️ v10（2026-10-01）：落盘实现已抽到 `utils/edge_raw.save_jpeg_bytes()` ——
    板端现在走**原始 TCP** 推图（不经 base64），两条通道必须写出同一种文件、
    用同一种校验和命名，所以这里只负责"解码 + 交给它"，别再各写一份。
    """
    import base64

    s = (b64 or '').strip()
    if s.startswith('data:'):            # 兼容 data:image/jpeg;base64,xxx 形式
        s = s.split(',', 1)[-1]
    try:
        raw = base64.b64decode(s, validate=False)
    except Exception:
        return ''
    from utils.edge_raw import save_jpeg_bytes
    return save_jpeg_bytes(raw, source)


def _edge_snapshot_locked():
    """调用方需持有 _lock：把内部状态整理成对外的字典（去掉下划线开头的内部字段）"""
    st = {k: v for k, v in _state.items() if not k.startswith('_')}
    st['enabled'] = bool(getattr(config, 'EDGE_ENABLED', True))
    st['host'] = getattr(config, 'EDGE_HOST', '')
    st['dedup_sec'] = float(getattr(config, 'EDGE_DEDUP_SEC', 120))
    st['min_interval'] = float(getattr(config, 'EDGE_MIN_INTERVAL', 5))
    st['require_confirmed'] = bool(getattr(config, 'EDGE_REQUIRE_CONFIRMED', True))
    st['record_all'] = bool(getattr(config, 'RECORD_ALL', True))
    st['unconfirmed_ttl_min'] = int(getattr(config, 'UNCONFIRMED_TTL_MIN', 30))
    # 原始 TCP 图片通道（v10）：看板上要能一眼看出图该往哪个端口推
    st['raw_image'] = bool(getattr(config, 'EDGE_RAW_IMAGE', True))
    st['raw_port'] = int(getattr(config, 'EDGE_RAW_PORT', 5001))
    st['thresholds'] = {'suspected': SUSPECTED_THRESHOLD,
                        'confirmed': CONFIRMED_THRESHOLD}
    st['online'] = bool(st.get('last_ts'))     # 有上报记录即视为"设备在用"
    return st


def snapshot():
    """对外暴露的设备状态快照（供 /api/disease/overview 等聚合接口复用）。

    与 GET /api/edge/status 同源，避免两处各写一份导致口径漂移。
    """
    with _lock:
        return _edge_snapshot_locked()


@edge_bp.route('/status')
def edge_status():
    """看板用：边缘设备上报状态。"""
    return jsonify({'status': 'ok', 'edge': snapshot()})
