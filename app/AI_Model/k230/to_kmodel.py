# -*- coding: utf-8 -*-
"""TFLite -> kmodel 转换（nncase 2.9.0，target=k230）

版本对应：
    CanMV v1.1.0 及之后 -> nncase 2.9.0
    本机固件 CanMV v1.8-0-gc2d1f5c -> nncase 2.9.0 ✅

Windows 环境的三个必踩坑（本脚本已全部处理，别删）：
    ①  nncase 2.9.0 的 whl 只到 cp310，本机 3.11 装不上 -> 用 .venv-nncase (Python 3.10)
    ②  Nncase.Runtime.Native.dll 依赖 libomp140.x86_64.dll，
        官方文档说要放 System32，但**放在 site-packages 同目录也生效**，免管理员权限
    ③  nncase 包导入时会调 _nncase.initialize(Nncase.Compiler.dll)，
        必须能找到 Nncase.Compiler.dll，否则 CompileOptions() 直接段错误（不是报错！）
        -> 本脚本显式设置 NNCASE_COMPILER

用法：
    bash runenv.sh && ./.venv-nncase/Scripts/python.exe to_kmodel.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_PACKAGES = os.path.join(HERE, '.venv-nncase', 'Lib', 'site-packages')

# ---- 坑 ③：必须在 import nncase 之前设置，否则段错误 ----
os.environ.setdefault('DOTNET_ROOT', r'C:\Program Files\dotnet')
os.environ.setdefault('NNCASE_COMPILER',
                      os.path.join(SITE_PACKAGES, 'nncase', 'Nncase.Compiler.dll'))
os.environ.setdefault('NNCASE_PLUGIN_PATH',
                      os.path.join(SITE_PACKAGES, 'nncase', 'modules'))
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'      # libomp 与其它 BLAS 共存

AI_DIR = os.path.dirname(HERE)                   # -> app/AI_Model
TFLITE_PATH = os.path.join(AI_DIR, 'model.tflite')
CALIB_DIR = os.path.join(HERE, 'calib')
OUT_KMODEL = os.path.join(HERE, 'model.kmodel')

IMG_SIZE = 224
INPUT_SHAPE = [1, IMG_SIZE, IMG_SIZE, 3]

# ---- 输入口径（2026-10-02 改，原因见 k230/README.md「一次推理 66 秒」段）----
#   "uint8"   ★ 现行，官方口径：preprocess=True + input_type='uint8' + input_range=[0,1]。
#             官方 API 手册「前处理流程」：uint8 --Dequantize(input_range)--> float
#             --Normalization(mean,std)--> 模型。input_range=[0,1] 时 Dequantize 就是
#             "0~255 映射到 0~1"，mean/std 保持单位阵即可 —— 等价于板端原来的 x/255.0，
#             但现在由 kmodel 内部完成，板端只喂 uint8。
#             ⚠️ 手册硬约束：preprocess=True 时 input_shape 必填；input_type 只能是
#             uint8/float32；input_type=uint8 时 input_range 必填。
#   "float32" 老口径（v1）：板端自己 /255 后喂 float32（preprocess=False）。
#             ⚠️ 板端实测 kpu.run() = 66.0 秒（量化被挪到 CPU 上解释执行），留作回退。
INPUT_MODE = "uint8"


def main():
    print('=' * 64)
    print('  TFLite -> kmodel   (nncase 2.9.0 / target=k230)')
    print('=' * 64)

    if not os.path.isfile(TFLITE_PATH):
        print('[X] 找不到模型：%s' % TFLITE_PATH)
        return 1

    calib_files = sorted(f for f in os.listdir(CALIB_DIR) if f.endswith('.png')) \
        if os.path.isdir(CALIB_DIR) else []
    if not calib_files:
        print('[X] 校准集为空，先运行 prepare_calib.py')
        return 1

    print('模型      : %s (%.1f MB)' % (TFLITE_PATH,
                                        os.path.getsize(TFLITE_PATH) / 1048576))
    print('校准集    : %d 张' % len(calib_files))
    print('输入      : %s %s NHWC%s'
          % (INPUT_SHAPE, INPUT_MODE,
             '（板端喂 0~255，/255 由 kmodel 内部做）' if INPUT_MODE == 'uint8'
             else '（板端自己 /255 后喂 0~1）'))
    print()

    import nncase
    import numpy as np
    from PIL import Image

    print('[1/5] 构造 CompileOptions ...')
    compile_options = nncase.CompileOptions()
    compile_options.target = 'k230'
    compile_options.input_shape = INPUT_SHAPE
    compile_options.input_layout = 'NHWC'
    compile_options.output_layout = 'NHWC'
    if INPUT_MODE == 'uint8':
        # 把前处理（反量化 + 归一化）烤进 kmodel —— 官方口径，板上就不再是"纯 CPU 转换"
        compile_options.preprocess = True
        compile_options.input_type = 'uint8'
        compile_options.input_range = [0, 1]     # uint8 0~255 -> float 0~1
        compile_options.mean = [0, 0, 0]
        compile_options.std = [1, 1, 1]          # 归一化已在 Dequantize 做过，单位阵即可
    else:
        # 老口径：前处理交给板端自己做（板端 x/255.0 后喂 float32）
        compile_options.preprocess = False
        compile_options.input_type = 'float32'
    compile_options.dump_ir = False
    compile_options.dump_asm = False

    compiler = nncase.Compiler(compile_options)

    print('[2/5] 导入 TFLite ...')
    with open(TFLITE_PATH, 'rb') as f:
        compiler.import_tflite(f.read(), nncase.ImportOptions())

    print('[3/5] 配置 PTQ 校准集 ...')
    # 校准数据必须与 kmodel **声明**的输入同域：
    #   uint8   -> 0~255 原值（Dequantize/Normalization 由 kmodel 内部做）
    #   float32 -> 0~1（板端自己 /255）
    samples = []
    for name in calib_files:
        img = Image.open(os.path.join(CALIB_DIR, name)).convert('RGB')
        if img.size != (IMG_SIZE, IMG_SIZE):
            img = img.resize((IMG_SIZE, IMG_SIZE), Image.BILINEAR)
        arr = np.asarray(img)                                     # uint8 (224,224,3)
        if INPUT_MODE == 'uint8':
            samples.append(arr[None, ...].astype(np.uint8))       # (1,224,224,3) uint8
        else:
            samples.append((arr.astype(np.float32) / 255.0)[None, ...])

    ptq_options = nncase.PTQTensorOptions()
    ptq_options.samples_count = len(samples)
    ptq_options.calibrate_method = 'NoClip'   # 分类任务 NoClip 比 Kld 稳
    ptq_options.quant_type = 'uint8'
    ptq_options.w_quant_type = 'uint8'
    ptq_options.set_tensor_data([samples])    # 注意：外层再包一层 list（多输入用）
    compiler.use_ptq(ptq_options)

    print('[4/5] 编译中，请稍候（首次可能要几分钟）...')
    compiler.compile()

    print('[5/5] 导出 kmodel ...')
    kmodel = compiler.gencode_tobytes()
    with open(OUT_KMODEL, 'wb') as f:
        f.write(kmodel)

    size_mb = os.path.getsize(OUT_KMODEL) / 1048576
    print()
    print('=' * 64)
    print('  [OK] 转换成功')
    print('  kmodel : %s' % OUT_KMODEL)
    print('  大小   : %.2f MB' % size_mb)
    print('=' * 64)
    print('下一步：把 model.kmodel 和 labels.txt 拷到 K230 的 /sdcard/，')
    print('        在 CanMV IDE 里运行 k230_classify.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
