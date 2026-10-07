# -*- coding: utf-8 -*-
"""K230 端病害识别 + 上报（方案 B：设备端 KPU 推理）v2

在 CanMV IDE 里运行。链路：
    摄像头出图 -> 预处理(转置+/255) -> KPU 跑 model.kmodel -> top1
    -> POST 结论到 Flask /api/edge/disease -> 服务端三档判定 + 入库 -> APP 通知

【v2 修正 2026-10-01】板端 nncase_runtime 的 API 与 PC 端 nncase 是两套东西：
    v1 错误写法（板上不存在，会报 AttributeError）：
        nn.kpu.Interpreter(0,0,0)          -> type object 'kpu' has no attribute 'Interpreter'
        nn.RuntimeTensor.from_numpy(x)     -> 板上没有 RuntimeTensor
        kmodel.load_kmodel(f.read())       -> 板上 load_kmodel 直接传路径
    v2 正确写法（与官方 demo 一致）：
        kpu = nn.kpu()
        kpu.load_kmodel("/sdcard/model.kmodel")     # 传路径字符串
        kpu.set_input_tensor(0, nn.from_numpy(x))
        kpu.run()
        probs = kpu.get_output_tensor(0).to_numpy()

【v3 修正 2026-10-01】板上 ulab 是 MicroPython 的阉割版 numpy：
        frame.astype(np.float32)   -> AttributeError: 'ndarray' object has no attribute 'astype'
        x.transpose((1,2,0))       -> transpose 不收轴参数
    预处理改用官方示例固定套路（无参 transpose + reshape + copy）：
        (C,H,W) -> reshape (C,H*W) -> transpose() -> (H*W,C)
                -> copy().reshape (H,W,C) -> reshape (1,H,W,C) -> 保持 uint8

【v4 修正 2026-10-02】kmodel 输入口径从 float32 改成 uint8：
    `x / 255.0` 已经**删掉**——归一化被烤进 kmodel 了（to_kmodel.py: INPUT_MODE='uint8'，
    preprocess=True + input_range=[0,1]）。板端只要原样喂 0~255 的 uint8。
    ⚠️ 别再改回 /255.0：float32 输入会让 nncase 编译出**没有 KPU 段**的纯 CPU 模型，
    板上一次推理 66 秒（2026-10-02 P11/P8 实测）。

【v5 修正 2026-10-02】抓拍改成「snapshot → 直接编码」，**零转换、零缩放**：
    旧写法在抓拍这一步让**板子直接掉电**（无 Python 回溯、USB 掉线）：
        img.to_rgb888(x_scale=0.5, y_scale=0.5)   # P9 崩在这
        img.to_rgb888()                           # P10 无参也崩
    ⇒ 不是缩放参数用错，是「对 CHN0 的 YUV420SP 活帧做 image 转换」这条路整体不成立
      （K230 的 image 模块对 YUV420 几乎不支持任何处理）。官方源码里 to_rgb888()
      只出现在**读文件**处，活帧从不用它。
    ⇒ v14 起**抓拍通道 = CHN1（RGB565）**：p13 探针上板实证（2026-10-02 14:15），
      裸 Sensor + CHN1 RGB565 + `_encode_jpeg()` 一次出**正常彩色**图
      （q60=15179 字节；判读指标：绿 0% / 品红 0% / 灰带 0 行）。
      CHN0 的 YUV420SP 在这版 image 模块里**没有任何可用的 JPEG 出口**
      （save/to_jpeg 全花屏，OSError: current format not support save function），
      **别把抓拍通道改回 CHN0**。
      `_to_rgb_scaled()` 与 `IMAGE_SCALE_LADDER` 已删除；`IMAGE_QUALITY_LADDER` 保留
      （q60 已占上限 87.6%，质量阶梯是唯一的刹车）。
    AI 那条路（CHN2 `to_numpy_ref()` → `preprocess()` → `kpu.run()`）**一行没动**。

【v15 修正 2026-10-02】**「立即抓拍」整条链已删除**（用户拍板：全链路杀死）：
    删除范围：`CMD_URL` / `CMD_POLL_INTERVAL` / `CMD_POLL_MS` / `CMD_SETTLE` / `CMD_BUDGET`
    / `poll_command()` / 主循环里的指令轮询块（原 `# ---- ① 取指令 ----`）。
    原因：它是**唯一**需要"服务端 → 板端"下行方向的通道，为此引入了每 2 秒一次的
    HTTP 往返 + 读响应头；删掉之后板端**彻底零下行**，只剩"发出去"这一个方向，
    `_http()` 的读头路径也只剩结论/图片两处调用。
    ⇒ 服务端 `POST/GET /api/edge/command` 与 `config.EDGE_CMD_TTL_SEC` **同步删除**，
      页面上的 `#db-btn-capture` 早已不在，前端 JS 的 capture 代码也一并清掉。
    ⚠️ 板子上的巡检节奏仍是 1 秒心跳 + 每 `LOOP_INTERVAL`(10s) 一轮推理，行为不变。

【首次运行建议】先跑 probes/ 下的探针分步验证，全 ✔ 再跑本脚本。

需要先放到板子 /sdcard/ 的文件：
    model.kmodel    3.48 MB（PC 上 to_kmodel.py 生成。⚠️ 旧的 12.35 MB 版本是 float32 口径，
                              板上 66 秒，已弃用，见 v4 说明）
    labels.txt      38 类

模型规格：
    输入 : **uint8 (1,224,224,3) NHWC，值域 0~255**（归一化在 kmodel 内部）
    输出 : float32 (1,38)，已带 softmax（和≈1.0）→ 直接取 max，不要再 softmax
    ⚠️ kmodel 里带 KPU 段的模型**PC 模拟器加载不了**（连官方导出的也加载不了），
       所以精度只能在板上验；旧的"60/60 对齐 TFLite"是在一个单段(纯 CPU)模型上测的，不作数。

与服务端的契约：
    confidence 传 0~100 百分数（服务端收到 <=1.0 判非法）
    服务端有节流(5s)和去重(120s)，巡检间隔 10s 是算过的值
    **结论与图片分两次请求**：
        POST /api/edge/disease   结论，几百字节，必须稳；accepted 时返回 record_id
        POST /api/edge/image     拿着 record_id 补传现场图，失败不影响记录
    为什么不合成一次：大包上行会让 urequests 读不到响应（list index out of range），
    连记录一起丢。详见 report_image() 的注释。改代码前先读那段。

【v4 修正 2026-10-01】三件事：
    ① 主循环改成 1 秒心跳 + 每 2 秒轮询 /api/edge/command，
       这样看板/APP 点「立即抓拍」能在 1~2 秒内真的拍一张（原来是 10 秒一轮，等太久）。
       手动触发的那一轮会带 manual=True，服务端据此跳过去重——人按了就该出一条。
    ② 图片不再只在确诊时回传：服务端已改成"拍到就入库"，每条记录都配图才有意义。
    ③ 未确诊的记录服务端也会入库，展示为「置信率不足」（不给病名），
       并由服务端按 TTL 自动清理，板端不用管。

【v5 修正 2026-10-01】上板实测"第 1 轮跑完就卡死、看门狗复位"，按以下方向加固：
    ① **每一步都埋点**：`[shot] ①snapshot ②to_rgb ③编码` + `[report] POST 上报中/响应已收到`
       —— 卡死时日志停在哪一行，就说明卡在哪一步，不用再猜。
    ② **每一步都不许"等"**（原话是"超时宁短勿长"，v9/v9.1 改成更彻底的做法）：
       卡在网络里时 MicroPython 自己不报错，外部看门狗先复位板子，
       日志就永远停在半句话上。**v9.1 的做法**：绝不 `settimeout`，
       改为"**先睡够 + `setblocking(False)` 轮询**"（每次 recv 前重设非阻塞，带时间预算），
       见文件末尾 v9.1 修正。
    ③ **显式 `Connection: close`**：板端 urequests 读响应是"读到 EOF 为止"，
       服务端若不关连接（HTTP/1.1 keep-alive）它会一直等 —— 表现为
       "库里记录已经有了，板子却卡死"。服务端 `/api/edge/*` 已配合加 `after_request` 强制关。
    ④ **整轮包 try**：任何一环出错只丢这一轮，不再冒泡到 `finally: pl.destroy()`
       （显示通道 destroy 在没接 LCD 时可能卡住，那才是真正的"卡死"）。

【v6 修正 2026-10-01】上板第二次卡死（display_mode 修好后）定位到**真凶：读响应体**。
    现象：日志 `[infer] Blueberry___healthy 27.4%` → `[report] POST 上报中…` → 卡死；
          而**服务端库里 id=72 已经写进去了**（数字与日志完全对上）
          → 请求到达且处理成功，卡的是**板端读响应**。
    ① **不再用 urequests**：它的 `timeout=` 在 CanMV 上不生效（卡住不抛异常），
       而且读响应体是 `sock.read()`（无参 = 一直读到 EOF），最容易挂死。
       改用文件里自实现的 `_http()`：HTTP/1.0 +
       **读到响应头（`\r\n\r\n` 有终止符）就立刻关连接，绝不读 body**。
       （当时配的是"显式 settimeout"，那一句在 v9 被删掉了 —— 它才是六~八轮卡死的根因。）
    ② **结果走响应头**：服务端 `blueprints/edge.py::_edge_json()` 把
       `X-Edge-Verdict / X-Edge-Accepted / X-Edge-Record-Id / X-Edge-Cmd`
       镜像到响应头，所以板端不读 body 也拿得到 record_id —— 图片补传照常。
    ③ 这也解释了 63~72 那批记录 `image_path` **全为空**：
       每次都在结论的响应读取上卡死，根本没跑到抓拍那一步。

【先跑 `k230_probe.py`】它就是为这次卡死写的：只做"抓拍链路"和"上报往返"两件事，
    每小步打一行。上板跑一遍，日志停在哪行，问题就在那行。

【v7 修正 2026-10-01】第五次上板（v6.1 仍然卡在同一位置）定论：**响应体积就是命门**。
    现象对照（服务端实测字节数，板上表现）：
        GET  /api/edge/command   头 217B + 体 36B  = 253B  → **每次都通**
        POST /api/edge/disease   头 273B + 体 660B = 933B  → **每次都卡死**
    而把读块从 256 改成 1024 **毫无变化** —— 说明瓶颈不是"我们请求读多少"，
    而是"板端一次能拿到多少、拿不全之后还能不能等到下一块"。
    能跑通的那条，**整个响应正好落在一次投递里**。所以 v7 两手一起上：
    ① **请求带 `X-Edge-Brief: 1`**：服务端把 body 从 660 字节压成 8 字节，
       再砍掉重复的 `Connection` 和没用的 `Server`/`Date` 头（见 server 端
       `edge.py` 的 `_edge_json` 与 `app.py` 的 `_LeanWSGIRequestHandler`），
       整个响应 ~185 字节 —— 稳稳落在"已证明能通"的 253 以内。
    ② **每次 recv 前用 select 探一次可读**，读不到就打印"已收 N 字节"退出：
       就算以后再出意外，也是**超时退出**而不是挂死等看门狗复位。
    ③ **读不到响应头 ≠ 失败**：返回 `(0, {})` 让调用方按"已送达"处理，不重发
       （手动抓拍会绕过去重，重发就是重复记录）；record_id 改用 **GET
       `/api/edge/last-record`（带 `after=`）** 捞回来 —— GET 通道实测每次都通，
       图片补传因此才算真正有保障（历史上从未成功过）。

【v9 修正 2026-10-01】第八次上板后定论 —— **别再碰 settimeout，这是六~八轮的共同点**。
    三轮的现象互相矛盾，但只有一条解释能同时说通（完整对照见 README §7.3）：
        第六轮 v7（阻塞 recv + settimeout6 + select 兜底）→ **永久卡死**
        第七轮 v8（settimeout6 + setblocking(False) 非阻塞轮询）→ **不卡了，但每次都读到 0 字节**
        第八轮 v8.1（同上 + 发完先睡 0.25s）→ **又卡死**
    决定性对照（CanMV REPL，同一台板、同一个服务端）：
        s = socket.socket()          ← **没有调用 settimeout**
        s.connect(...); s.sendall(req); time.sleep(2)
        s.recv(2048)                 ← **普通阻塞 recv，一次就读全**
        -> b'HTTP/1.1 200 OK ... X-Edge-Record-Id: 103 ... {"ok":1}'
    而**失败的那几版全都调了 `sock.settimeout()`**，REPL 那次没调。
    ⇒ 根因：**这块固件上一设超时，`recv()` 就被挂到 `uselect.poll` 上，而 poll 在这种
      非阻塞/超时语义下不可靠** —— 没数据时它不"等待"，要么让 recv 永久阻塞（卡死），
      要么让 recv 立刻返回空（读到 0 字节）。这就把六~八轮的所有矛盾现象一次解释完了。
    v9 因此只改一件事：**彻底不设超时**——
        不 `settimeout`、不 `setblocking`、不用 `uselect`。
        connect 交给固件自己的超时（连不上会打印 `run connect failed`）；
        读响应用"**先睡一下 + 普通阻塞 recv**"，读的永远是"已经躺在缓冲区里"的数据。
    另加一层兜底（图片不再依赖"读得到响应头"）：
        **`client_uid`** —— 板端每轮生成一个 id，结论和图片两次 POST 都带上它；
        万一响应头读不到（拿不到 record_id），服务端按 uid 把图贴到那条记录上。
        `send_round()` 也不再因"读失败"跳过抓拍。

【v9.1 修正 2026-10-01】第九次上板：**v9 的方向对了，但代价露出来了**。
    ✅ 好消息（九轮里第一次）：**读响应终于通了**。同一轮里连续四次都拿到完整响应头：
        [http] recv #1 -> 161B，累计 161B，含空行=True      ← 结论 POST（429）
        [http] recv #1 -> 137B，累计 137B，含空行=True      ← probe B2 GET /command
        [http] recv #1 -> 186B，累计 186B，含空行=True      ← probe B5 POST（record_id=112）
        [http] recv #1 -> 139B，累计 139B，含空行=True      ← probe B6 GET /last-record
       没有 settimeout ⇒ 不再走 uselect.poll ⇒ 一次 recv 拿全。**v9 的判定成立。**
    ❌ 代价：**"不设超时"等于"没有退路"**。第一轮巡检第 1 次 POST 读通了（161B/429），
       等 5.5 秒重试的第 2 次 POST **永久卡死在 recv 上**（日志停在
       "先睡 0.35s 再阻塞读响应头..." 之后再无一行）—— 因为阻塞 recv 一旦"数据不来"
       就会一直等，而这块板子上"数据不来"是真实存在的：
       同一次 probe 里 B3/B4 都是 `connect to server faild!` + `OSError: Errno 107 ENOTCONN`
       （12s / 13s 才失败），说明**WiFi 链路会偶发抖动** —— 抖一下就永久卡死，这是不可接受的。
    ⇒ v9.1 的读法 = **v8 的非阻塞骨架 + v8.1 的语义修正 + 每次 recv 前重设非阻塞**：
        ① **仍然绝不 settimeout**（那是六~八轮卡死的根因，v9 已验证）；
        ② 改用 `setblocking(False)` + 轮询：recv 没数据时立刻返回 ⇒ **结构上不可能挂死**；
        ③ **每次 recv 之前都重新 `setblocking(False)` 一次** —— 因为 v8.1 是
           "setblocking → sleep(0.25) → recv" 卡死的，高度怀疑**这块固件的
           `setblocking` 会被 `time.sleep()` 冲掉**。把重设放在紧邻 recv 的位置，
           中间不夹任何 sleep，就把这条路堵死了（这是 v9.1 相对 v8.1 唯一的关键差别）；
        ④ 空返回**不再当 EOF**：只有"已经读到过字节"之后再连续空 3 次才算响应发完；
        ⑤ 加**时间预算**（`HEAD_BUDGET_*`）：轮询超过预算就放弃，打印"已收 N 字节"，
           这一轮照常结束，绝不把整台板子拖住。
    ⚠️ 如果这次还是卡，那就只剩一种可能：`setblocking(False)` 紧邻 recv 也无效。
       先跑 `k230_probe.py` 的 **C8**（它专测这个）再改代码。

【v9.2 修正 2026-10-01】第十次上板：**不是 socket 的问题，是计时的问题**。
    v9.1 上板后 6 个请求**全部**报同一句：
        [http] 读头超时：已收 0B，空转 0 次（预算 4.0s 用尽）
    而且都发生在"发完只过了 0.3s"的时候，全程**没有**出现 `!! setblocking 不可用`。
    "空转 0 次" ⇒ 循环在**第一次判断**就 break 了 ⇒ **一次 recv 都没执行过**
    （所以根本轮不到 setblocking/settimeout 表现，也就读不到任何字节）。
    根因：预算计时用的是 `time.time()`。板上它返回 RTC epoch ≈ 276111872（**秒**），
        这个量级下浮点分辨率约 32 秒 ⇒ `time.time() + 4.0` 被**抹平成 `time.time()` 本身**
        ⇒ `time.time() >= deadline` 第一次判断就成立 ⇒ 立刻"预算用尽"。
        （对照：v9 没有预算循环所以能读通；v9.1 补了预算循环，反而全灭。）
    ⇒ v9.2 只改计时：**预算 / 排程一律走单调时钟 `ticks_ms` + `ticks_diff`**，
        不再拿 `time.time()` 做短间隔算术（它只留给 `_BOOT_TAG` 那种
        "需要每次开机都不同"的场合）。
    ⚠️ 同一类坑还有一处：`next_poll = now + CMD_POLL_INTERVAL`（浮点秒）同样会被抹平
        ⇒ 指令轮询间隔归零、主循环空转。已改成毫秒整型 `CMD_POLL_MS` / `LOOP_INTERVAL_MS`。
    ⚠️ 这条**离线测不出来**：PC 的 CPython 是双精度，`t0 + 4.0` 精确无误；
        而旧测试里那条 `'time.time() >= deadline' in code` 的断言还把 bug 钉住了。

【v9.3 现场自检 2026-10-01】第十一次上板：**计时修复有效，但主脚本换了个地方挂**。
    ✅ probe v9.2 下 B2~B6 **五条全通**（全部 `recv #1 ...（空转 0 次）`，130/116/865/161/139 字节）
       ⇒ `ticks_ms` 修复是对的，"不碰相机"的干净环境里读头毫无问题。
    ❌ 但正式脚本（banner 确认 v9.2）**又挂**，而且这次 **USB 直接断开（"叮咚"）**：
           [http] 已发出 301B（已带 brief 头），先睡 0.35s 再非阻塞读响应头...
           ← 之后再无一行
       注意第九轮 v9 纯阻塞卡死时**没有**掉线 ⇒ 这次很可能是**固件崩了/复位了**。
    ⚠️ 这组日志"说不通"的地方（正是要钉的点）：
       ① `_read_head` 在 report 里是 `verbose=True` 调的，成功必打 `recv #1`、
          失败必打 `读头超时` —— **两条都没有** ⇒ 不是"预算用尽"，而是**卡在 recv 里**；
       ② 同一次运行里**前面那次 `poll_command` 的读头是静默成功的**
          （v9.1 会打「读头超时 + 响应头不完整」，v9.2 这两行都没出现）
          ⇒ 同一份代码、同一个进程里，recv 一会儿有效一会儿挂死；
       ③ probe 用**同一套 `_read_head`** 却 5 次全通。
       ⇒ 唯一没验过的差异变量 = **相机 + KPU + 一轮推理在场**。
    ⇒ v9.3 只做两件事（不改读法本身）：
       ① `SELFTEST_SOCK`：相机/KPU 就绪后、主循环前，跑一次"故意不发请求"的连接 +
          `sleep → setblocking(False) → recv`（= 探针 C8d）—— 把这个变量单独拎出来测；
       ② `_read_head` 加一行「遗言行」：第一次 recv 之前打印
          setblocking 返回值 / 已睡秒数 / mem_free —— 万一再挂，日志最后一行就能定性。

【★ v10 换方向 2026-10-01】第十二次上板：**不再修"读响应"，直接不用它**。
    v9.3 的证据把嫌疑收到了一点（完整日志见 README §7）：
        ✅ probe `net` 5/5、`sock` C1~C8 全通（含 C8a/C8d/C8c/C8b 四条）
           ⇒ **socket 栈健康**；而且 **`sleep` 并没有冲掉 `setblocking`**
             （C8b 通过）—— v9.1 那条"规则③"是伪命题，可以彻底放下。
        ✅ 主脚本 `[selftest]` 也通过（`recv 返回 b''`）
           ⇒ **相机 + KPU 在场、但只要没跑推理**，非阻塞 recv 也有效。
        ❌ 但启动时那次 `/command` 轮询（推理**前**）读响应成功，
           而 `[infer]` 之后的 `report()` 读响应**再也没回来**。
        ⇒ 唯一对得上的解释：**跑过一次 KPU 推理之后，这块板子的读响应会挂死/崩掉**
          （不是 HTTP 太重、不是内存不够、不是代码写错 —— 都验过了）。
    ⇒ 既然"等一个响应"就是挂死点，那就**把它从设计里删掉**（v10 两条改动）：

    ① **结论上报 = 只发不读**（`report()`）：
       connect → send → close，**一个 recv 都没有**。
       不需要 record_id（服务端按 `client_uid` 自己配对），
       也不需要读 429/duplicate —— 收不到就算了，下一轮马上又来。
       ⚠️ 代价：板端**无法知道服务端收没收**。这是刻意的取舍：
          "能不能读到响应"在板子上不可靠，"发出去"一直可靠（历史日志里服务端全是 200）。
    ② **现场图 = 原始 TCP 单向推送**（`send_image_raw()`）：
       连服务端的 **RAW_PORT**，发 `EIMG1 <uid> <nbytes> <source>\\n` + **原始 JPEG 字节**，
       然后直接关连接。服务端只收不回（见 Python_Backend/utils/edge_raw.py）。
       - 不走 base64：省 33% 流量 + 省一次 b64 编码（CPU 和一份大字符串）；
       - 不走 HTTP：不用等响应、不用 Content-Length 来回；
       - **全程零 recv** ⇒ 结构上不存在"读挂死"这个结果。
    ⚠️ 还保留的东西（别顺手删）：
       - `report_image()` / `grab_shot_base64()` 保留但默认不用（`IMAGE_TRANSPORT` 切回 "http"
         时才会走），万一原始 TCP 通道被网络环境挡住还能退回去。
    【v15 2026-10-02】原第 1 条 `poll_command()` **已删除**（「立即抓拍」整条链下线），
      板端现在**没有任何"服务端→板端"的下行方向**，只有往外发。
    ⚠️ **服务端必须先起来**（原始 TCP 端口由 app.py 在第一个请求时开）：
       板端第一轮会先 POST 结论（触发服务端起监听），紧接着才推图，顺序天然对上。
"""
import os
import time
import gc

