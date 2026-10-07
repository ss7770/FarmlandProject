DATABASE_PATH = 'sensor.db'

# Java 天气服务（Spring Boot，app/JAVA_Backend，默认 8080）
# 用 127.0.0.1 而非 localhost：Windows 上 localhost 会优先解析到 IPv6 ::1，
# 若 Java 端只监听 IPv4 会出现"服务明明起着却连不上"的假故障
WEATHER_API_URL = 'http://127.0.0.1:8080/api/weather'

# ===== D200 网络摄像头（已淘汰，2026-10-01）=====
# 淘汰原因：① 固件 HTTP 并发≈1，长连接拉流期间再抓拍会把采集链路压死（只能断电重启）；
#          ② 板端会"假死"——以正常帧率持续发字节但内容逐字节不变，换最新固件同样复现。
# 现已改用 01Studio CanMV-K230 做**边缘推理**（本地跑模型，只回传结论），见下方 K230 区块。
# 这一组配置保留仅为兼容旧代码与回滚：D200_ENABLED=False 时整套拉流/巡检线程都不会启动。
D200_ENABLED = False           # 已淘汰，保持 False
D200_HOST = '192.168.57.163'   # 历史值（已退役设备 IP）
D200_PORT = 80
D200_STREAM_PATH = '/stream'   # 出厂固件的 MJPEG 流路径（不是 /video、不是 /mjpeg）
D200_RECONNECT_DELAY = 3.0     # 断线重连间隔（秒）
# 自动巡检间隔（秒）。与 ESP32-CAM 的 5 分钟巡检保持一致
D200_INSPECT_INTERVAL = 300
# 自动巡检抓拍的图片保留张数（超出后删除最旧的 d200_*.jpg，避免 uploads 无限增长）
D200_KEEP_FRAMES = 50
# 缓存帧的"保鲜期"（秒）：超过这个年龄的帧不再当作实时画面（抓拍/巡检/预览都拒绝），
# 防止摄像头掉线后对同一张旧图反复识别入库、或看板一直显示冻结画面
D200_FRAME_MAX_AGE = 15

# ===== K230 边缘推理设备（01Studio CanMV-K230，方案 B）=====
# 架构变化：原来是"摄像头 → 拉流回 PC → PC 跑 TFLite"，
# 现在是"K230 本地跑模型 → 只把结论（JSON）回传 → Flask 入库 + 推送"。
#
#   K230(摄像头 + KPU 推理) --HTTP POST JSON--> Flask /api/edge/disease
#                                                    --> 二次三档校验 --> 确诊入库 source='k230'
#                                                    --> APP 30s 轮询自动通知 + TTS（APP 零改动）
#
# 好处：不走图片链路（几字节 vs 每帧 60KB）、K230 自带算力可离线工作、
#       彻底绕开 D200 的并发与假死两个坑。代价：模型要转成 K230 的 kmodel 格式。
EDGE_ENABLED = True
# K230 的 IP（CanMV 里用 network.LAN() 看到，或用可选的 HEARTBEAT 上报）。
# 目前**仅用于看板展示与心跳比对**，不影响接收——接收接口是被动等 K230 来推。
EDGE_HOST = '192.168.57.126'
# 服务端二次校验阈值：与 utils/inference.py 的三档保持一致（≥75 确诊 / 45~75 疑似 / <45 拒识）。
# K230 的 kmodel 与 PC 端 TFLite 是**两套模型**，置信度分布可能有偏差，
# 所以不盲信设备结论，服务端再判一次；两端阈值都改才算真正改口径。
EDGE_REQUIRE_CONFIRMED = False  # ← 已被 RECORD_ALL 取代，保留仅为兼容旧代码/回滚
# 同一结论去重窗口（秒）：窗口内收到完全相同的 disease+confidence 视为重复上报，丢弃。
# 防止 K230 巡检脚本重试/循环上报把库里刷满同一条。
EDGE_DEDUP_SEC = 120
# 结论上报最小间隔（秒）：比这更频繁的上报直接拒绝（429），避免设备端 bug 打爆后端。
EDGE_MIN_INTERVAL = 5

