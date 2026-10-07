# -*- coding: utf-8 -*-
# 探针 P11：kmodel 输入体检 —— 那 66 秒到底是**我们的 kmodel** 还是**我们的调用方式**？
#
# ================= 不联网 / 不用相机 / 不写库（只跑模型）=================
#
# 【为什么要跑】
#   P8 实测（真实相机帧）：preprocess 0.011s → from_numpy 0.001s → set_input 0.000s
#                          → **kpu.run() 66.024s** → 取输出 0.000s
#   set_input_tensor 什么都不做、耗时却全在 run() 里 ⇒ **换算发生在 run() 内部**。
#
# 【官方源码怎么写的】（AIBase.py / Ai2d.py / object_detect_yolov8n.py，都已读过）
#       # object_detect_yolov8n.py:55
#       self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)
#       # object_detect_yolov8n.py:63
#       self.ai2d.build([1,3,h,w], [1,3,320,320])
#       # AIBase.inference()
#       self.kpu.set_input_tensor(i, tensors[i])      # ← 喂的是 **uint8 / NCHW**
#   而我们的 to_kmodel.py:70 是：
#       compile_options.input_type = 'float32' ; input_layout = 'NHWC' ; preprocess = False
#   ⇒ 结构上就是两处不同：**dtype（float32 vs uint8）** 和 **layout（NHWC vs NCHW）**。
#
# 【本探针干什么】三段对照，同一个 4 步调用形式、同一块板，只改"喂什么进去"：
#   ① 官方 `/sdcard/examples/kmodel/yolov8n_320.kmodel` + uint8 NCHW (1,3,320,320)
#        —— 路径/形状/dtype 全部来自官方源码（object_detect_yolov8n.py:185 / :55 / :63），不是猜的
#   ② 我们的 `/sdcard/model.kmodel` + uint8 NHWC (1,224,224,3)
#   ③ 我们的 `/sdcard/model.kmodel` + float32 NHWC (1,224,224,3)（现状，已知 ~66s，默认关）
#   （数值故意用全 0：本探针只测**耗时**，不判精度）
#
# 【判读】
#   ① 秒出 + ② 也是 66s  => 我们的**调用形式没问题**，是我们**转出来的 kmodel** 有问题
#   ① 也 66s             => 板子对任何 kmodel 都慢（但官方 yolov8n 例程实测 <1s，所以基本不可能）
#   ② 秒出               => 坐实 float32 输入就是元凶 ⇒ 重跑 to_kmodel.py（见下）
#   ② 直接报错            => kmodel 确声明 float32 输入 ⇒ 同样要重转
#
# 【重转配方（② 秒出或报错后就这么改 to_kmodel.py）】
#       compile_options.input_type   = 'uint8'          # 官方口径
#       compile_options.input_layout = 'NCHW'           # 官方口径（Ai2d 输出 NCHW）
#       compile_options.preprocess   = True             # 把 /255 烤进模型
#       compile_options.input_mean   = [0, 0, 0]
#       compile_options.input_std    = [255, 255, 255]
#   并同步：校准集从 `(img/255.0)[None]` 改成 **uint8 原值 0~255**、形状 NCHW。
#   ⚠️ 具体字段名/取值以本机 nncase 2.9.0 的 API 为准 —— 动手前先确认，别照抄。

import sys, time, gc

for _p in ("/sdcard", "/sdcard/probes", ".", ".."):
    if _p not in sys.path:
        sys.path.append(_p)

import nncase_runtime as nn
import ulab.numpy as np
import k230_classify as K        # 复用存量：K._say / K._mem_free / K.top1

TAG = "P11"

OUR_KMODEL = "/sdcard/model.kmodel"
OUR_SHAPE = (1, 224, 224, 3)