import network
import ujson
import nncase_runtime as nn
import ulab.numpy as np
# v14：弃用 libs.PipeLine（它只配 CHN0+CHN2，且 create() 末尾就 sensor.run()，
#      run 之后补配 CHN1 不生效 —— p13 变体 A 实测 failed(3)）。
#      改照官方 02.Basic/19.snapshot.py + 13.training/det_video.py 的**裸 Sensor 三通道**。
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2
from media.display import Display
from media.media import MediaManager

try:
    import usocket as _socket               # 板端：MicroPython 的 socket
except ImportError:
    import socket as _socket                # PC 端跑离线自测时走这里

try:
    import ubinascii                       # 确诊时把 JPEG 转 base64 用
except ImportError:                        # 个别固件叫 binascii
    import binascii as ubinascii

# ------------------------------------------------------------------
# ★★ 单调时钟（2026-10-01 第十次上板定位到的根因，别再改回去）
# ------------------------------------------------------------------
# ⚠️⚠️「预算 / 超时」这类**短间隔算术一律用 ticks_ms，绝不用 time.time()**：
#    板上 time.time() 返回的是 RTC epoch ≈ 276111872（**秒**）。这个量级下浮点分辨率
#    约 32 秒 ⇒ `time.time() + 4.0` 会被抹平成 `time.time()` 本身
#    ⇒ `time.time() >= deadline` 第一次判断就成立
#    ⇒ 读响应头的循环**一次 recv 都没做**就报"预算用尽 / 已收 0B / 空转 0 次"，
#      六个请求全灭、一个字节都读不到。
#    （对照：v9 没有预算循环，所以能读通；v9.1 加了用 time.time() 数的预算，直接全灭。）
#    ticks_ms 是小整数、单调、不受 RTC 同步跳变影响，加减精确。
try:
    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
except AttributeError:                     # PC 端 CPython 没有 ticks_ms
    def _ticks_ms():
        return int(time.time() * 1000)

    def _ticks_diff(a, b):
        return a - b

# ------------------------------------------------------------------
# 配置区：换 WiFi / 换服务器只改这里
# ------------------------------------------------------------------
WIFI_SSID = "ka"
WIFI_PASS = "kaito7777"

