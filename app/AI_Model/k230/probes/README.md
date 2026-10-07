# K230 最小探针包（probes/）

目的：不再每次跑 90KB 大脚本。每个探针只隔离**一个变量**，几十~一百多行，跑完把 `[P?]` 日志贴回来即可定位。

对照官方例程（`D:\software\zidai\Desktop\CanMV K230\`）写法，重点验证两处官方与我们的差异：
官方 `socket.py` 用 `settimeout(0)`；官方 `camera.py` 不走 PipeLine。

---

## 一、已跑完的结果（2026-10-02，第十四~十六轮）

| 探针 | 结果 | 结论 |
|---|---|---|
| P0 纯网络 | **20/20**（我们写法 10/10 + 官方 `settimeout(0)` 10/10），connect 0.06~0.43s | 基线健康；"绝不 settimeout"至少在简单 GET 场景不成立 |
| P1 PipeLine 取帧 | 10/10（299 帧空转） | PipeLine / media 本身**不伤网络** |
| P2 只跑 KPU（假输入） | 10/10 | `kpu.run()` 本身**不伤网络** |
| P3 PipeLine+KPU 真图推理 | **27/27**（推理后 0.2~58s 全程 ✔） | **「推理后网络必死」作废** |
| P4 官方 Sensor 采集 | 10/10 | 采集路径无关 |
| **P7 真实轮次复刻**（4 步） | **全 ✔**（2 轮，含轮后探 3 次 + 长盯 30s） | ①poll_command ②推理后 selftest ③wifi_ready ④POST 真实接口 —— **四步全部无罪** |
| **P8 推理计时拆解** | ①preprocess **0.011s** ②from_numpy **0.001s** ③set_input 0.000s ④**kpu.run() 66.024s** ⑤取输出 0.000s | 那 66 秒 **100% 在 `kpu.run()`**；输入张量正确（dtype=102/float32，shape=(1,224,224,3)）；**内存不泄漏**（三轮后 mem_free 3981696→3982432→3982752） |
| **P9 完整 send_round（含图片）** | a) 结论 POST **✔**（`已连接 0.08s` → `已发出 309B`）<br>b) `snapshot(chn=CHN0)` **✔**（640×480）<br>c) **`to_rgb 缩放 0.50` ✘ → 板子自己下电** | **抓到了 14 轮来第一个真死点**：在转图那一步（`k230_classify.py:988`），**无 Python 回溯**。推理后网络完全正常 ⇒ 之前"死在 report/connect"的读数全部作废（那是没有 30ms 节流时，硬复位丢掉了 USB 缓冲尾巴） |
| **P11 kmodel 输入体检**（跑了两遍） | 第一遍（旧 kmodel）：① 官方 `yolov8n_320` + uint8 **0.027s** ✔（输出 `(1,84,2100)`）<br>② 我们的(旧) kmodel + uint8 → **`RuntimeError: KPU run failed`**<br>第二遍（换上新 3.48MB kmodel）：② 我们的 kmodel + uint8 → **`kpu.run()` = 0.003s** ✔（输出 `(1,38)`） | ✓ **66 秒彻底闭环**。旧 kmodel 声明的**不是** uint8；新的 uint8 模型板上 **3 毫秒**，比官方 yolov8n_320 还快（我们输入是 224 而非 320） |
| **P10 抓拍路径**（两遍） | 第一遍 `SOURCE="pipeline"`：① `snapshot(CHN0)` ✔ 640×480<br>② **`VARIANT=1`（无参 `to_rgb888()`）也直接下电**<br>第二遍 `SOURCE="sensor"`：① `Sensor` 直出 RGB888 640×480 ✔<br>③ `to_numpy_ref()` shape=**(480,640,3)** ✔<br>④ `Display.show_image()` ✔<br>⑤ `K._encode_jpeg(q60)` = **13398 字节** ✔（收尾全部正常） | ⇒ **不是缩放参数的锅**：对 CHN0 的 YUV420SP 活帧做转换这条路**整体不可用**；<br>★ `Sensor` 直出这条路全绿，证明**抓拍不需要任何转换** |
| **P12 抓拍通道选型**（2026-10-02 第十六轮，最终定案） | **H0** 读到板上 `/sdcard/libs/PipeLine.py` **全文 157 行**<br>**H3** ✔ `pl.get_frame()` → `shape=(3,224,224) dtype=66`<br>**H1** ✔ CHN0 **零转换**直接编码：q60=**39430** / q40=17322 / q25=10632 字节<br>**H2** ✘ `RuntimeError: sensor(0) snapshot chn(1) failed(3)` | ★★ **⇒ 走路线乙，已落地**：<br>① `create()` 真实签名里**没有 `ch1_frame_size`**（`TypeError`），且它**只配 CHN0/CHN2** —— CHN1 从没配过，H2 失败是必然的 ⇒ **路线甲判死**；<br>② `dtype=66` = ulab 的 `ord('B')` = **uint8** ⇒ `get_frame()` 本来就给 uint8，与 uint8 kmodel 口径一致（**AI 路一行不用动**）；<br>③ CHN0 的 YUV420SP **零转换直接编码可行** ⇒ 正式脚本抓拍改成 `snapshot → _encode_jpeg`，`_to_rgb_scaled` + `IMAGE_SCALE_LADDER` 已删除；<br>④ ⚠️ **q60 已占 45000 上限的 87.6%** ⇒ `IMAGE_QUALITY_LADDER` 必须保留 |

合计 **100+ 次网络调用 0 失败** ⇒ **网络波动这个变量可以彻底排除**（正式脚本的死亡症状是 USB 掉线+复位，不是网络症状）。

### P9 实测：死点在**转图**，不在网络（2026-10-02）

```
[P9] a) 结论返回 dict=已发出（0.18s）        ← 推理之后，网络 0.08s 就连上、309B 发出
[P9] b) 抓拍开始  mem_free=3849216
[shot] ① 完成，原始帧 640x480（mem_free=3847488）   ← snapshot 正常返回
[shot] ② to_rgb 缩放 0.50 中…（mem_free=3847328）   ← 之后什么都没有（板子掉电）
```

- **帧是 640×480，不是 1920×1080** —— 因为我们没给 `PipeLine` 传 `display_size`，
  `create()` 走 else 分支用 `Display.width()/height()`（VIRT 默认 640×480）。
  ⇒ 代码注释里"FHD 的 RGB888 要 6.2MB、手上有 3.8MB"**是错的**：640×480 RGB888 只有 900KB，
  ×0.5 只要 225KB。**内存从来不是问题**（是的话会吐 `MemoryError`，不是掉电）。
- **`osd_img` 不是捷径**：它是空的 `image.Image(display_size, ARGB8888)` 画布，
  相机画面在 `LAYER_VIDEO1`（CHN0 零拷贝直绑显示层），**没进 Python 侧**。

### 官方源码研读结论（PipeLine.py / AIBase.py / Ai2d.py / Utils.py）

| # | 发现 | 对我们的意义 |
|---|---|---|
| 1 | `AIBase.inference` 喂给 `set_input_tensor` 的 tensor **全是 uint8**（来自 `Ai2d` 的 `np.uint8` 输入/输出） | 官方 kmodel 吃 uint8；我们 `to_kmodel.py:70` 是 `input_type='float32'` ⇒ **66 秒的头号嫌疑**（P11 去验） |
| 2 | 缩放一律走**硬件 2D 引擎 `Ai2d`**，官方从不给 image 模块传缩放参数 | 我们 `to_rgb888(x_scale=..., y_scale=...)` **没有官方先例** ⇒ P10 去打 |
| 3 | 官方唯一一次 `to_rgb888()` 在 `Utils.read_image()`，**且不带任何参数** | P10 变体 1 的依据 |
| 4 | `PipeLine.create(..., to_ide=True)` 是默认值；CHN0 = `display_size`，格式被硬编码成 `YUV420SP` | IDE 里那一路画面是**显示层在放 CHN0**，和 Python 侧取图无关 |
| 5 | `ScopedTiming` 用 `time.time_ns()`（整数） | 印证我们 v9.2「别用浮点秒」的修法 |
| 6 | `AIBase.deinit()` 里就有 `kpu.__del__()` + `del self.ai2d` + `nn.shrink_memory_pool()` | 以后要"松手"照抄官方，别再自己发明 |

### 官方「取图 / 多通道」取证（2026-10-02 第十六轮新增）

上一轮只读了 `libs/` 那四个文件，这轮把两套官方资料里的**例程**也扫了一遍（`PipeLine.py` 等仍只在板上有）：

| 出处 | 原文关键行 | 对我们的意义 |
|---|---|---|
| `K230视觉模块/程序源码/02.Basic/19.snapshot.py:90-91,106,130` | `set_framesize(width=640,height=480, chn=CAM_CHN_ID_1)` + `set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_1)` → `img = sensor.snapshot(chn=CAM_CHN_ID_1)` → `img.save(path)` | **官方唯一的"snapshot 存盘"范式**：抓拍走 **CHN1 + RGB565**，**零转换**。⇒ `set_framesize`/`set_pixformat`/`snapshot` **都吃 `chn=`** |
| `K230视觉模块/程序源码/13.training/det_video.py:145-149,151-152,164-166,173-177` | CHN0 `PIXEL_FORMAT_YUV_SEMIPLANAR_420` 给显示 / CHN2 `PIXEL_FORMAT_RGB_888_PLANAR` 给 AI；`Display.bind_layer(..., LAYER_VIDEO1)`；`sensor.snapshot(chn=CAM_CHN_ID_2).to_numpy_ref()` → `nn.from_numpy` | **裸 Sensor 多通道并存的完整范式** ⇒ 路线丙的每条 API 都有官方出处 |
| `K230视觉模块/程序源码/04.Detecting/01.find_lines.py:100-110` | `pl.create(ch1_frame_size=[W,H])` → `pl.sensor.snapshot(chn=CAM_CHN_ID_1)` | **PipeLine 也能开 CHN1** 的唯一先例（**另一套 SDK，本固件是否支持未验证**）⇒ P12 的 H0/H2 |
| `2.机器视觉/7.cv_lite图像处理/2.边缘检测（彩色图）/rgb888_find_edges.py:81` | `image.Image(w,h,image.GRAYSCALE, alloc=image.ALLOC_REF, data=edge_np)` | ndarray **零拷贝**变回 image.Image 的官方写法 |
| 全项目搜索 | `PipeLine.py`/`AIBase.py`/`Ai2d.py`/`Utils.py` 源码副本 **0 命中** | 只能从板上 `/sdcard/libs/` 拿 ⇒ P12 的 H0 直接把源码打印出来 |

### ★ 那 66 秒——**已定案**（2026-10-02）

1. **一次推理要 66 秒**（P2: 64.11/63.97/64.05s，P3: 66.04s，P7: 66.09/65.99s，P8: 66.024/66.040/66.004s），
   而官方 `yolov8n_320` 在同板上 `kpu.run()` = **0.027s**（P11 ①）⇒ **不是板子/固件/供电的问题**。
2. **根因 = 旧的 `model.kmodel` 里没有 KPU 段**，板上全程在 **CPU** 上解释执行。
   判别不用上板，读 kmodel 头部 **`offset 16` = 段数**：

   | kmodel | offset16 | 大小 | PC 模拟器 | 板上 |
   |---|---|---|---|---|
   | 官方在线平台导出（参照） | **2** | 6.24 MB | ❌ | 正常 |
   | 我们**新**转 uint8 | **2** | 3.48 MB | ❌ | **`kpu.run()` = 0.003s** ✔ |
   | 我们**旧** float32 | **1** | 12.35 MB | ✅ | **66 秒** ✘ |

   触发条件是 `to_kmodel.py` 用了 `input_type='float32' + preprocess=False` ⇒ KPU 后端出不来，
   **静默退化成纯 CPU**（不报错、不警告）。已改成 `INPUT_MODE="uint8"`（`preprocess=True` +
   `input_range=[0,1]` + `mean=[0,0,0]` + `std=[1,1,1]`，校准集同步改 uint8 原值）。
   官方 API 手册的硬约束：`preprocess=True` 时 `input_shape` 必填；`input_type` 只能是 uint8/float32；
   `input_type='uint8'` 时 `input_range` 必填。
3. ⚠️⚠️ **`nncase.Simulator`（PC）只加载得动"单段(纯 CPU)"kmodel** —— 两段模型一律 `RuntimeError`，
   **连官方导出的 kmodel 也加载不了** ⇒ 当年那句"60/60 对齐 TFLite"是在一个**没有 KPU 段**的模型上
   测出来的，**对板上毫无意义**。KPU 模型的精度**只能上板验**。
4. ⚠️ `stackvm` 字符串在**所有** k230 kmodel 头部都有（官方也有）——**别拿它当线索**（已排除）。
5. **P2 那个"每轮漏 43,616 字节"可以撤回** —— P8 用真实相机路径三轮跑完 `mem_free` 基本不动。

### P7 唯一没覆盖的东西（= P9 要打的）

| send_round() 里的步骤 | P7 |
|---|---|
| `report()` 结论只发不读 | ✅ 已测（同构：`post_send_only`） |
| **`grab_shot_jpeg()` 抓拍**：snapshot(CHN0) + 缩放 + 编码阶梯 | ❌ **没测** |
| **`send_image_raw()` 推图**：连 5001 推 `EIMG1` + 原始 JPEG | ❌ **没测** |

而正式脚本日志最后一行却停在 `report` 的 connect 上 —— 这个矛盾只有两种解释：

- **(a)** 死点其实在 `report` **之后**（抓拍 / 推图），硬复位只是丢掉了输出缓冲区的尾巴；
- **(b)** 死点确实在 `report` 的 connect，那差异只剩"启动期多做的那几件事"
  （`load_labels` / `flush_pending` / 启动时那次 `selftest_socket`）。

⚠️ 方法论：P7 的 `say()` 每行停 30ms，所以它的"最后一行"可信；**v11.2 之前的正式脚本没有这个节流** ——
硬复位会丢掉缓冲区里没刷出去的尾巴，"最后一行"未必是真正的死点。v11.2 已给关键路径（结论/抓拍/推图）
加上同样的 30ms 节流（`_say()` + `FLUSH_PACE_MS`）。

---

## 二、★ 本固件 `PipeLine.py` 的真实源码（P12 H0 从板上 `/sdcard/libs/` 读到的，157 行）

**这是 P12 最值钱的产出** —— 官方两套资料里都没有这份文件的副本，只有板上有。

```python
class PipeLine:
    def __init__(self, rgb888p_size=[224,224], display_mode="hdmi",
                 display_size=None, osd_layer_num=1, debug_mode=0):
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]   # :17
        ...
        self.osd_img = None

    def create(self, sensor=None, sensor_id=None, hmirror=None, vflip=None,
               fps=60, to_ide=True, crop_vertical=False):      # :35  ← **没有 ch1_frame_size**
        ...
        # ★ 只配两个通道（:108-120）
        if crop_vertical:
            ... (略，带 crop) ...
        else:
            # 通道0直接给到显示VO，格式为YUV420
            self.sensor.set_framesize(w=self.display_size[0], h=self.display_size[1], chn=CAM_CHN_ID_0)
            self.sensor.set_pixformat(Sensor.YUV420SP, chn=CAM_CHN_ID_0)
            # 通道2给到AI做算法处理，格式为RGB888
            self.sensor.set_framesize(w=self.rgb888p_size[0], h=self.rgb888p_size[1], chn=CAM_CHN_ID_2)
            self.sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_2)
        # ★ CHN1 从头到尾**没配过** ⇒ snapshot(chn=1) 必失败
        self.osd_img = image.Image(self.display_size[0], self.display_size[1], image.ARGB8888)  # :123
        sensor_bind_info = self.sensor.bind_info(x=0, y=0, chn=CAM_CHN_ID_0)
        Display.bind_layer(**sensor_bind_info, layer=Display.LAYER_VIDEO1)                     # :126
        self.sensor.run()

    def get_frame(self):                                          # :132
        self.cur_frame = self.sensor.snapshot(chn=CAM_CHN_ID_2)
        return self.cur_frame.to_numpy_ref()                      # 零拷贝 ulab ndarray

    def show_image(self, flag=None): ...                          # :139
    def get_display_size(self): return self.display_size          # :146

    def destroy(self):                                            # :150
        os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
        self.sensor.stop()
        Display.deinit()          # ⚠️ **没有** MediaManager.deinit()