# 官方 kmodel 的三项参数全部来自官方源码，不是猜的：
OFFICIAL_KMODEL = "/sdcard/examples/kmodel/yolov8n_320.kmodel"   # object_detect_yolov8n.py:185
OFFICIAL_SHAPE = (1, 3, 320, 320)                                # :63 build([1,3,h,w],[1,3,320,320])
OFFICIAL_DTYPE = "uint8"                                         # :55 set_ai2d_dtype(..., np.uint8)

# ---- 开关 ----
RUN_OFFICIAL = True     # ① 官方 kmodel（最安全、最决定性，放第一个跑）
RUN_OUR_UINT8 = True    # ② 我们的 kmodel + uint8
RUN_OUR_FLOAT32 = False # ③ 现状（已知 ~66s，想看基线再开）
DO_INTROSPECT = True    # 打印 kpu / tensor 的真实能力（查档求证，别猜接口）


def tms():
    return time.ticks_ms()


def elapsed(t0):
    return time.ticks_diff(tms(), t0) / 1000.0


def pick_dtype(name):
    if name == "uint8":
        return np.uint8
    return np.float


def make_kpu(path):
    kpu = nn.kpu()
    try:
        kpu.load_kmodel(path)
    except Exception:
        with open(path, "rb") as f:
            kpu.load_kmodel(f.read())
    return kpu


def drop_kpu(kpu):
    """官方 AIBase.deinit() 就是这么收尾的。"""
    try:
        kpu.__del__()
    except Exception:
        pass
    try:
        nn.shrink_memory_pool()
    except Exception:
        pass
    gc.collect()


def one_run(kpu, dtype_name, label, shape):
    """造一个全 0 数组，走完整 4 步并逐段计时。返回 kpu.run() 耗时（失败返回 None）。"""
    K._say("[%s] ---- %s ----" % (TAG, label))
    K._say("[%s]   shape=%s dtype=%s" % (TAG, str(shape), dtype_name))
    dtype = pick_dtype(dtype_name)

    try:
        t0 = tms()
        x = np.zeros(shape, dtype=dtype)
        K._say("[%s]   造数组      %.3fs（mem_free=%s）"
               % (TAG, elapsed(t0), K._mem_free()))
    except Exception as e:
        K._say("[%s]   ✘ 造数组失败: %s: %s" % (TAG, type(e).__name__, e))
        return None

    try:
        t0 = tms()
        tensor = nn.from_numpy(x)
        d_from = elapsed(t0)
        K._say("[%s]   from_numpy  %.3fs → %s" % (TAG, d_from, type(tensor).__name__))
    except Exception as e:
        K._say("[%s]   ✘ from_numpy 失败: %s: %s" % (TAG, type(e).__name__, e))
        return None

    try:
        t0 = tms()
        kpu.set_input_tensor(0, tensor)
        d_set = elapsed(t0)
    except Exception as e:
        K._say("[%s]   ✘ set_input_tensor 失败（= 该输入与 kmodel 声明的不匹配）: %s: %s"
               % (TAG, type(e).__name__, e))
        return None

    t0 = tms()
    kpu.run()
    d_run = elapsed(t0)
    K._say("[%s]   set_input=%.3fs   ★ kpu.run()=%.3fs   ← 关键数字" % (TAG, d_set, d_run))

    try:
        arr = kpu.get_output_tensor(0).to_numpy()
        K._say("[%s]   输出 shape=%s（全 0 输入，仅看耗时，不判精度）" % (TAG, str(arr.shape)))
        if len(arr.shape) == 1 and len(arr) > 1:
            i = K.top1(arr)
            K._say("[%s]   top1=类%d 值=%.4f" % (TAG, i, float(arr[i])))
    except Exception as e:
        K._say("[%s]   取输出失败（只影响诊断，不影响耗时结论）: %s: %s"
               % (TAG, type(e).__name__, e))

    del x
    try:
        del tensor
    except Exception:
        pass
    gc.collect()
    return d_run