SERVER_URL = "http://192.168.57.97:5000/api/edge/disease"
# 接口根（下面几个地址都从它推导，换服务器只改上面 SERVER_URL 一行）
API_BASE = SERVER_URL.rsplit("/", 1)[0]
# 图片补传地址：自动推导，不用两处改
IMAGE_URL = API_BASE + "/image"
# 兜底取 record_id 的地址：读不到 POST 响应头时用它（GET 通道实测每次都通）
# ⚠️ v10 起**默认不再使用**（结论改成只发不读，也就没有"读不到响应头"这回事）；
#    保留是为了 `IMAGE_TRANSPORT="http"` 的退路还能跑。
LAST_RECORD_URL = API_BASE + "/last-record"
SOURCE_ID = "k230-canmv"

# ---- 原始 TCP 图片通道（v10）----
# 为什么要有：跑过一次 KPU 推理之后，这块板子的"读响应"会挂死（十二轮实测）。
# 图片走单向 TCP —— 连上、发头行 + 原始 JPEG、关连接，**一个 recv 都不做**。
# ⚠️ 端口必须与服务端 config.EDGE_RAW_PORT 一致（默认 5001）。
#    主机名从 SERVER_URL 里推导，换服务器只改上面一行。
_RAW_HP = SERVER_URL.split("://", 1)[-1].split("/", 1)[0]
RAW_HOST = _RAW_HP.split(":")[0]
RAW_PORT = 5001

# 图片传输方式：
#   "raw"  = 原始 TCP 单向推送（v10 默认，无 recv、无 base64 —— 推荐）
#   "http" = POST /api/edge/image（base64，**需要读响应**；只在 raw 被网络挡住时退回）
IMAGE_TRANSPORT = "http"   # ★ 2026-10-02 方案 B：板端直接 POST /api/edge/image（暂别串口；退路见文件头）

# ==================================================================
# ★★★ 方案丙（2026-10-01 第十三次上板定论）：板子**不开 socket**，走 USB 串口
# ==================================================================
# 为什么要有（把前面十几轮的结论收敛成一句）：
#     跑过一次 kpu.run() 之后，这块板子的**网络整体不可用**——
#       v9.x   死在「读响应」（日志停在「已发出 … 再非阻塞读响应头」）
#       v10/v10.1 死在「建连」（日志停在「[http] connect …」，连固件的
#                 `connect success` 都不再打印），随后 **USB 掉线、板子复位**
#     同一段 connect 代码，0.12 秒前的 [selftest] 刚成功过；崩点还一路往前漂。
#     ⇒ 病根不是某一行，是「推理之后不能用网」。那就**一个 socket 都不开**。
#
# 做法：结果和图片全部 `print()` 到 USB 串口（就是 IDE 那根 Type-C 线），
#      由 PC 侧 `serial_relay.py` 假冒板子去 POST 服务端 —— **服务端一行都不用改**。
#      板端从此**没有 network、没有 lwip、没有等响应**，那条因果链整根拔掉。
#
#   TRANSPORT = "serial" —— 默认。板子只做「抓拍 + 推理 + 打印」，零网络。
#   TRANSPORT = "http"   —— 老路（v10：只发不读 + 原始 TCP 推图）。
#                           留着是为了串口链路万一被环境挡住时还能退回来。
#
# ⚠️ 串口模式下这些**全部不启用**：
#      wifi_connect / flush_pending / push_pending / selftest_socket
#    —— 没有网络可用；补传队列也没意义（串口不报"发失败"）。
#      （原先这里还列着 poll_command：v15 起「立即抓拍」整条链已删除，见文件头。）
TRANSPORT = "http"         # ★ 2026-10-02 方案 B：改回板端直接上报（只发不读 + base64 图片）
                           #    回退方案丙：这行改回 "serial"，并把 IMAGE_TRANSPORT 改回 "raw"
SERIAL_B64_CHUNK = 512    # 每个 @@D 行的 base64 字符数（必须是 4 的倍数）

KMODEL_PATH = "/sdcard/model.kmodel"
LABELS_PATH = "/sdcard/labels.txt"
SHOT_PATH = "/sdcard/_edge_shot.jpg"      # 兜底编码时的临时图（每轮覆盖）

IMG_SIZE = 224
LOOP_INTERVAL = 10        # 自动巡检间隔（秒）
TICK = 1.0                # 主循环心跳（秒）：让掉线重连 / 异常恢复能及时介入
# ⚠️★ 排程算术一律用**毫秒整数**（ticks_ms 不能和浮点秒混算，见文件头「单调时钟」）：
#     `time.time() + 2.0` 这种写法在板上会被抹平 ⇒ 间隔直接归零、主循环空转。
LOOP_INTERVAL_MS = LOOP_INTERVAL * 1000       # 10000
MAX_PENDING = 20          # 断网时最多缓存多少条（只缓存结论，不含图）
# ⚠️⚠️ 显示模式：填错不会报错，但 get_frame()/snapshot() 会**无限卡死**（见 README §7.1）。
#     "virt" = IDE 缓冲区显示 —— **没接任何物理屏时就用这个**（画面在 CanMV IDE 里看）
#     "lcd"  = MIPI 小屏（ST7701），要真插着屏
#     "hdmi" = HDMI 显示器（LT9611），要真接显示器
#     **没有 "none" 这个值**（老注释写错过）。
#   原因：显示层没人消费 → CHN0 吐不出帧 → 整条 ISP 流水线堵住 → AI 的 CHN2 也拿不到帧。
DISPLAY_MODE = "virt"
SHOT_W = 640                 # CHN0(显示)/CHN1(拍照) 的尺寸；CHN2(AI) 用 IMG_SIZE
SHOT_H = 480

# ---- 「发完先睡多久」+「最多轮询多久」（v9.1）----
# ⚠️⚠️ 改这里之前先读文件头 v9 / v9.1 两段：
#    `sock.settimeout()` 一调用，这块固件上的 recv 就被挂到 uselect.poll 上，而 poll 不可靠
#    —— 表现就是"卡死"或"读到 0 字节"。所以 v9 起**一个超时都不设**。
#    settle  = 发完先睡多久（让服务端把响应写进接收缓冲区，"读已缓冲数据"100% 可靠）
#    budget  = 之后最多非阻塞轮询多久（防止"响应压根不来"时无限空转）
#    ⚠️ 两者的关系：正常情况 settle 睡完，第一次 recv 就拿到；budget 只在异常时起作用。
#    ⚠️ budget 不能靠 settimeout 实现（那会走坏路径），只能靠 ticks_ms（单调时钟）自己数。
HTTP_SETTLE = 0.35        # 结论上报：小包，服务端要多写一次库
IMG_SETTLE = 0.80         # 图片补传：上行 ~几十 KB，服务端要收完再回，多睡一会儿

HEAD_BUDGET = 4.0         # 结论上报：读头轮询的最长预算（秒）
IMG_BUDGET = 8.0          # 图片补传：包大，给宽一点
# ⚠️ 为什么预算不能省（第九次上板教训）：没有预算 + 阻塞 recv = **响应不来就永久卡死**。
#    v9.0 就是这么卡住的（WiFi 抖一下，那一轮直接死在那儿，必须人工断电）。

# ---- v9.3：启动时的 socket 现场自检（只跑一次）----
# 为什么要有（第十一次上板）：
#   probe 在**不碰相机**的干净环境里 B2~B6 五条全通；但正式脚本（相机+KPU 都在跑）
#   在第 1 轮上报时**卡在 recv 里出不来**（日志停在 "已发出 301B…" 之后再无一行，
#   随后 USB 直接断开）。同一份 `_read_head`、同一次运行里 poll_command 还刚成功过，
#   两者唯一没验过的差异变量就是「**相机 + KPU + 一轮推理在场**」。
#   ⇒ 在相机/KPU 就绪之后、主循环之前，用**正式脚本自己的** _set_nonblocking 跑一次
#     "故意不发请求"的连接 + recv（= 探针的 C8d）：
#         连接后一个字节都不发 ⇒ 服务端在等请求行、绝不会回数据 ⇒
#         recv 立刻返回 b'' = 非阻塞有效；卡住 = 就是这个变量的锅。
#   验证完可以改成 False（省约 1 秒启动时间）。
SELFTEST_SOCK = False      # ★ 方案 B 先关掉：它是 http 模式才跑的 no-op 探针，只往日志里塞噪音

# ---- v7：跟服务端要「极短响应」----
# 2026-10-01 第五次上板定论：板端能不能跑通，**由响应体积决定**——
#     GET  /api/edge/command  响应 253 字节 → 每次都通
#     POST /api/edge/disease  响应 933 字节 → 每次都卡（把读块从 256 改到 1024 也没用）
# 所以带上这个头，服务端会把 body 从 660 字节压到 8 字节（信息全在 X-Edge-* 头里），
# 整个响应约 185 字节 —— 稳稳落在"已证明能通"的 253 以内。
EDGE_BRIEF = True

# ---- 现场图回传 ----
# 2026-10-01 起服务端改成"拍到就入库"，每条记录都值得配一张现场图
# （尤其未确诊的那些，没有图根本没法判断设备到底拍到了什么）。
# 所以这里不再只在确诊时抓拍，而是**每轮都抓**。流量代价：约 30KB/轮，
# 10 秒一轮 ≈ 3KB/s，局域网可接受；真要省就在下面关掉。
SEND_IMAGE = True         # 每轮上报成功后，是否补传一张现场图
# ⚠️ 单张图 base64 的硬上限。为什么要有：K230 上行 socket 走核间通信，大包不稳，
#    实测 27 万字符（≈198KB JPEG）一次 POST 会让 urequests 拿不到响应、
#    报 "list index out of range"。这里从"最小够用"出发逐级压缩，超上限就干脆不带图。
#    数值按 step_test 的 STEP 5.5 实测结果调整，别凭感觉调大。
IMAGE_MAX_B64 = 60000
# ⚠️ v10：原始 TCP 通道不传 base64，所以另有一个"原始字节"上限（= 60000*3/4）。
#    两个数字表达的是同一张图的体积，改一个记得同步另一个。
IMAGE_MAX_RAW = IMAGE_MAX_B64 * 3 // 4      # 45000 字节原始 JPEG
# 压缩阶梯：**只降质量，不降分辨率**，从前往后试，够小就停。
# ⚠️ 2026-10-02 v12：**缩放阶梯已整体删除**（原 IMAGE_SCALE_LADDER = (0.5,0.25,1.0)）。
#    为什么：抓拍走 CHN0 的 YUV420SP，任何 image 侧转换/缩放都会让板子**直接掉电**
#    —— 带参数和无参数的 to_rgb888() 都实测过（P9 / P10），不是缩放参数的锅。
#    P12 H1 当时判成"不做任何转换直接编码就能出合法 JPEG" ——
#    ⚠️ 2026-10-02 **上板已推翻**：出的字节魔数合法，但内容是 RGB565 花屏
#    （原因与判据见 `_encode_jpeg()` 的说明）。抓拍"snapshot → 编码两行、零转换
#    零缩放"这个**结构是对的**，错的只是"交给哪个 API 去编"。
#    于是抓拍 = snapshot → 编码，两行，零转换、零缩放。
# ⚠️ 为什么质量阶梯**必须留着**：P12 实测这帧 640×480 q60 就要 **39430 字节**，
#    占 IMAGE_MAX_RAW(45000) 的 87.6% —— 画面一复杂就会越限。质量阶梯是唯一的刹车。
IMAGE_QUALITY_LADDER = (60, 40, 25)
# 排查用：True 时把最终要发出去的那张图另存到 SHOT_DEBUG_PATH，方便拔卡肉眼看
SHOT_DEBUG_SAVE = False
SHOT_DEBUG_PATH = "/sdcard/_shot_last.jpg"

LOCAL_CONFIRMED = 75.0    # 仅用于本地判断"要不要带图"；权威判定在服务端

# ---- 版本标识（别删：上板排查时靠它一眼认出跑的是不是最新版）----
# 2026-10-01 第三次上板踩的坑：日志停在 `[report] POST 上报中…` 之后，
# 但仓库里**没有任何 .py 会打印日志里那行 `connect success`** —— 高度怀疑
# 板上跑的还是旧脚本（拷漏 / IDE 里跑的是打开已久的旧缓存）。
# 所以启动头几行必须能自证版本，省得拿着旧版日志分析新版代码。
SCRIPT_VERSION = "v15-no-capture"
SCRIPT_BUILD = "2026-10-02 16:50"