# ===== 记录策略（2026-10-01 调整：拍到就入库）=====
# 原口径：只有确诊（≥75%）才写 disease_records，疑似/拒识只在响应里回一句。
# 新口径：**拍到什么就存什么**——每一次采集/上传都留下记录，保证"设备到底看到了什么"
# 可追溯（尤其是排查识别不准时，没图没记录根本没法复盘）。
# 代价是记录会持续累积，所以未确诊的那些设一个 TTL，到期由后台线程连图一起清掉。
RECORD_ALL = True               # True：所有上报都入库（不再只看确诊）
UNCONFIRMED_TTL_MIN = 30        # 未确诊记录的保留时长（分钟），到期连图片一起删
PURGE_INTERVAL_SEC = 300        # 后台清理线程扫描间隔（秒）

# ===== 现场图原始 TCP 通道（v10，2026-10-01）=====
# 为什么要有（十几轮上板的结论）：
#   板端 HTTP 那套「发得出去、读不回来」——probe 干净环境下全通，
#   但主脚本跑过一次 KPU 推理之后，读响应就挂死。图片这条路要**多等一次响应**，
#   所以最先牺牲的就是它（历史现象：记录一直在涨、APP 一张图都没有）。
# 改成单向推送：板端连上来 → `EIMG1 <uid> <nbytes> <source>\n` + 原始 JPEG → 关连接。
#   服务端**只收不回**，板端**零 recv** ⇒ 结构上不存在"等响应"这个挂死点。
#   顺带省掉 base64（少 33% 流量 + 板端一次 b64 编码）。
# 协议与实现见 utils/edge_raw.py。
EDGE_RAW_IMAGE = True           # False 时退回 `POST /api/edge/image`（base64，需要读响应）
EDGE_RAW_HOST = '0.0.0.0'       # 监听地址（板端要能从局域网连进来）
EDGE_RAW_PORT = 5001            # ⚠️ 与板端 k230_classify.py 的 RAW_PORT 必须一致
EDGE_RAW_MAX_BYTES = 2000000    # 单张图原始字节上限（2MB，够 1080p q80 还有余量）
EDGE_RAW_UID_WAIT_SEC = 6.0     # 等"结论那条记录"入库的最长时间（超时只丢这张图）

# ===== 【已删除】抓拍指令通道（v15，2026-10-02）=====
# 原设计：K230 是纯被动设备，所以"让它立刻拍一张"要反过来做 ——
#   服务端挂一条待取指令(EDGE_CMD_TTL_SEC)  ->  K230 每 2 秒轮询取走  ->  立刻跑一轮推理 + 抓拍
# 用户拍板全链路删除（板端 poll_command / 服务端 /api/edge/command / 前端按钮 一并下线）。
# ⇒ 本文件不再有 EDGE_CMD_TTL_SEC；服务端现在是**纯被动接收**，不给设备下发任何东西。

# ===== 离线语音识别（Vosk，2026-10-07）=====
# 小慧对话框的语音输入原本走安卓原生 SpeechRecognizer，但那台设备上：
#   ① 内嵌识别恒回 ERROR_RECOGNIZER_BUSY（服务在、干不了活）；
#   ② 系统语音界面不存在（RecognizerIntent 抛 ActivityNotFoundException）。
# ⇒ 两条原生路都是死的（已按官方文档确认包可见性不是原因，补 <queries> 没用）。
# 改成「APP 只负责录音 → WAV 传到本接口 → PC 用 Vosk 离线识别」：
# 不依赖设备的语音服务，也不需要外网（模型就放在 models/ 下）。
# 接口：POST /api/asr（WAV 16k/16bit/mono）、GET /api/asr/status
ASR_ENABLED = True
# 模型目录（相对后端根目录 app/Python_Backend）：vosk-model-small-cn-0.22 约 42MB
ASR_MODEL_DIR = 'models/vosk-model-small-cn-0.22'