def introspect(kpu):
    K._say("[%s] ---- 内省 kpu ----" % TAG)
    try:
        K._say("[%s]   inputs_size=%s outputs_size=%s"
               % (TAG, kpu.inputs_size(), kpu.outputs_size()))
    except Exception as e:
        K._say("[%s]   inputs_size/outputs_size 取不到: %s" % (TAG, e))
    names = []
    try:
        for n in dir(kpu):
            if not n.startswith("_"):
                names.append(n)
    except Exception as e:
        K._say("[%s]   dir(kpu) 失败: %s" % (TAG, e))
    K._say("[%s]   dir(kpu) = %s" % (TAG, names))
    try:
        t = nn.from_numpy(np.zeros((1, 8), dtype=np.float))
        tn = []
        for n in dir(t):
            if not n.startswith("_"):
                tn.append(n)
        K._say("[%s]   dir(from_numpy(1x8)) = %s" % (TAG, tn))
    except Exception as e:
        K._say("[%s]   tensor 内省失败: %s" % (TAG, e))


# ==================================================================
print("=" * 60)
print(" 探针 P11：kmodel 输入体检（只跑模型，不用相机/网络）")
print(" official=%s our_uint8=%s our_float32=%s"
      % (RUN_OFFICIAL, RUN_OUR_UINT8, RUN_OUR_FLOAT32))
print("=" * 60)
K._say("[%s] mem_free(启动) = %s" % (TAG, K._mem_free()))

results = {}

# ---- ① 官方 kmodel（先跑：最安全，而且它快/慢直接定性"板子"还是"我们的模型"）----
if RUN_OFFICIAL:
    try:
        kpu = make_kpu(OFFICIAL_KMODEL)
        K._say("[%s] 官方 kpu 就绪（%s）mem_free=%s"
               % (TAG, OFFICIAL_KMODEL, K._mem_free()))
        if DO_INTROSPECT:
            introspect(kpu)
        results["official_uint8"] = one_run(
            kpu, OFFICIAL_DTYPE, "① 官方 yolov8n_320 + uint8 NCHW", OFFICIAL_SHAPE)
        drop_kpu(kpu)
    except Exception as e:
        K._say("[%s] ✘ 官方 kmodel 阶段失败: %s: %s" % (TAG, type(e).__name__, e))
        K._say("[%s]   （若报文件不存在，先确认 %s 在不在；不在就 RUN_OFFICIAL=False 跳过）"
               % (TAG, OFFICIAL_KMODEL))

# ---- ②③ 我们的 kmodel ----
if RUN_OUR_UINT8 or RUN_OUR_FLOAT32:
    try:
        kpu = make_kpu(OUR_KMODEL)
        K._say("[%s] 我们的 kpu 就绪（%s）mem_free=%s" % (TAG, OUR_KMODEL, K._mem_free()))
        if RUN_OUR_UINT8:
            results["our_uint8"] = one_run(kpu, "uint8", "② 我们的 kmodel + uint8 NHWC", OUR_SHAPE)
        if RUN_OUR_FLOAT32:
            results["our_float32"] = one_run(kpu, "float", "③ 我们的 kmodel + float32 NHWC（现状）",
                                             OUR_SHAPE)
        drop_kpu(kpu)
    except Exception as e:
        K._say("[%s] ✘ 我们的 kmodel 阶段失败: %s: %s" % (TAG, type(e).__name__, e))

print("-" * 60)
print("[%s] 耗时汇总：%s" % (TAG, results))
print("[%s] 判读：" % TAG)
print("   ① 快 + ② 也 66s  => 调用形式没问题，是**我们转出来的 kmodel** 有问题（按文件头配方重转）")
print("   ② 快             => 坐实 float32 输入就是那 66 秒的原因（重转成 uint8）")
print("   ② 报错            => kmodel 确声明 float32 输入，同样要重转")
print("   ① 也 66s         => 板子对任何 kmodel 都慢（官方例程实测 <1s，基本不可能，需回头查）")
print("[%s] 把上面全部日志贴回来" % TAG)