def boot_banner():
    print("=" * 52)
    print(" k230_classify.py  %s   build=%s" % (SCRIPT_VERSION, SCRIPT_BUILD))
    print(" ★ 传输方式 TRANSPORT=%s" % TRANSPORT)
    if TRANSPORT == "serial":
        print(" ★ v11【方案丙】：板子**不开任何 socket** —— 结果/图片全走 USB 串口")
        print("   @@R 结论 / @@B@@D@@E 图片(base64) -> PC 侧 serial_relay.py 代发")
        print("   服务端不变；PC 侧必须同时跑 app/AI_Model/k230/serial_relay.py")
    else:
        print(" v10：结论**只发不读**；图片走**原始 TCP 单向推送** —— 全链路不再等响应")
        print(" HTTP：自实现；**不 settimeout**（避开 uselect.poll），setblocking(False) 轮询读头")
        print(" v9.3：新增「相机/KPU 在场」的 socket 现场自检（本轮仍保留，可关）")
    print(" ★ 看不到这几行 = 板上是旧脚本，先重新拷贝再谈别的")
    print(" ★ v11.2：关键路径打印带 %dms 节流（硬复位会丢输出缓冲区尾巴；"
          "加了它「日志停在哪 = 死在哪」才成立，见 _say）" % FLUSH_PACE_MS)
    # 打印本文件自身大小：贴在 /sdcard 跑时可以直接和 PC 上的字节数比对。
    # 从 CanMV IDE 直接运行是没有 __file__ 的（正常现象，不是错误）。
    try:
        print(" 本文件 %s  %d 字节" % (__file__, os.stat(__file__)[6]))
    except NameError:
        print(" 从 IDE 运行（没有 __file__，正常）—— 靠上面两行版本号自证")
    except Exception as e:
        print(" 本文件路径不可读（%s）—— 不影响运行" % type(e).__name__)
    print("=" * 52)


# ------------------------------------------------------------------
# 极简 HTTP 客户端（v6：不用 urequests）
# ------------------------------------------------------------------
# ⚠️⚠️ 为什么自己写（2026-10-01 上板实测两次卡死后定的，别改回 urequests）：
#   ① `timeout=` 在 CanMV 上**不生效**。实测：记录已经写进服务端库了
#      （id=72 与日志里的 27.4% 完全对上），板子却卡死复位，日志停在
#      `[report] POST 上报中…` 的下一行 —— 异常根本没抛出来，超时形同虚设。
#   ② 它读响应体用 `sock.read()`（无参 = **一直读到 EOF**）。服务端 keep-alive
#      或半关闭状态下这会永久阻塞；这也解释了为什么 63~72 那批记录 image_path 全空
#      （卡在结论的响应读取上，压根跑不到抓拍那一步）。
#   ③ 异常被吞，看不出卡在哪一步。
#
# v6 的做法：显式 `settimeout` + HTTP/1.0 + **读完响应头就立刻关连接，绝不读 body**。
#   * 响应头以 `\r\n\r\n` 结尾，有**明确终止符**，读到就停 —— 从机制上不可能挂死；
#   * 关键结果（record_id / verdict / accepted / cmd）由服务端**镜像到响应头**
#     （见 blueprints/edge.py 的 `_edge_json`），所以不读 body 也不丢信息；
#   * 请求用 HTTP/1.0：服务端不会用 chunked 编码回，语义最简单。
def _split_url(url):
    """'http://host:port/a/b' -> ('host', port, '/a/b')"""
    rest = url.split("://", 1)[-1]
    hostport, _, path = rest.partition("/")
    host, _, ps = hostport.partition(":")
    return host, (int(ps) if ps else 80), "/" + path


def _send_all(sock, data):
    """确保整包发完。MicroPython 的 socket 一般有 sendall，没有就退回循环 send。"""
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


# ⚠️⚠️ v9 命门（动读响应这段之前，务必把下面几行读完）
#
# 八轮上板，"读响应"这一步的结论链**每一版都被下一版证伪**，按时间顺序：
#   第一轮  DISPLAY_MODE 填错（与读响应无关，是相机流水线）        ✔ 真修好
#   第二轮  urequests 读 body（无参 read = 读到 EOF）              ✔ 换自实现 HTTP
#   第三轮  "板上跑的不是新版"                                     ❌ 被第四轮证伪
#   第四轮  `recv(256)` 差 15 字节跨块                             ❌ 被第五轮证伪
#   第五轮  响应体积（933B 卡 / 253B 通）                          ❌ 被第六轮证伪
#   第六轮  "GET 通、POST 卡"⇒ 猜"数据到达时机" ⇒ v8 非阻塞轮询    ❌ 被第七轮证伪
#   第七轮  "空返回 ≠ EOF"⇒ v8.1 先睡再轮询                        ❌ 被第八轮证伪（又卡死）
#
# 只有把"三次失败版本的代码"摆在一起看，共同点才浮出来：
#        v6 / v7   sock.settimeout(6)                  → 卡死
#        v8        settimeout(6) + setblocking(False)  → 不卡，但每次都读回 0 字节
#        v8.1      同上 + 发完先睡 0.25s               → 又卡死
#     **三次全都调了 `sock.settimeout()`。**
# 而唯一成功的那次（CanMV REPL 里手敲的对照实验）**没调**：
#     s = socket.socket()  → connect → sendall → time.sleep(2) → s.recv(2048)
#     -> b'HTTP/1.1 200 OK ... X-Edge-Record-Id: 103 ... {"ok":1}'   ← 一次拿全
# 另外，从第二轮起就写着"settimeout 不生效"，只是当时把它当成"超时没兜住"，
# 没想到它是**根因**：这块固件上一设超时，`recv()` 就改走 `uselect.poll` 那条路，
# 而 poll 在这种语义下不可靠 —— 没数据时它既不老实等待、也不老实报可读。
#
# ⇒ v9.1 的铁律（别再改回去）：
#     **绝不 settimeout、绝不用 uselect**（六~八轮的坑都在这里，v9 已验证）；
#     读响应用 **setblocking(False) + 轮询**，而且**每次 recv 之前都重设一次非阻塞**。
#     connect 交给固件自己的超时；读 = "先睡够让数据躺进缓冲区，再非阻塞取走"。
#     ⚠️ v9.0 只用"阻塞 recv"是被第九次上板证伪的：WiFi 一抖、响应不来，那一轮就**永久卡死**。

_NB_MODE = None          # None=未探测 / True=非阻塞可用 / False=setblocking 不可用


