DATABASE_PATH = 'sensor.db'

# Java 天气服务（Spring Boot，app/JAVA_Backend，默认 8080）
# 用 127.0.0.1 而非 localhost：Windows 上 localhost 会优先解析到 IPv6 ::1，
# 若 Java 端只监听 IPv4 会出现"服务明明起着却连不上"的假故障
WEATHER_API_URL = 'http://127.0.0.1:8080/api/weather'

# ===== D200 网络摄像头（安信可 AiPi-CAM-D200，出厂固件 MJPEG）=====
# 板子用 AT+WJAP 连入热点后进入 Station 模式，作为网络摄像头被 Flask 拉流，
# 不需要任何板端开发。详见 开发文档.md "摄像头接入"一节。
D200_ENABLED = True            # False 则完全不连摄像头（离线演示/无硬件时不报错）
D200_HOST = '192.168.57.163'   # AT+WJAP? 查询到的板子 IP（换了热点需同步修改）
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

