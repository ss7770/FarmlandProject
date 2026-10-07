# -*- coding: utf-8 -*-
# 探针 P8：**推理计时拆解** —— 回答那个 64 秒到底花在哪
#
# 背景：P2/P3 实测一次推理 64~66 秒（P2: 64.11/63.97/64.05，P3: 66.04）。
#       224x224 的模型在 K230 上应该是**几十毫秒**级 —— 慢了约 1000 倍。
#       正式脚本用的是同一行代码，所以它的 [infer] 那行大概率也要等 60 多秒才出现。
#
# 本探针把一轮推理拆成 6 段分别计时，并做一次**小数组对照**：
#   · 小数组(1x8) 也慢         => 不是数据量问题，是 nn.from_numpy 本身有固定开销
#   · 小数组快、大数组按比例慢 => **逐元素转换**（ulab float -> nncase tensor）就是元凶
#   · 转换都很快、全在 kpu.run => 模型/固件层面的问题（跑的是软件回退？）
#
# 顺带验证 P2 发现的"每轮固定漏 43,616 字节"，看 gc.collect() 能不能收回。

import time, gc
from libs.PipeLine import PipeLine
import nncase_runtime as nn
import ulab.numpy as np

KMODEL_PATH = "/sdcard/model.kmodel"
ROUNDS = 3
TAG = "P8"


def tms():
    return time.ticks_ms()


def elapsed(t0):
    return time.ticks_diff(tms(), t0) / 1000.0


def preprocess(frame):
    sh = frame.shape
    if len(sh) == 4:
        c, h, w = sh[1], sh[2], sh[3]
    else:
        c, h, w = sh[0], sh[1], sh[2]
    x = frame.reshape((c, h * w)).transpose().copy().reshape((h, w, c))
    # ⚠️ 2026-10-02：kmodel 已改成 uint8 口径（/255 烤进模型），这里**不能**再除 255
    return x.reshape((1, h, w, c))


print("=" * 52)
print(" 探针 P8：推理计时拆解（每段单独计时）")
print("=" * 52)
print("[%s] mem_free(启动) = %d" % (TAG, gc.mem_free()))

pl = PipeLine(rgb888p_size=[224, 224], display_mode="virt")
pl.create()
print("[%s] PipeLine 就绪" % TAG)

kpu = nn.kpu()
try:
    kpu.load_kmodel(KMODEL_PATH)
except Exception:
    with open(KMODEL_PATH, "rb") as f:
        kpu.load_kmodel(f.read())
print("[%s] kpu 就绪" % TAG)

# ---- 小数组对照：1x8，只有 8 个数 ----------------------------------
small = np.zeros((1, 8), dtype=np.float)
t0 = tms()
ts = nn.from_numpy(small)
d_small = elapsed(t0)
print("[%s] 对照 nn.from_numpy(1x8) 耗时 %.3fs → tensor 类型 %s"
      % (TAG, d_small, type(ts).__name__))
del ts, small
gc.collect()

# ---- 正式形状：全局计时一次（不进循环，免得重复太久）----
frame = pl.get_frame()
t0 = tms()
x = preprocess(frame)
d_pre = elapsed(t0)
print("[%s] 形状 dtype=%s shape=%s itemsize=%s"
      % (TAG, getattr(x, "dtype", "?"), x.shape, getattr(x, "itemsize", "?")))
print("[%s] ① preprocess     %.3fs" % (TAG, d_pre))

t0 = tms()
tensor = nn.from_numpy(x)
d_from = elapsed(t0)
print("[%s] ② nn.from_numpy  %.3fs   <-- 若这里最大，就是逐元素转换的锅" % (TAG, d_from))
del x
gc.collect()

t0 = tms()
kpu.set_input_tensor(0, tensor)
d_set = elapsed(t0)
print("[%s] ③ set_input_tensor %.3fs" % (TAG, d_set))

t0 = tms()
kpu.run()
d_run = elapsed(t0)
print("[%s] ④ kpu.run()       %.3fs   <-- 若这里最大，是模型/固件层的问题" % (TAG, d_run))

t0 = tms()
row = kpu.get_output_tensor(0).to_numpy()[0]
d_out = elapsed(t0)
print("[%s] ⑤ 取输出          %.3fs" % (TAG, d_out))

# ---- 再跑两轮 kpu.run()，看是不是"每轮都这么慢" -------------------
for i in range(2, ROUNDS + 1):
    t0 = tms()
    kpu.set_input_tensor(0, tensor)      # 复用同一个 tensor，不再走 from_numpy
    d_set = elapsed(t0)
    t0 = tms()
    kpu.run()
    d_run = elapsed(t0)
    m0 = gc.mem_free()
    gc.collect()
    print("[%s] 第 %d 轮 set=%.3fs run=%.3fs | mem_free %d → gc 后 %d"
          % (TAG, i, d_set, d_run, m0, gc.mem_free()))

print("-" * 52)
print("[%s] 判读：" % TAG)
print("   ② 最大 → 问题在 ulab→tensor 的转换（可试：直接造 float32 的 ulab 数组 / 缩小输入）")
print("   ④ 最大 → 问题在模型或固件（kpu 在软件回退？换官方 kmodel 试同一段代码）")
print("   都正常(<1s) → 那 64 秒是别的地方，把本日志发我")
try:
    pl.destroy()
    print("[%s] PipeLine 已销毁（下一个探针可以不用按 reset）" % TAG)
except Exception as e:
    print("[%s] PipeLine 销毁失败: %s" % (TAG, e))