def _set_nonblocking(sock):
    """把 socket 切到非阻塞；成功返回 True。

    ⚠️ 为什么**每次 recv 之前都要重新调一次**（v9.1 的关键，别优化掉）：
        v8   的写法 = `setblocking(False)` → **立刻** recv  ⇒ 不卡，但读到 0 字节
        v8.1 的写法 = `setblocking(False)` → `time.sleep(0.25)` → recv ⇒ **卡死**
        两者唯一的差别就是中间那个 `time.sleep()` ⇒ 高度怀疑
        **这块固件的 `setblocking` 会被 `time.sleep()` 冲掉**。
        所以 v9.1 把重设放在**紧邻 recv** 的位置，中间不夹任何 sleep。
    ⚠️ 这里**不是 settimeout**：settimeout 会让 recv 走上 uselect.poll，那是六~八轮的根因。
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


def _read_head(sock, settle=HTTP_SETTLE, verbose=False, limit=1024, budget=HEAD_BUDGET):
    """先睡 `settle`，再用 **setblocking(False) 轮询**把响应头读完（读到 `\\r\\n\\r\\n` 停）。

    v9.1 读法 = v8 的非阻塞骨架 + v8.1 的语义修正 + 「每次 recv 前重设非阻塞」：
        ① **绝不 settimeout / uselect**（六~八轮的根因，v9 已验证它是坏的）；
        ② recv 前 `setblocking(False)` ⇒ 没数据时**立刻返回**，结构上不会挂死；
        ③ ⚠️ **空返回 ≠ EOF**：这块固件的非阻塞 recv 在"此刻没数据"时返回**空字节**
           （而不是抛 EAGAIN），与"对端关闭"完全同形。所以只有**已经读到过字节之后**
           再连续空 3 次，才算响应发完；
        ④ 轮询有 `budget` 秒上限（用 ticks_ms 单调时钟自己数，**不是 socket 超时**），
           到点打印"已收 N 字节"正常返回 —— 这一轮废了，但板子活着，下一轮照常。

    日志判读（上板一眼分清）：
        `recv #1 -> 3xx B，含空行=True`     → 正常（settle 睡完一次拿全）
        `recv #N ...（空转 K 次）`          → 响应"边等边到"，轮询兜住了
        `读头超时：已收 0B，空转 N 次`       → 响应压根没来（网络抖动 / 服务端问题）
        `!! setblocking 不可用`             → 这块固件连非阻塞都不给（会明说）
        `开始非阻塞 recv（…）` 之后**再无输出**  → ★ 卡在 recv 里（v9.3 的"遗言行"，
                                               它把"预算用尽/抛异常"两种可能直接排除）
    """
    global _NB_MODE
    if settle is None:
        settle = HTTP_SETTLE
    if budget is None:
        budget = HEAD_BUDGET
    if settle > 0:
        time.sleep(settle)          # 只等这一下：等的是"服务端已经在写响应"

    # ★★ 预算计时必须用**单调时钟 ticks_ms**，绝不能用 time.time()（见文件头）：
    #    板上 time.time() ~2.7e8 秒，浮点在这个量级分辨率 ~32 秒
    #    ⇒ `time.time() + budget` 被抹平 ⇒ 第一次判断就"用尽"、一次 recv 都没做。
    t0 = _ticks_ms()
    budget_ms = int(budget * 1000)
    buf = b""
    n = 0            # 成功读到数据的次数
    spins = 0        # "没数据"的轮数（= 板端日志里的"空转"）
    quiet = 0        # 已收到字节之后的连续空次数
    while b"\r\n\r\n" not in buf:
        if _ticks_diff(_ticks_ms(), t0) >= budget_ms:
            print("[http] 读头超时：已收 %dB，空转 %d 次（预算 %.1fs 用尽）"
                  % (len(buf), spins, budget))
            break

        # ★★ v9.1 命门：**每一次 recv 之前**都重设非阻塞（sleep 可能把它冲掉）
        nb = _set_nonblocking(sock)
        if _NB_MODE is None:
            _NB_MODE = nb
            if not nb:
                print("[http] !! setblocking 不可用 —— 只能退回单次阻塞 recv（有挂死风险）")

        if n == 0 and spins == 0 and not buf:
            # ★ v9.3「遗言行」：只打一次。万一板子挂在 recv 里出不来，
            #   这一行就是日志最后一行 —— 直接排除"预算用尽"和"setblocking 抛异常"两种可能，
            #   并把内存状态一起留下（第十一次上板卡死时连这行都没有，只能靠猜）。
            print("[http] 开始非阻塞 recv（setblocking=%s，已睡 %.2fs，mem_free=%s）"
                  % (nb, settle, _mem_free()))

        try:
            piece = sock.recv(limit)
        except OSError:
            spins += 1
            time.sleep(0.05)
            continue
        except Exception as e:
            print("[http] recv 异常 %s: %s（已收 %dB）" % (type(e).__name__, e, len(buf)))
            break

        if not piece:
            if buf:
                # 已经读到过内容 ⇒ 这个空更可能是"响应发完了"（真 EOF）
                quiet += 1
                if quiet >= 3:
                    if verbose:
                        print("[http] recv 连续空 %d 次（响应已发完），累计 %dB"
                              % (quiet, len(buf)))
                    break
            spins += 1
            time.sleep(0.05)        # 唯一允许的等待姿势：睡一下再来轮询
            continue

        quiet = 0
        n += 1
        buf += piece
        if verbose:
            print("[http] recv #%d -> %dB，累计 %dB，含空行=%s（空转 %d 次）"
                  % (n, len(piece), len(buf), b"\r\n\r\n" in buf, spins))
        if len(buf) >= 4096:
            break
    return buf



def _build_req(method, host, port, path, body_bytes):
    """拼一个 HTTP/1.0 请求（`_http` 与 `_http_send_only` 共用，别各写一份）。"""
    req = ("%s %s HTTP/1.0\r\nHost: %s:%d\r\nConnection: close\r\n"
           % (method, path, host, port)).encode()
    if body_bytes:
        req += b"Content-Type: application/json\r\n"
        req += ("Content-Length: %d\r\n" % len(body_bytes)).encode()
    if EDGE_BRIEF:
        req += b"X-Edge-Brief: 1\r\n"       # 要极短响应（顺带省点字节，没坏处）
    req += b"\r\n" + body_bytes
    return req


def _http_send_only(method, url, body_bytes=None, verbose=False):
    """**只发不读**：connect → send → close，返回 True/False（是否成功发出）。

    v10 的核心（第十二次上板定论，改之前先读文件头的 v10 段）：
        "发"一直没问题（历史日志里服务端全是 200、记录都建了），
        挂死的永远是"读响应"。所以这条路径里**一个 recv 都不做**。
        服务端收不收得到、判成什么（429 / duplicate），板端一概不管 ——
        拿不到就算了，10 秒后下一轮照常来。

    与 `_http()` 的区别只有一个：不调 `_read_head`。
    其余（不 settimeout、HTTP/1.0、Connection: close、brief 头）完全一致，
    免得将来排查时又多出"两条不一样的通道"。
    """
    host, port, path = _split_url(url)
    if body_bytes is None:
        body_bytes = b""
    req = _build_req(method, host, port, path, body_bytes)

    sock = _socket.socket()
    _t0 = _ticks_ms()
    try:
        if verbose:
            _say("[http] connect %s:%d ...（只发不读）" % (host, port))
        sock.connect(_socket.getaddrinfo(host, port)[0][-1])
        if verbose:
            _say("[http] 已连接 (%.2fs)" % (_ticks_diff(_ticks_ms(), _t0) / 1000.0))
        _send_all(sock, req)
        if verbose:
            _say("[http] 已发出 %dB（brief=%s），发完即关，**不读响应**"
                 % (len(req), EDGE_BRIEF))
        return True
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _http(method, url, body_bytes=None, settle=None, verbose=False, budget=None):
    """发一个 HTTP/1.0 请求，**只读响应头**。返回 (code, headers_dict)。

    绝不读响应体：body 可能很大（图片接口 >100KB）且没有终止符保证；
    需要的信息都在响应头里。

    ⚠️ 参数是 `settle`（发完先睡几秒）+ `budget`（之后最多轮询几秒），**都不是 socket 超时**：
       这块固件上 `settimeout()` 会让 recv 走上不可靠的 uselect.poll（见 v9 命门）。
       所以本方法内部**一个 socket 超时都不设**，连 connect 也交给固件自己兜底；
       "限时"是靠 `_read_head` 里用 `ticks_ms`（单调时钟）数出来的（见 v9.1）。

    返回 (code, headers)，**两种失败分得很清**（调用方靠它决定要不要重发）：
        抛异常     —— 连不上 / 发不出去：请求没送达，可以安全重发（进补传队列）
                      ⚠️ 这块板子上"连不上"是真实存在的：probe 里出现过
                         `connect to server faild!` + `OSError: Errno 107 ENOTCONN`
                         （12~13 秒才失败）⇒ 调用方必须能接住并继续下一轮。
        (0, {})   —— 已发出但没读到响应头（含轮询超时）：**服务端很可能已经处理了**，
                     绝不能当失败重发（手动抓拍会绕过去重，重发 = 重复记录）

    verbose=True 时每一步印一行 —— 这是为"卡死"准备的：
        [http] connect 192.168.57.97:5000 ...
        [http] 已连接 (0.02s)
        [http] 已发出 267B（已带 brief 头），先睡 0.35s 再非阻塞读响应头...
        [http] recv #1 -> 268B，累计 268B，含空行=True（空转 0 次）
        [http] 响应头到达 268B (0.36s)
    万一还是卡：**日志停在哪一行就说明卡在哪一行**。
    默认 False（只有排查时手动打开），免得每轮刷屏。
    """
    host, port, path = _split_url(url)
    if body_bytes is None:
        body_bytes = b""
    if settle is None:
        settle = HTTP_SETTLE
    if budget is None:
        budget = HEAD_BUDGET
    req = _build_req(method, host, port, path, body_bytes)

    sock = _socket.socket()
    _t0 = _ticks_ms()
    try:
        # ⚠️ 这里**没有** sock.settimeout()，是刻意的（README §7.3）。
        if verbose:
            print("[http] connect %s:%d ..." % (host, port))
        sock.connect(_socket.getaddrinfo(host, port)[0][-1])
        if verbose:
            print("[http] 已连接 (%.2fs)" % (_ticks_diff(_ticks_ms(), _t0) / 1000.0))
        _send_all(sock, req)
        _t1 = _ticks_ms()
        if verbose:
            print("[http] 已发出 %dB%s，先睡 %.2fs 再非阻塞读响应头..."
                  % (len(req), "（已带 brief 头）" if EDGE_BRIEF else "", settle))
        head = _read_head(sock, settle=settle, verbose=verbose, budget=budget)
        if verbose:
            print("[http] 响应头到达 %dB (%.2fs)" % (len(head), _ticks_diff(_ticks_ms(), _t1) / 1000.0))
    finally:
        try:
            sock.close()
        except Exception:
            pass

    if b"\r\n\r\n" not in head:
        # ⚠️ 注意这里**不抛异常**：请求已经发出去了，服务端多半也处理了，
        #    抛异常会让调用方当成"失败"去重发（手动抓拍会绕过去重 → 重复记录）。
        #    用 (0, {}) 表示"已送达但没读到响应"，由调用方决定兜底动作。
        print("[http] 响应头不完整（%d 字节）—— 请求已发出，按“已送达”处理" % len(head))
        return 0, {}
    lines = head.split(b"\r\n\r\n")[0].split(b"\r\n")
    if not lines or b" " not in lines[0]:
        raise OSError("状态行异常: %r" % lines[0][:40])
    code = int(lines[0].split(b" ")[1])
    headers = {}
    for ln in lines[1:]:
        k, _, v = ln.partition(b":")
        if k:
            headers[k.strip().lower().decode()] = v.strip().decode()
    return code, headers


# ------------------------------------------------------------------
# v9.3：socket 现场自检（相机/KPU 在场时才测得出真假）
# ------------------------------------------------------------------
# ---- 打印节流（★ 排查用：硬复位会丢掉 USB 输出缓冲区里没刷出去的尾巴）----
# 为什么需要：前十几轮的日志都停在"半句话"上，我们一直把最后一行当成"真正的死点"。
# 但板子复位时缓冲区里没刷出去的内容会丢 —— 最后一行可能只是"来得及刷出来的那一行"。
# 关键路径（结论上报 → 抓拍 → 推图）每打一行停 FLUSH_PACE_MS，让 CDC 有机会刷出去，
# 这样"日志停在哪 = 死在哪"才成立。代价每行 30ms，相对 66 秒的推理可以忽略。
# ⚠️ **不要**把它用到 `_read_head()` 里：那边 recv 前必须紧邻地 setblocking(False)，
#    中间夹 sleep 会让离线测试 [13] 段判成"非阻塞被 sleep 冲掉"的违规。
# 设 0 = 完全关掉（回到加这个之前的行为）。
FLUSH_PACE_MS = 30


def _say(msg):
    """打印 + 停一下（给 USB-CDC 刷新缓冲的机会）。见 FLUSH_PACE_MS。"""
    print(msg)
    if FLUSH_PACE_MS > 0:
        try:
            time.sleep_ms(FLUSH_PACE_MS)              # 板端
        except Exception:
            try:
                time.sleep(FLUSH_PACE_MS / 1000.0)    # PC（离线测试里会被虚拟推进，不真睡）
            except Exception:
                pass


def _mem_free():
    """堆剩余字节数（拿不到就返回 '?'）。诊断行里带上它，方便判断是不是内存问题。"""
    try:
        return gc.mem_free()
    except Exception:
        return "?"


def selftest_socket():
    """相机/KPU 已在跑时，验一次"setblocking(False) + recv 还灵不灵"（v9.3）。

    ⚠️ 为什么必须等相机起来再测：probe 全程不碰相机，它在干净环境下 5/5 全通；
       而正式脚本偏偏在 recv 里卡住。两者唯一没验过的差异就是「相机 + KPU 在场」。
       不把这个变量单独拎出来，就又会重复前十轮"猜一个改一个"的老路。

    做法与 `_read_head` 的第一步**逐字对齐**（也就是探针的 C8d）：
        sleep(HTTP_SETTLE) → setblocking(False) → recv
    区别只在于**故意一个字节都不发** —— 服务端在等请求行，绝不会回数据，
    于是"非阻塞到底还有没有效"在一次 recv 里就分清了：
        返回 b''（或抛 OSError/EAGAIN）  → ✔ 非阻塞有效，卡点在别处
        卡住不返回                        → ✘ 就是这个变量在作祟

    本函数**故意不把 recv 包进 try**：要的就是让"卡住"原样暴露 ——
    日志最后一行就是结论。只用 finally 把 socket 关掉。
    """
    print("[selftest] socket 现场自检（相机/KPU 已在跑；故意不发请求，服务端不会回数据）")
    print("[selftest]   mem_free=%s" % _mem_free())
    host, port, _ = _split_url(SERVER_URL)
    s = None
    try:
        t0 = _ticks_ms()
        s = _socket.socket()
        s.connect(_socket.getaddrinfo(host, port)[0][-1])
        print("[selftest]   已连接 %s:%d（%.2fs）"
              % (host, port, _ticks_diff(_ticks_ms(), t0) / 1000.0))

        time.sleep(HTTP_SETTLE)      # ← 与 _read_head 的第一步完全一致
        print("[selftest]   已睡 %.2fs（与 _read_head 的第一步一致）" % HTTP_SETTLE)
        nb = _set_nonblocking(s)
        print("[selftest]   setblocking(False) -> %s（%.2fs）→ 立刻 recv(16)"
              % (nb, _ticks_diff(_ticks_ms(), t0) / 1000.0))
        if not nb:
            print("[selftest]   ！！setblocking 抛异常 —— 这块固件不给非阻塞")
            return
        tr = _ticks_ms()
        piece = s.recv(16)
        print("[selftest]   ✔ recv 返回 %r（%.3fs）→ **相机/KPU 在场时非阻塞有效**"
              % (piece, _ticks_diff(_ticks_ms(), tr) / 1000.0))
    except OSError as e:
        print("[selftest]   ✔ 抛 OSError(%s) → **非阻塞有效**（EAGAIN 语义）" % e)
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
    print("[selftest] 自检完毕（结论 = 上面最后一行）")


# ------------------------------------------------------------------
# WiFi
# ------------------------------------------------------------------
def wifi_connect(timeout=20):
    wlan = network.WLAN(network.STA_IF)
    if not wlan.isconnected():
        wlan.active(True)
        print("[wifi] 连接 %s ..." % WIFI_SSID)
        wlan.connect(WIFI_SSID, WIFI_PASS)
        t0 = _ticks_ms()
        while not wlan.isconnected():
            if _ticks_diff(_ticks_ms(), t0) / 1000.0 > timeout:
                print("[wifi] 连接超时")
                return None
            time.sleep(0.5)
    print("[wifi] 已连接 IP=%s" % wlan.ifconfig()[0])
    return wlan


def wifi_ready(wlan):
    return wlan is not None and wlan.isconnected()


# ------------------------------------------------------------------
# 标签
# ------------------------------------------------------------------
def load_labels():
    labels = []
    try:
        with open(LABELS_PATH) as f:
            for line in f.read().split("\n"):
                s = line.strip()
                if s:
                    labels.append(s)
    except OSError:
        print("[!] 读不到 %s" % LABELS_PATH)
    print("[labels] 加载 %d 类" % len(labels))
    return labels


# ------------------------------------------------------------------
# 预处理 / 推理
# ------------------------------------------------------------------
def preprocess(frame):
    """CHN2(RGBP888) 帧 -> kmodel 输入 **uint8** NHWC (1,224,224,3)，值域 0~255

    实测 CanMV v1.8 的 CHN2 to_numpy_ref() 返回 (3, 224, 224) —— CHW、**没有 batch 维**。
    （CHN2 已设 224x224，帧就是 224x224，不需要 ai2d 缩放；p13 探针回归 shape/dtype 一致）
    板上 ulab 没有 astype、transpose 不收轴参数（PC 端写法会上板即炸），
    改用官方示例的固定套路（无参 transpose + reshape + copy）：
        (C,H,W) -> reshape (C,H*W) -> transpose() -> (H*W,C)
                -> copy().reshape (H,W,C) -> reshape (1,H,W,C)   [保持 uint8，不除 255]
    兼容两种帧形状：3D (3,H,W) 和 4D (1,3,H,W)，都是 RGB planar。

    ⚠️ v4：**不要**再 `x / 255.0`。/255 已被 to_kmodel.py 烤进 kmodel
    （preprocess=True + input_range=[0,1]），板端除一次 = 除两次，精度直接崩。
    """
    sh = frame.shape
    if len(sh) == 4:                            # (1,3,H,W)
        c, h, w = sh[1], sh[2], sh[3]
    elif len(sh) == 3:                          # (3,H,W) —— v1.8 实测形状
        c, h, w = sh[0], sh[1], sh[2]
    else:
        raise ValueError("帧 shape=%s 无法识别，发这条报错给 AI" % (sh,))
    x = frame.reshape((c, h * w))               # (C, H*W)
    x = x.transpose()                           # -> (H*W, C)
    x = x.copy().reshape((h, w, c))             # -> (H,W,C)
    x = x.reshape((1, h, w, c))                 # 补 batch -> (1,H,W,C)
    # ⚠️ v4：这里**不做** /255 —— 归一化已由 kmodel 内部完成（input_range=[0,1]）。
    #    原样喂 uint8 0~255 即可。
    return x


def top1(row):
    best = 0
    for i in range(1, len(row)):
        if row[i] > row[best]:
            best = i
    return best


# ------------------------------------------------------------------
# 上报（带断网缓存重传）
# ------------------------------------------------------------------
_pending = []
_sensor = None   # 裸 Sensor 实例（v14 弃用 PipeLine；抓拍用 CHN1，AI 用 CHN2，显示用 CHN0）


def now_str():
    t = time.localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % (t[0], t[1], t[2], t[3], t[4], t[5])


def _grab_shot_raw(max_bytes):
    """抓一张现场图，压到 `max_bytes`（**原始 JPEG 字节**）以内；压不下来返回 (None, 描述)。

    v10 把抓拍主干抽到这里，两条通道共用同一份阶梯：
        `grab_shot_jpeg()`   -> 原始字节，走**原始 TCP**（默认）
        `grab_shot_base64()` -> base64，走 HTTP（退路）
    必须共用：否则"哪条通道能出图"会不一致，排查时又多一个变量。

    抓拍通道为什么是 CHN1（v14 定案，p13 探针 2026-10-02 上板实证）：
        CHN0 的 YUV420SP 在这版 image 模块里**没有任何可用的 JPEG 出口**：
        `to_jpeg()` 把它当 RGB565 打包 → 花屏（魔数/尺寸全合法，不报错）；
        `save()` 直接 `OSError: current format not support save function!`。
        CHN2 给 AI 的是 planar ndarray，不是 image.Image。
        CHN1 配成 **RGB565**（官方 02.Basic/19.snapshot.py 的存图范式），
        `_encode_jpeg()` 一次出**正常彩色**图 —— p13-B-q60 实测 15179 字节，
        判读指标：绿 0% / 品红 0% / 灰带 0 行（花图是绿 37~46% + 灰带 121 行）。

    ★★ v12（2026-10-02）—— 抓拍改成「snapshot → 直接编码」，**零转换、零缩放**：

        旧写法在转图那一步让板子**直接掉电**（无 Python 回溯、USB 掉线）：
            rgb = img.to_rgb888(x_scale=0.5, y_scale=0.5)      # P9 崩在这
            rgb = img.to_rgb888()                              # P10 无参也崩
        ⇒ 结论不是"缩放参数用错了"，而是「对 CHN0 的 YUV420SP 活帧做 image 转换」
        这条路整体不成立（K230 的 image 模块对 YUV420 几乎不支持任何处理）。
        官方源码里 to_rgb888() **只出现在读文件处**（`image.Image(文件)`），
        活帧从不用它；活帧要 RGB 官方是靠 `Sensor` 侧的 pixformat 或硬件 Ai2d。

        现在是 p13-B 实测出来的路（CHN1 RGB565 + 零转换 + 直接编码）：
            img = _sensor.snapshot(chn=CAM_CHN_ID_1)   # RGB565 image.Image
            raw = _encode_jpeg(img, q)                 # save → to_jpeg → compress
        实测 640×480 q60 = 15179 字节（画面简单时更小）。
        ⚠️ q60 在画面复杂时可能超上限 —— 所以**质量阶梯必须留着**当刹车。

    为什么要压体积（坑三，实测算出来的）：
        原图 1920x1080 q80 ≈ 198KB。这个体积推给 K230 的上行 socket 会出问题
        （历史上 urequests 直接报 "list index out of range"）。
    """
    # v5：整条链路分步埋点。板子卡死时日志会停在某一步上，
    # 一眼就能看出是 snapshot 卡了、还是编码卡了、还是网络卡了。
    _say("[shot] ① snapshot(chn=CHN1) 中…（mem_free=%s）" % _mem_free())
    try:
        img = _sensor.snapshot(chn=CAM_CHN_ID_1)   # image.Image（RGB565 通道）
    except Exception as e:
        _say("[shot] ① 抓拍失败（不影响结论上报）: %s: %s" % (type(e).__name__, e))
        return None, ""
    _say("[shot] ① 完成，原始帧 %dx%d（mem_free=%s）"
         % (_img_w(img), _img_h(img), _mem_free()))

    best = None
    best_desc = ""
    try:
        for q in IMAGE_QUALITY_LADDER:
            # ⚠️ 每一档都必须**重新取帧**：to_jpeg/compress 是**原地**把图变成 JPEG 的，
            #    同一个对象再编一次只会拿回上一次的结果（降质量会形同失效）。
            try:
                img = _sensor.snapshot(chn=CAM_CHN_ID_1)
            except Exception as e:
                _say("[shot] ② q%d 重取帧失败: %s: %s" % (q, type(e).__name__, e))
                break
            w, h = _img_w(img), _img_h(img)
            _say("[shot] ② 编码 q%d 中…（%dx%d，mem_free=%s）" % (q, w, h, _mem_free()))
            raw = _encode_jpeg(img, q)
            if not raw:
                _say("[shot] ② 编码 q%d 失败，换下一档" % q)
                del img
                continue
            if SHOT_DEBUG_SAVE and len(raw) <= max_bytes:
                _dbg_save(img)          # 存的就是即将发出去的那张
            del img
            if best is None or len(raw) < len(best):
                best = raw
                best_desc = "%dx%d q%d" % (w, h, q)
            if len(raw) <= max_bytes:
                _say("[shot] %dx%d q%d -> %d 字节 JPEG（上限 %d，采用）"
                     % (w, h, q, len(raw), max_bytes))
                return raw, best_desc
    except Exception as e:
        # 兜底：抓拍链路的任何异常都不该把整轮巡检带走
        _say("[shot] 抓拍链路异常（本轮不带图）: %s: %s" % (type(e).__name__, e))
        return None, ""

    if best:
        _say("[shot] 压到最小仍有 %d 字节（上限 %d），本轮不带图，只报结论（%s）"
             % (len(best), max_bytes, best_desc))
    else:
        _say("[shot] 取帧/编码全部失败，本轮不带图（跑 probes/p12_shot_channel.py 定位）")
    return None, ""


def grab_shot_jpeg():
    """抓一张现场图，返回**原始 JPEG 字节**（≤ IMAGE_MAX_RAW）；压不下来返回 None。

    v10 主用：`send_image_raw()` 直接把这段字节推给服务端 —— **不经过 base64**，
    省掉一次编码（CPU + 一份大字符串）和 33% 的流量。
    """
    raw, _ = _grab_shot_raw(IMAGE_MAX_RAW)
    return raw


def grab_shot_base64():
    """抓一张现场图，返回 base64 字符串（≤ IMAGE_MAX_B64）；压不下来返回 None。

    ⚠️ v10 起**默认不再使用**：这条路走 `POST /api/edge/image`，**需要读响应**，
    而"读响应"正是这块板子跑过推理之后会挂死的地方（见文件头 v10 段）。
    只在 `IMAGE_TRANSPORT="http"` 的退路里用。
    """
    raw, _ = _grab_shot_raw(IMAGE_MAX_RAW)
    if not raw:
        return None
    return ubinascii.b2a_base64(raw).decode().strip()


def send_image_raw(uid, jpeg):
    """把一张现场图用**原始 TCP 单向推送**给服务端（v10 主通道）。

    协议（服务端实现见 Python_Backend/utils/edge_raw.py）：
        EIMG1 <uid> <nbytes> <source>\\n
        <nbytes 个原始 JPEG 字节>

    发完立刻关连接 —— **服务端一个字节都不回，板端一个字节都不读**。
    这就是 v10 的全部意义：把"等响应"这个挂死点从图片链路里彻底删掉。
    （服务端也不回包，所以关连接时接收缓冲区是空的，不会 RST 掉刚发出去的数据。）

    返回 True 表示已发出（**不代表服务端已入库**，因为没读回执）；
    False 表示连不上/发不出 —— 本轮丢掉这张图，下一轮照常。
    """
    if not jpeg or not uid:
        return False
    header = ("EIMG1 %s %d %s\n" % (uid, len(jpeg), SOURCE_ID)).encode()
    _say("[shot] 原始 TCP 推图：%d 字节 JPEG -> %s:%d（发完即关，不读回；mem_free=%s）"
         % (len(jpeg), RAW_HOST, RAW_PORT, _mem_free()))
    sock = _socket.socket()
    _t0 = _ticks_ms()
    try:
        sock.connect(_socket.getaddrinfo(RAW_HOST, RAW_PORT)[0][-1])
        _say("[shot] 推图已连接（%.2fs），开始发 %d 字节…"
             % (_ticks_diff(_ticks_ms(), _t0) / 1000.0, len(jpeg)))
        _send_all(sock, header)
        _send_all(sock, jpeg)
        _say("[shot] ✔ 已推送（%.2fs）；服务端按 uid=%s 贴到刚入库的那条记录"
             % (_ticks_diff(_ticks_ms(), _t0) / 1000.0, uid))
        return True
    except Exception as e:
        _say("[shot] 推图失败（记录仍在，只是没图）: %s: %s" % (type(e).__name__, e))
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _img_w(img):
    try:
        return img.width()
    except Exception:
        return -1


def _img_h(img):
    try:
        return img.height()
    except Exception:
        return -1


# ★ v12（2026-10-02）：`_to_rgb_scaled()` **已删除**（原 1120-1164 行）。
#   它做的事是"对 CHN0 抓到的 YUV420SP 活帧调 to_rgb888()/to_rgb565() 转格式 + 缩放"，
#   而这条路在板上会让**板子直接掉电**：
#       P9  to_rgb888(x_scale=0.5, y_scale=0.5)  -> 掉电（无 Python 回溯）
#       P10 to_rgb888()   无参                    -> 也掉电
#       P12 H1 CHN0 不做任何转换，直接 _encode_jpeg -> ✔ 出"合法"JPEG（3 档都成）
#         ⚠️ 2026-10-02 上板推翻：**魔数合法 ≠ 内容对** —— 实为 RGB565 花屏。
#            所以 `_encode_jpeg()` 已改成 `save()` 优先，详见该函数说明。
#   ⇒ 不是缩放参数的锅，是这条路整体不成立。抓拍改走"snapshot → 直接编码"，
#     所以这里不再需要任何 RGB 转换 / 缩放函数。
#   ⚠️ 如果以后真要缩图，**别在 image 模块上缩**：要么在 PipeLine/sensor 侧定
#     `display_size`，要么照官方用硬件 2D 引擎 Ai2d。


def _dbg_save(img, path=SHOT_DEBUG_PATH):
    """把图另存到 TF 卡，方便拔卡/IDE 拉出来肉眼确认（排查花屏专用）。"""
    try:
        img.save(path, quality=80)
        print("[shot] 调试图已存 %s" % path)
    except Exception as e:
        print("[shot] 调试图保存失败: %s" % e)


def _encode_jpeg(img, quality=80):
    """把 image.Image 编成 JPEG bytes；按候选顺序挨个试，全不行返回 None。

    ★★ 2026-10-02 上板实证之后**顺序改了**：`to_jpeg` 不再排第一。

    ⚠️ 为什么（别再改回 `to_jpeg` 优先）：`to_jpeg()` 拿到 CHN0 的 YUV420SP 帧时
       **不报错、也不抛异常**，却把字节按 **RGB565** 直接打包 —— 产出的 JPEG
       魔数合法、尺寸也报 640×480，但画面是绿/品红花屏。实证图：
         `Python_Backend/uploads/edge_k230-canmv_20261002_125402_800632.jpg`
       判据：那次上板日志里**一条** `编码 xxx 不可用` 都没有 ⇒ 就是排第一的
       `to_jpeg` 干的，`compress` / `save` 压根没被碰到。
       算术也对得上：640×480 按 RGB565 装 = 614400 字节容量 > NV12 buffer
       460800 ⇒ 图的前 50% = Y 平面、接着 25% = UV 交错、最后 25% = 读越界，
       与花屏图上的三条带**完全吻合**。（也可以反证：IDE 预览是正常彩色的 ——
       显示层直连 CHN0 零拷贝，数据是好的，坏的只有 image 模块这个软件编码器。）

    所以先试 `save()`：官方唯一 snapshot 存盘范式用的就是它
    （`02.Basic/19.snapshot.py`：CHN1 + RGB565 → `img.save(path)`）。

    ⚠️⚠️ 魔数 `raw[:2] == b'\xff\xd8'` **只能证明"是个 JPEG"，证明不了"内容对"**。
        上一次就是栽在这条假阳性上（P12 的 H1 只看了"出没出字节、多少字节"，
        没人解码回来看一眼）。所以下面把「**是谁编的 + 多少字节**」打进日志 ——
        换没换生效、走的哪条分支，一眼可见。
    """
    for how in ("save", "to_jpeg", "compress"):
        try:
            if how == "save":
                try:
                    img.save(SHOT_PATH, quality=quality)
                except TypeError:
                    img.save(SHOT_PATH)          # 老固件 save() 不吃 quality=
                with open(SHOT_PATH, "rb") as f:
                    raw = f.read()
            else:
                out = getattr(img, how)(quality=quality)
                raw = bytes(out)
            if raw and raw[:2] == b'\xff\xd8':       # JPEG 魔数，确认编对了
                print("[shot] 编码 ✔ %s: %d 字节 (q%d)" % (how, len(raw), quality))
                return raw
            print("[shot] 编码 %s 返回的不是 JPEG（%s 字节），换下一种"
                  % (how, len(raw) if raw else 0))
        except Exception as e:
            print("[shot] 编码 %s 不可用: %s: %s" % (how, type(e).__name__, e))
            continue
    return None


# ------------------------------------------------------------------
# record_id 兜底（v7 / v9）
# ------------------------------------------------------------------
# 背景：板端读 POST 响应头这条路一直不稳（见 _read_head 上面那段 v9 命门），而图片补传
# 原先必须要 record_id。两条兜底：
#   ① v7：GET /api/edge/last-record（小包，最稳）把 id 捞回来；
#   ② v9：还捞不到就只带 `client_uid` 发图，服务端自己按 uid 找那条记录（见 _new_uid）。
_LAST_RID = 0


def _remember_rid(rid):
    """记住已知的最大 record_id，兜底查询时用 after= 只认更新的记录。"""
    global _LAST_RID
    try:
        if rid:
            v = int(rid)
            if v > _LAST_RID:
                _LAST_RID = v
    except (TypeError, ValueError):
        pass


def _fetch_last_record_id():
    """GET 一次"比我已知的更新的记录 id"。拿不到返回 None。

    `after=_LAST_RID` 是关键：只接受比已知更新的 id，
    避免"这一轮其实没入库（重复/节流）"时把新拍的图贴到旧记录上。
    """
    url = "%s?source=%s&after=%d" % (LAST_RECORD_URL, SOURCE_ID, _LAST_RID)
    try:
        code, hdrs = _http("GET", url, settle=HTTP_SETTLE, budget=HEAD_BUDGET)
    except Exception as e:
        print("[http] 兜底取 record_id 失败: %s: %s" % (type(e).__name__, e))
        return None
    if code != 200:
        return None
    rid = hdrs.get("x-edge-record-id")
    _remember_rid(rid)
    return rid


# ------------------------------------------------------------------
# v9：给"结论"和"图片"两次 POST 发同一个 id，服务端据此配对（见文件头 v9 修正）
# ------------------------------------------------------------------
# 为什么需要它：图片补传原本必须拿到 record_id，而 record_id 只能从"结论 POST 的
# 响应头"里读。一旦读不到（第八次上板就是每一条都读不到），图片链路整条哑火。
# 有了 uid，两次请求自带关联信息，服务端自己配对 —— 板端**读不到响应也不影响贴图**。
# 组成：SOURCE_ID + 开机序号 + 本轮序号。
#   开机序号用 time.time() 取整：板子有 RTC 时是 epoch（每次开机都不同），
#   没 RTC 时是开机秒数（同一块板子也会变），两种情况都够用。
#   服务端还会加"只认最近 N 分钟内、且还没有图的那条"，所以即便撞号也无害。
_UID_SEQ = 0
_BOOT_TAG = None


def _new_uid():
    global _UID_SEQ, _BOOT_TAG
    if _BOOT_TAG is None:
        try:
            _BOOT_TAG = int(time.time())
        except Exception:
            _BOOT_TAG = 0
    _UID_SEQ += 1
    return "%s-%d-%d" % (SOURCE_ID, _BOOT_TAG, _UID_SEQ)


# ==================================================================
# 方案丙：串口输出（板 → PC 单向，**一个 socket 都不开**）
# ==================================================================
# 帧格式（全部 ASCII 文本行，`\n` 结尾；PC 侧只认 `@@` 开头的行，
# 所以 banner / [infer] / 异常打印混在中间也不影响解析）：
#
#     @@R <uid> <conf100> <manual> <label>        一条结论
#     @@B <uid> <nbytes> <nchunks>                图片开始
#     @@D <seq> <base64 块>                       图片分块（seq 从 0 起，按 seq 归位）
#     @@E <uid> <sum8>                            图片结束 + 校验（字节和 mod 256）
#
# ⚠️ 时间戳**不由板子给**：板上 time.time() 是 RTC epoch（≈2.766e8，见文件头
#    「单调时钟」那段），拿它当墙上时间会错。由 PC 补本地时间即可。
# ⚠️ label 放**最后一位**：PC 用 split(' ', 3) 取，标签里万一有空格也不会错位。

def serial_report(disease, conf100, manual=False, uid=None):
    """方案丙：把一条结论打成一行 `@@R`（**不联网、不开 socket**）。

    返回值和 `report()` 保持同形（dict），免得调用方要分两套。
    这里没有"发失败"这种状态：print 要么成功、要么把板子堵在串口缓冲区上
    （PC 侧没在读），不存在"重发"的概念 —— 所以永不返回 None。
    """
    uid = uid or _new_uid()
    print("@@R %s %.1f %d %s" % (uid, conf100, 1 if manual else 0, disease))
    return {'accepted': True, 'verdict': 'unknown', 'record_id': None,
            'client_uid': uid}


def serial_send_image(uid, jpeg):
    """方案丙：把一张 JPEG 用 base64 分块打到串口（**不联网、不开 socket**）。

    为什么要 base64：串口是**文本行**通道，原始二进制里出现 \\n 会把帧切乱。
    base64 多 33% 流量，但 USB-CDC 这点量无所谓，换来的是"任何字节都不会破坏分帧"。

    校验和用 `sum(jpeg) & 0xFF`：PC 侧对拼好的字节算同一个值，对不上就整张丢。
    （PC 侧另外还会校验：块数齐不齐 / base64 能否解码 / 是不是 \\xff\\xd8 开头。）
    """
    if not jpeg or not uid:
        return False
    b64 = ubinascii.b2a_base64(jpeg).decode().strip()
    chunks = [b64[i:i + SERIAL_B64_CHUNK]
              for i in range(0, len(b64), SERIAL_B64_CHUNK)]
    try:
        s = sum(jpeg) & 0xFF
    except Exception:
        s = 0

    print("[shot] 串口推图：%d 字节 JPEG -> base64 %d 字符 / %d 块（%d 字符/块）"
          % (len(jpeg), len(b64), len(chunks), SERIAL_B64_CHUNK))
    t0 = _ticks_ms()
    print("@@B %s %d %d" % (uid, len(jpeg), len(chunks)))
    for i in range(len(chunks)):
        print("@@D %d %s" % (i, chunks[i]))
    print("@@E %s %d" % (uid, s))
    print("[shot] ✔ 已推到串口（%.2fs）；PC 侧按 uid=%s 贴到刚入库的那条记录"
          % (_ticks_diff(_ticks_ms(), t0) / 1000.0, uid))
    return True


def _send_round_serial(name, conf, uid, manual):
    """方案丙的一轮：结论一行 + 图片分块 —— 全部走串口，**绝不碰网络**。"""
    try:
        serial_report(name, conf, manual=manual, uid=uid)
    except Exception as e:
        # print 到串口失败只有一种可能：CDC 被占/断开。这一轮废了，但板子活着。
        print("[serial] 结论打印异常: %s: %s（板子仍在运行）"
              % (type(e).__name__, e))
        return False

    if SEND_IMAGE:
        t0 = _ticks_ms()
        try:
            jpeg = grab_shot_jpeg()
            if jpeg:
                try:
                    serial_send_image(uid, jpeg)
                finally:
                    del jpeg                  # 大 bytes 尽早释放，别留到下一轮
        except Exception as e:
            print("[shot] 抓拍链路异常（本轮不带图）: %s: %s" % (type(e).__name__, e))
        print("[shot] 抓拍+传图共 %.1fs" % (_ticks_diff(_ticks_ms(), t0) / 1000.0))
        gc.collect()
    return True


def report(disease, conf100, manual=False, uid=None):
    """上报**结论**（**只发不读**，v10）。发出去了返回 dict；连不上/发不出返回 None。

    ⚠️ v10 为什么不再读响应（第十二次上板定论，改回去之前先读文件头 v10 段）：
        跑过一次 KPU 推理之后，这块板子的"读响应"会挂死
        （probe 干净环境下 5/5 全通，主脚本一读就死；`[selftest]` 也证明
          相机/KPU 在场、只要没推理，读就是好的）。
        所以结论改成**单向**：`connect → send → close`，**一个 recv 都没有**。

    返回值语义（与 v9 不同，调用方注意）：
        dict -> 已经发出去了（**不代表服务端判成什么**），别再重发
        None -> 真的没发出去（connect/send 抛异常），进补传队列
    也就是说"读不到响应"这件事在 v10 里**根本不存在**了。

    为什么不要 record_id：图片靠 `uid` 配对（服务端自己找记录，见 utils/edge_raw.py）。
    为什么不怕 429/duplicate：这一轮被挡了，10 秒后下一轮照常来；
        而且服务端 v10 起对 manual=True 免节流。

    manual=True 表示"这次是人主动要的，不是自动巡检轮次"，服务端据此跳过去重，
    保证人主动触发的那一次一定出一条记录。
    ⚠️ v15 起「立即抓拍」已整条下线，主循环**不再有任何地方传 manual=True**；
    这个形参保留只是给 probes/ 手动调用和未来"本地按键触发"留的口子。
    """
    payload = {
        "disease": disease,
        "confidence": round(conf100, 1),
        "source": SOURCE_ID,
        "timestamp": now_str(),
    }
    if manual:
        payload["manual"] = True
    if uid:
        payload["client_uid"] = uid
    _say("[report] POST 上报中（只发不读）…")
    try:
        body_bytes = ujson.dumps(payload).encode()
        _http_send_only("POST", SERVER_URL, body_bytes, verbose=True)
    except Exception as e:
        # 真的没发出去（连不上/发不出）—— 只有这种才进补传队列
        _say("[report] 上报失败（请求没能发出）: %s: %s" % (type(e).__name__, e))
        return None

    # ⚠️ 这里**刻意不读响应**。服务端收到就算数；收不到，下一轮再来。
    _say("[report] 已发出（不读响应；图片将按 uid=%s 配对）" % (uid or "-"))
    return {'accepted': True, 'verdict': 'unknown', 'record_id': None,
            'client_uid': uid}


def report_image(record_id, image_b64, uid=None):
    """给已入库的记录**单独补传**一张现场图（尽力而为，失败不重试、不影响记录）。

    ⚠️ v10 起这是**退路**，默认不走（默认 = `send_image_raw()` 的原始 TCP 单向推送）：
        这条路要读响应，而读响应正是板子跑过推理之后会挂死的地方。
        只在 `IMAGE_TRANSPORT = "http"` 时用它（原始 TCP 端口被网络环境挡住的情况）。
        保留不删：它经受过实机验证（历史上真出过图），是个可用的后备。

    为什么和结论分成两次请求（2026-10-01 实测，别合回去）：
        结论+198KB 图一次 POST 时，板端 urequests 报
        `list index out of range` —— 那是它读响应状态行读到空行的报错，
        即服务端一个字节都没回。服务端本身没问题（PC 端 423KB base64 用 urllib 正常），
        瓶颈在 K230 上行走核间通信的 socket，大包不稳。
        拆开后：结论（几百字节）必达 → 记录不会丢、APP 通知照发；
        图片失败最多是这条记录没图，比"整条丢"好得多。

    record_id 与 uid（v9）：
        record_id 有就直接用；
        没有（响应头没读到）就退一步 —— ① 先 GET /last-record 兜一下；
        ② 还不行就**只带 uid 发**，服务端按 uid 自己找那条记录贴图。
        所以"读不到响应头"再也不会导致"一张图都没有"。
    """
    if not record_id:
        # 兜底 ①：没拿到 record_id 就问服务端要（GET 通道小包，最稳）
        record_id = _fetch_last_record_id()
        if record_id:
            print("[shot] 用 GET 兜底拿到 record_id=%s" % record_id)
    if not record_id and not uid:
        return False
    if not image_b64:
        return False
    payload = {"image": image_b64, "source": SOURCE_ID}
    if record_id:
        payload["record_id"] = record_id
    if uid:
        payload["client_uid"] = uid          # 兜底 ②：服务端按 uid 找记录
    print("[shot] 图片 POST 中…（%d 字节 base64，record_id=%s uid=%s）"
          % (len(image_b64), record_id or "-", uid or "-"))
    try:
        body_bytes = ujson.dumps(payload).encode()
        code, hdrs = _http("POST", IMAGE_URL, body_bytes,
                           settle=IMG_SETTLE, verbose=True, budget=IMG_BUDGET)
        if code == 200:
            _remember_rid(hdrs.get("x-edge-record-id") or record_id)
            print("[shot] 现场图已补传 record_id=%s (%d 字节 base64)"
                  % (record_id or hdrs.get("x-edge-record-id") or "?", len(image_b64)))
            return True
        if code == 0:
            # 同上：图片发出去就发到了，读不到响应不代表失败，不要再折腾。
            print("[shot] 图片已发出但未见响应头（记录仍在，图可能已入库）")
            return False
        print("[shot] 补传被拒 %d（status=%s）" % (code, hdrs.get("x-edge-status") or "-"))
    except Exception as e:
        print("[shot] 补传失败（记录仍在，只是没图）: %s: %s" % (type(e).__name__, e))
    return False


def push_pending(disease, conf100):
    """把一条**结论**放进补传队列。

    ⚠️ 刻意不缓存图片：一张图几十~上百 KB，MAX_PENDING=20 时最坏几 MB 常驻堆，
    K230 上会 OOM；而且补传时网络往往更差，带图更容易再失败。
    所以重传只保证"结论不丢"，图丢了就丢了（看板上那条显示无图，无伤大雅）。
    """
    if len(_pending) >= MAX_PENDING:
        _pending.pop(0)
    _pending.append((disease, conf100))


def flush_pending():
    if not _pending:
        return
    print("[report] 补传 %d 条缓存结论（只补结论，不含图）" % len(_pending))
    left = []
    for disease, conf in _pending:
        if report(disease, conf) is not None:
            time.sleep(5.5)                     # 遵守服务端最小间隔
        else:
            left.append((disease, conf))
    while len(_pending):
        _pending.pop(0)
    for item in left:
        _pending.append(item)


def send_round(name, conf, manual=False):
    """上报一轮：**结论先行（只发不读），图片随后（原始 TCP 单向推）**。

    顺序别调，理由和 v9 一样：结论只有几百字节，小包必然发得出去 ->
    记录、APP 通知都不受图片影响；图片单独推，失败最多"这条没图"，绝不会连累记录。
    v10 的区别是**两次都不再等响应**（见文件头 v10 段）—— 挂死点从结构上没有了。

    返回 True 表示结论已经发出（**不代表服务端判成什么**，板端没读回执）；
    False 表示连都没连上（进补传队列）。

    ⚠️ TRANSPORT="serial"（方案丙，默认）时整轮走 `_send_round_serial()`：
       一串 `print` 到 USB 串口，**一个 socket 都不开**，下面这段 HTTP 代码不执行。
       它在文件里原样保留，是为了"串口被挡住时还能翻回 v10 老路"（改 TRANSPORT 即可）。
    """
    uid = _new_uid()
    if TRANSPORT == "serial":
        return _send_round_serial(name, conf, uid, manual)
    res = report(name, conf, manual=manual, uid=uid)
    if res is None:
        push_pending(name, conf)          # 只有"真的没发出去"才补传
        return False

    # ⚠️ v10：这里**不再有 429 重试分支** —— 板端不读响应，无从知道被没被节流。
    #    配套的服务端改动：`manual=True` 已从节流里摘出来（人按按钮不该被静默丢掉），
    #    而自动巡检本来 10 秒一轮，远大于服务端 5 秒的最小间隔，撞不上。
    if SEND_IMAGE:
        _t0 = _ticks_ms()
        if IMAGE_TRANSPORT == "raw":
            # 主通道：原始 TCP 单向推送（无 base64、无 recv）
            jpeg = grab_shot_jpeg()
            if jpeg:
                try:
                    send_image_raw(uid, jpeg)
                finally:
                    del jpeg              # 大 bytes 尽早释放，别留到下一轮
        else:
            # 退路：老的 HTTP + base64 通道（要读响应，仅在 raw 被挡住时用）
            b64 = grab_shot_base64()
            if b64:
                try:
                    report_image(res.get("record_id"), b64, uid=uid)
                finally:
                    del b64
        _say("[shot] 抓拍+传图共 %.1fs" % (_ticks_diff(_ticks_ms(), _t0) / 1000.0))
        gc.collect()
    return True


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def main():
    global _sensor
    boot_banner()
    labels = load_labels()
    if not labels:
        print("[X] 没有标签文件，退出")
        return

    wlan = None
    if TRANSPORT == "serial":
        # ★ 方案丙：**不碰网络**。不连 WiFi、不补传、不轮询指令、不做 socket 自检。
        #   板子唯一的输出通道就是 USB 串口（就是现在这根 Type-C 线）。
        print("[cfg] ★ 传输方式 = serial（方案丙）：板子**不开任何 socket**")
        print("[cfg] 结果 -> 串口 @@R …；图片 -> 串口 @@B/@@D/@@E（base64，%d 字符/块）"
              % SERIAL_B64_CHUNK)
        print("[cfg] ⚠️ PC 侧必须同时跑 app/AI_Model/k230/serial_relay.py，否则没人转发")
        print("[cfg] 串口模式下：WiFi / 补传队列 / socket 自检 **全部关闭**")
        print("[cfg] 巡检间隔 %.0f 秒；服务端口径不变（拍到就入库，未确诊的按 TTL 清理）"
              % LOOP_INTERVAL)
    else:
        wlan = wifi_connect()
        flush_pending()
        print("[cfg] 结论上报 %s（**只发不读**：发完即关，不等响应）" % SERVER_URL)
        print("[cfg] 图片传输 %s (SEND_IMAGE=%s)"
              % ("原始 TCP %s:%d（%d 字节上限，无 base64 / 无 recv）"
                 % (RAW_HOST, RAW_PORT, IMAGE_MAX_RAW)
                 if IMAGE_TRANSPORT == "raw" else
                 "%s（%d base64 上限，需要读响应）" % (IMAGE_URL, IMAGE_MAX_B64),
                 SEND_IMAGE))
        print("[cfg] 巡检间隔 %.0f 秒；记录由服务端保留，未确诊的到期自动清理"
              % LOOP_INTERVAL)
        print("[cfg] v10：结论与图片都**不等响应**（一个 recv 都不做，挂死点从结构上消失）")
        print("[cfg] 自实现 HTTP/1.0，**不 settimeout**（一设超时 recv 就走坏的 poll 路径）")
        print("[cfg] 读响应 = 先睡 + setblocking(False) 轮询（每次 recv 前重设），预算用 ticks_ms")
        print("[cfg] settle 结论 %.2fs / 图片 %.2fs；预算 %.1fs / %.1fs"
              % (HTTP_SETTLE, IMG_SETTLE, HEAD_BUDGET, IMG_BUDGET))
        print("[cfg] v9.3 现场自检 SELFTEST_SOCK=%s（相机/KPU 就绪后验一次非阻塞 recv）"
              % SELFTEST_SOCK)

    sensor = None
    try:
        # v14：裸 Sensor 三通道（p13-B 探针 2026-10-02 上板实证全绿）。
        #   顺序铁律：**先配全部通道，再 MediaManager.init()，最后 sensor.run()**
        #   （官方 19.snapshot.py / det_video.py 都是这个顺序；run 之后再配通道不生效，
        #    p13 变体 A 实测 snapshot chn(1) failed(3)）。
        print("[camera] 裸 Sensor 三通道初始化(display=%s %dx%d) ..."
              % (DISPLAY_MODE, SHOT_W, SHOT_H))
        sensor = Sensor()
        sensor.reset()
        sensor.set_framesize(width=SHOT_W, height=SHOT_H, chn=CAM_CHN_ID_0)
        sensor.set_pixformat(Sensor.YUV420SP, chn=CAM_CHN_ID_0)   # 显示
        sensor.set_framesize(width=SHOT_W, height=SHOT_H, chn=CAM_CHN_ID_1)
        sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_1)     # 拍照
        sensor.set_framesize(width=IMG_SIZE, height=IMG_SIZE, chn=CAM_CHN_ID_2)
        sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_2)    # AI
        Display.init(Display.VIRT, SHOT_W, SHOT_H, to_ide=True)
        Display.bind_layer(**sensor.bind_info(x=0, y=0, chn=CAM_CHN_ID_0),
                           layer=Display.LAYER_VIDEO1)
        MediaManager.init()
        sensor.run()
        _sensor = sensor
        print("[camera] 就绪（拍照 %dx%d RGB565 / AI %dx%d）"
              % (SHOT_W, SHOT_H, IMG_SIZE, IMG_SIZE))

        print("[kpu] 加载 %s ..." % KMODEL_PATH)
        kpu = nn.kpu()
        try:
            kpu.load_kmodel(KMODEL_PATH)
        except Exception:
            with open(KMODEL_PATH, "rb") as f:
                kpu.load_kmodel(f.read())
        print("[kpu] 就绪")

        # ★ v9.3：相机/KPU 已全部就位 —— 先验一次"非阻塞 recv 还灵不灵"（见 SELFTEST_SOCK）。
        #    这是唯一能把"probe 全通、正式脚本卡死"这个矛盾钉死的实验，验证完可关掉。
        #    ⚠️ 方案丙（serial）不跑：那时压根没有 socket 可验。
        if TRANSPORT == "http" and SELFTEST_SOCK:
            selftest_socket()

        loop = 0
        next_inspect = _ticks_ms()        # 启动后立刻跑第一轮

        # 主循环是 1 秒心跳，而不是"跑一轮睡 10 秒"：
        # 掉线重连、异常恢复都靠这个心跳及时介入，不会被一轮 10 秒的推理堵住。
        while True:
            now = _ticks_ms()

            # ---- 不到时间就继续等（心跳 1 秒一次）----
            if _ticks_diff(now, next_inspect) < 0:
                time.sleep(TICK)
                continue

            next_inspect = _ticks_ms() + LOOP_INTERVAL_MS
            loop += 1
            print("\n--- 第 %d 轮（自动巡检）---" % loop)

            # v5：整轮包一层 try —— 任何一环出错（推理、抓拍、上报）都只丢掉这一轮，
            # 循环继续。以前异常会一路冒到 finally，而 pl.destroy() 在显示通道上
            # 是可能卡住的（实机没接 LCD），一卡就是"日志停在半句话 + 看门狗复位"。
            try:
                # v14：PipeLine.get_frame() 的本体就是这句（板上 PipeLine.py:134-136），
                #      行为不变 —— 返回 CHN2 的 (3,224,224) uint8 零拷贝视图。
                img_ch2 = sensor.snapshot(chn=CAM_CHN_ID_2)
                frame = img_ch2.to_numpy_ref()
                x = preprocess(frame)
                kpu.set_input_tensor(0, nn.from_numpy(x))
                kpu.run()
                row = kpu.get_output_tensor(0).to_numpy()[0]

                best = top1(row)
                conf = float(row[best]) * 100.0
                name = labels[best] if best < len(labels) else str(best)
                _say("[infer] %s %.1f%%  %s"
                     % (name, conf,
                        "(达确诊线)" if conf >= LOCAL_CONFIRMED
                        else "(未达确诊线，服务端仍入库，展示为「置信率不足」)"))

                # ★ v10.1：推理之后再验一次 socket（v9.3 那次自检只跑在推理前）。
                #   这次自检**证明网络是好的**（connect 0.03s、recv 立刻返回），
                #   紧接着 report 的 connect 却把板子送进了复位 —— 方案丙由此而来。
                #   串口模式下没有 socket 可验，跳过。
                if TRANSPORT == "http" and SELFTEST_SOCK:
                    selftest_socket()

                # ---- ③ 联网检查（仅 http；serial 模式没有网络可查）----
                if TRANSPORT == "http":
                    if not wifi_ready(wlan):
                        print("[wifi] 掉线，尝试重连")
                        wlan = wifi_connect(timeout=10)

                    if not wifi_ready(wlan):
                        push_pending(name, conf)
                        gc.collect()
                        time.sleep(TICK)
                        continue

                # ---- ④ 上报：结论先行，图片随后（顺序别调，这是可靠性设计）----
                send_round(name, conf)
            except Exception as e:
                print("[main] 本轮异常（已跳过，循环继续）: %s: %s"
                      % (type(e).__name__, e))
            gc.collect()
            time.sleep(TICK)

    except KeyboardInterrupt:
        print("\n[main] 已停止")
    finally:
        # v14：官方收尾顺序（19.snapshot.py）：sensor.stop → Display.deinit → MediaManager.deinit
        #      （板上 PipeLine.destroy() 缺最后一步，反复建拆会报 sensor already inited）
        try:
            if sensor is not None:
                sensor.stop()
        except Exception:
            pass
        try:
            Display.deinit()
        except Exception:
            pass
        try:
            MediaManager.deinit()
        except Exception:
            pass
        gc.collect()


if __name__ == "__main__":
    main()