```

四条对写代码直接有用的结论：

1. **`create()` 不接受 `ch1_frame_size`** ⇒ 「PipeLine 也能开 CHN1」那个先例属于另一套 SDK，本固件不适用。
2. **CHN1 根本没被配置** ⇒ `snapshot(chn=CAM_CHN_ID_1)` 必然 `RuntimeError: sensor(0) snapshot chn(1) failed(3)`。
3. **`display_size=None` 时会取 `[Display.width(), Display.height()]`**（`:99`）—— VIRT 下就是 640×480。
   正式脚本 `main()` 没传 `display_size`，所以 CHN0 恒为 640×480，与 P12 显式传 `[640,480]` **等价**。
4. **`destroy()` 不含 `MediaManager.deinit()`** ⇒ 一个脚本里反复建/拆 PipeLine 时要自己补
   （探针里就是这么补的：`pl.destroy()` 之后再 `Display.deinit()` + `MediaManager.deinit()`）。

---

## 三、P13 结果（2026-10-02 14:15）：**变体 B 一次成功 —— 抓拍通道定案 CHN1 RGB565**

| 变体 | 结果 |
|---|---|
| **B 裸 Sensor 三通道** | ✅ **一次成功**。CHN1 RGB565 q60 = **15179 字节**，肉眼正常彩色（天花板/灯管/网格全对）。判读指标：**绿 0% / 品红 0% / 灰带 0 行**（花图是 37~46% / 121 行）。AI 回归 ✔ `shape=(3,224,224) dtype=66` |
| **A PipeLine 后补配 CHN1** | ❌ **板级判死**。`set_framesize/set_pixformat(chn=1)` 不报错但**不生效**，取帧 `failed(3)`。根因（板上 `PipeLine.py:35-129` 全文已到手，PC 副本 `D:/software/zidai/Desktop/libs/`）：`create()` **末尾就 `sensor.run()`**，run 之后再配通道无效 |

- 证据图：`Python_Backend/uploads/edge_p13-B-q60_20261002_141547_469378.jpg`（记录已清，文件保留）。
- **落地**：`k230_classify.py` **v14-chn1-rgb565** 已改裸 Sensor 三通道（CHN0 显示 / CHN1 拍照 / CHN2 AI），
  收尾补上官方顺序 `sensor.stop → Display.deinit → MediaManager.deinit`。
- ⚠️ 上板 p13 之后**断电重上电**再跑正式脚本（探针 README 一贯规矩）。

### 判读方式（P12 的教训，别再犯）

⚠️⚠️ **"出字节 / 魔数对 / 字节数大" 证明不了内容正确** —— P12 就是只看字节数，把一张花图判成了 ✅。
所以 P13 把每张候选图 **POST 回 Flask**，落成
`Python_Backend/uploads/edge_p13-<A|B>-q<质量>_<微秒>.jpg`（文件名自带变体标记，不会认错），
再用 PC 侧工具判读：

```bash
D:/software/Python/Python311/python.exe .workbuddy/tmp/p13_judge.py list      # 指标 + 对比图
D:/software/Python/Python311/python.exe .workbuddy/tmp/p13_judge.py clean --yes  # 按 uid 清记录+图
```

**指标已用已知花图校准过**（阈值可信）：

| 图 | 绿% | 品红% | 灰带行 |
|---|---|---|---|
| 已知花图（13:43~13:45 那批） | **37~46%** | **15~21%** | **121 行** |
| 判据 | 绿+品红合计 **>40** ⇒ 还是 RGB565 误打包 | | **>50** ⇒ NV12 的 UV 平面被读出来了 |

（121 行 = 240~361 行，与"Y 平面占前 50%、UV 占接下来 25%"的 buffer 模型**严丝合缝**。）

### P13 判读结论表

| 观察 | 结论 |
|---|---|
| B 出图且肉眼正确 | 走路线 B 改正式脚本（裸 Sensor 三通道） |
| B 花/失败，A 正确 | 走路线 A（PipeLine + 自配 CHN1），**改动最小** |
| B、A 都花/失败 | 回 PC 反解兜底（`utils/edge_shot_decode.py`，Y 平面已验证能还原） |
| 任一变体里 CHN2 的 `to_numpy_ref()` 形状不是 `(3,224,224)` | 先别动正式脚本，AI 路被牵连了 |

各探针保留备查：`p10`（抓拍采集路径二分）、`p11`（kmodel 输入 dtype 体检）、
`p12`（抓拍通道选型 / dump PipeLine 源码，`DUMP_ONLY=True` 可在不碰相机时只 dump 源码）。

### P12 的用法（已跑完，保留备查）

**只用相机 + 编码：不联网 / 不推理 / 不写库。** 一次相机 init，顺序固定 H0 → create → H3 → H1 → H2：

| 步骤 | 做什么 | 实测结果 |
|---|---|---|
| **H0** | `open('/sdcard/libs/PipeLine.py')` → 打印 `__init__`/`create`/`destroy`/`get_frame` 源码 + `dir()` | ✅ 拿到 157 行全文，见上面第二节 |
| **H3** | `pl.get_frame()`（CHN2，AI 的输入口）回归 | ✅ `shape=(3,224,224) dtype=66`(=uint8) ⇒ AI 路不用动 |
| **H1** | CHN0（YUV420SP）**零转换**直接 `K._encode_jpeg` | ✅ 出真 JPEG（39430/17322/10632 字节）⇒ **路线乙成立** |
| **H2** | CHN1（PipeLine 空闲通道）snapshot → `_encode_jpeg` | ❌ `RuntimeError: sensor(0) snapshot chn(1) failed(3)`（CHN1 从没配过） |
| — | H1、H2 都失败 | 未触发（H1 就成立了）⇒ 路线丙（裸 Sensor 三通道）**不需要** |

⚠️ **本探针全程不调用 `to_rgb888`**（对 CHN0 活帧调它必掉电，P9/P10 已实测两次）。
⚠️ 跑完**断电重上电**；`DUMP_ONLY=True` 可在不碰相机的情况下只把官方源码 dump 出来。

条件执行（已无必要）：`p5_recover_delay.py`、`p6_gc_release.py`（P3 全 ✘ 才有意义）。

### ⚠️ 老探针的 preprocess 口径

`p3` / `p5` / `p6` 里各自复制了一份 `preprocess()`，还是 **float32 老口径**（末尾 `/ 255.0`）。
2026-10-02 起 kmodel 是 **uint8** 口径，**重跑它们之前先把 `/ 255.0` 删掉**，否则会报
`KPU run failed`。`p7` / `p8` / `p9` 已经改好（它们还有在役用途）。
—— 这也是当初不该在探针里复制业务代码的教训（P9/P10/P11 改成 `import k230_classify as K` 就够了）。

### P10 的用法（**第二个跑**）

**纯图像：不联网 / 不推理 / 不写库。** 只改两个开关，一次跑一档。

| 开关 | 取值 | 说明 |
|---|---|---|
| `SOURCE` | `"sensor"` **★ 现行默认** | **官方式**：`Sensor` 直接 `set_pixformat(Sensor.RGB888)`，**全程零转换**；还会 `Display.show_image()` 让你在 IDE 里看图 |
| | `"pipeline"` ❌ 已判死 | 现状老路：`PipeLine → snapshot(chn=CHN0) → 转换 → 编码`。**无参 `to_rgb888()` 也掉电**，别再跑 |
| `VARIANT` | `1` | `img.to_rgb888()` —— 无参（**实测也掉电**，2026-10-02） |
| | `2` | `img.to_rgb565()` —— 无参，内存更省 |
| | `3` | `img.to_rgb888(x_scale=.5, y_scale=.5)` —— **已知掉电** |
| | `4` | `img.to_rgb888(0.5, 0.5)` —— 位置参数（**已知掉电**） |
| | `5` | **内省**：不转换，只打印 `img` 的能力与 `to_rgb888.__doc__` |
| `PIPE_DISPLAY_SIZE` | `None`（默认） | 与正式脚本一致（实测 CHN0 = 640×480） |
| | `[320,240]` | 小图对照 —— 验证"**尺寸在 sensor 上定，根本不用缩放**" |

⚠️ `VARIANT=3/4` 是已知会让板子掉电的两条；跑完必须断电重上电。
⚠️ `SOURCE="sensor"` 也会**独占 sensor**，结尾 `sensor.stop()` + 崩了就按 reset。

### P11 的用法（**先跑这个**）

**不联网、不用相机、不写库**，同一个 4 步调用形式，只改"喂什么进去"。**三段对照**：

| 段 | 开关 | 喂什么 | 参数出处 |
|---|---|---|---|
| ① **官方 kmodel** | `RUN_OFFICIAL=True` | `/sdcard/examples/kmodel/yolov8n_320.kmodel` + `uint8` **NCHW** `(1,3,320,320)` | 路径 `object_detect_yolov8n.py:185`；形状 `:63`；dtype `:55` —— **全部来自官方源码，不是猜的** |
| ② 我们的 kmodel | `RUN_OUR_UINT8=True` | `/sdcard/model.kmodel`（**新 3.48MB 那份**）+ `uint8` NHWC `(1,224,224,3)` | 官方 `Ai2d` 口径 + `to_kmodel.py: INPUT_MODE="uint8"` |
| ③ 我们的 kmodel | `RUN_OUR_FLOAT32=False` | 同上但 `float32` | 老口径，旧 kmodel 才吃这个 |

**2026-10-02 已跑过的结果**：① **0.027s** ✔ / ②（旧 kmodel）`RuntimeError: KPU run failed`。
⇒ 换成新 kmodel 后重跑，**② 应该是 0.0x 秒**；若仍是 66s 或报错，把日志贴回来。

**判读**：
- **② 秒出** ⇒ 66 秒闭环，「没有 KPU 段」这个诊断成立；
- **② 仍 66s** ⇒ 段数对了但 KPU 还是没在干活，回头查 `to_kmodel.py` 的 `target` / 插件路径；
- **② 报错** ⇒ 新 kmodel 不是 uint8 输入，说明重转没生效（检查是不是换了文件）。

**重转配方**（已落进 `to_kmodel.py`，`INPUT_MODE="uint8"`）：

```python
compile_options.target        = 'k230'
compile_options.input_shape   = [1, 224, 224, 3]   # preprocess=True 时必填
compile_options.input_layout  = 'NHWC'
compile_options.preprocess    = True
compile_options.input_type    = 'uint8'            # 官方口径
compile_options.input_range   = [0, 1]             # uint8 时必填：0~255 -> 0~1
compile_options.mean          = [0, 0, 0]
compile_options.std           = [1, 1, 1]          # 归一化已在 Dequantize 做过
```

校准集同步改成 **uint8 原值 0~255**（`arr[None, ...]`，不再 `/255.0`）。
⚠️ 产出后**必看大小**：约 **3.48 MB / 2 段** 才算对；12MB 左右就是又退化成 CPU 模型了。

### P9 的用法（已跑完，保留备查）

**设计要点：P9 不重写业务代码。** 它 `import k230_classify as K`，直接调用
`K.send_round` / `K.report` / `K.grab_shot_jpeg` / `K.send_image_raw`（只在外面套一层计时打点 +
`mem_free`）。**测的就是正式脚本那条真路**，不会再有"探针和正式脚本不一样"的扯皮。

它会把 `K.TRANSPORT` 钉回 `"http"`（P9 就是要测这条老路），并 `K._pl = pl` 让 `_grab_shot_raw` 能拿到 sensor。

开关（一次只动一个）：

| 开关 | 默认 | 作用 |
|---|---|---|
| `DO_REPORT` | True | a) 结论 POST（只发不读）—— P7 已证清白，这里当"同轮前置" |
| `DO_SHOT` | True | b) 抓拍：snapshot(CHN0) + 缩放 + 编码阶梯 |
| `DO_RAW_TCP` | True | c) 推图：连 5001 推 `EIMG1` + 原始 JPEG |
| `SHOT_LADDER` | `"script"` | 改 `"small"` = 梯度从最小档起步（0.25 x q40），堆只有 3.8MB 时更稳 |

⚠️ `DO_REPORT=True` **会真的写入库记录**：`disease="P9probe"`，未确诊的 30 分钟后服务端自动清。
⚠️ 跑之前确认 Flask 在跑（`/api/edge/status` 里 `raw_image=True`），否则推图必失败。
⚠️ 跑之前先断电重上电一次。

### 官方 yolov8n 对照（已跑完，2026-10-02）

`2.机器视觉\8.AI视觉(KPU)\6.物体检测（yolov8n）\object_detect_yolov8n.py` 原封不动跑：
**实时、不到 1 秒**（跑完按停止时报的 `Exception: IDE interrupt` 是中断打在阻塞的
`PipeLine.show_image` 里，**不是错误**）。

⇒ 同板、同 PipeLine、同相机下官方毫秒级，只有我们 66 秒：
**KPU 完全正常，"板子/固件不工作"和"供电不足"两个假设都被推翻**；
锅在 **我们转出来的 `model.kmodel`**（输入类型，见上表发现 1），P11 去钉。

---

## 四、判读表

| 结果 | 含义 | 下一步 |
|---|---|---|
| **P0 挂** | 问题在网络/环境本身 | 后面全不用跑 |
| P1/P2/P4 任一挂 | PipeLine / KPU / 采集路径伤网络 | 已排除（全 ✔） |
| **P3 全 ✔** | 组合无罪 | 已排除 |
| **P7 全 ✔** | 轮前 4 步无罪 | 已排除 ⇒ 跑 **P9** |
| **P9 崩在 b) 抓拍**（已发生） | 崩在 `to_rgb888(...)`，**不是内存**（640×480 只要 225KB，堆有 3.8MB） | 已跑 P10：`VARIANT=1` **也崩** ⇒ 走 `SOURCE="sensor"` |
| **P9 崩在 c) 推图** | 尚未到达（死在 b 之前）；真要到了才查大包上行 | 先把图压到 160×120 q50（≈4KB）验证 |
| ~~P10 VARIANT=1/2 没崩~~ | **未发生** —— `VARIANT=1` 实测就掉电 | 放弃 pipeline 转换路，见下行 |
| **P10 VARIANT=1 也崩**（已发生） | CHN0 的 YUV420SP 走 image 模块转换**整体不可用**（不是缩放参数的锅） | 改 `SOURCE="sensor"`：官方 `Sensor` 直出 `RGB888`，零转换 |
| **P10 VARIANT=5** | 打印的是 `to_rgb888` 的**真实签名/可用方法** | 备用；已走 sensor 路就不用它了 |
| **P11 ① 官方 0.027s + ② 报错**（已发生） | 板子无罪；旧 kmodel 声明的不是 uint8 | 已重转成 uint8（3.48MB / 2 段），**重跑 P11 看 ② 是否秒出** |
| **P11 ②（新 kmodel）秒出** | 66 秒闭环：根因是旧 kmodel **没有 KPU 段** | 转去跑 P10 `SOURCE="sensor"` |
| **P12 H0**（已发生） | `create()` **没有 `ch1_frame_size`**，且**只配 CHN0/CHN2** | ⇒ 路线甲**判死**，不要再试 `pl.create(ch1_frame_size=…)` |
| **P12 H1 出字节**（已发生） | CHN0 的 YUV420SP **零转换**可直接 JPEG 编码 | ✅ **走路线乙**（已落地：删 `_to_rgb_scaled` 调用 + 删缩放阶梯） |
| **P12 H2 失败**（已发生） | CHN1 从没被 `create()` 配置过 ⇒ snapshot 必失败 | 正常现象，不是 bug；**别为此改 setup** |
| **P12 H3 `dtype=66`** | `ord('B')` = **uint8** | `get_frame()` 本来就给 uint8 ⇒ 与 uint8 kmodel 口径一致，AI 路不用动 |

---

## 五、硬约束

- 网络目标 `192.168.57.97:5000`；除 P7 的 `STEP_POST_REAL` / P9 的 `DO_REPORT` 外一律只打 `/api/edge/ping`（**不产生入库记录**）；
- 读响应预算用 `ticks_ms`；每条输出带 `[P?]` 前缀与耗时；
- 采集类探针结尾会 `pl.destroy()` / `sensor.stop()` 收尾，**但中途崩了就仍要按 reset**（否则下个探针报 `sensor(2) is already inited`）；
- 板端 ulab **没有 `np.float32`**，用 `float` / `np.float`（K230 单精度，等价 float32）；
  `np.uint8` **有**（官方 `Ai2d.py` / `AIBase.py` 在用）；
- P9 / P10 / P11 都需要 `k230_classify.py` 能被 import（已把 `/sdcard`、`/sdcard/probes`、`.`、`..` 塞进 `sys.path`）；
  它们复用 `K._say` / `K._mem_free` / `K._encode_jpeg`，不重写业务代码；
- **P10 / P11 都不联网、不写库**；P10 的 `SOURCE="sensor"` 会独占 sensor，
  结尾 `sensor.stop()` + `MediaManager.deinit()`，中途崩了仍要按 reset；
- 新增探针前先读官方源码（`/sdcard/libs/` 里的 `PipeLine.py` / `AIBase.py` / `Ai2d.py` / `Utils.py`）——
  **接口用法以官方源码为准，不凭印象写。**
