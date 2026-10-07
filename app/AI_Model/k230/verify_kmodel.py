# -*- coding: utf-8 -*-
"""kmodel 精度验证：转换后的 int8 kmodel 和原 float32 TFLite 判得一样吗？

⚠️⚠️ 2026-10-02 重要更正：**这个脚本验不了真正的 KPU 模型。**
    `nncase.Simulator` 只加载得动"单段(纯 CPU/StackVM)"的 kmodel；
    正常的 k230 模型有两段（stackvm + kpu），一律 RuntimeError
    —— 连官方在线平台导出的 kmodel 也加载不了。
    判别方法：读头部 offset 16，2 = 两段（正常），1 = 单段（退路模型）。
    ⇒ 这里当初那句"60/60 对齐 TFLite"，是在一个**没有 KPU 段**的模型上测的，
      对板上毫无意义（那种模型在板上用 CPU 跑，一次推理 66 秒）。
      **现在的精度只能上板验**（或用能跑 KPU 的官方工具链）。

为什么本来要做这一步：
    nncase 把 float32 压成 int8，**精度一定会掉**。掉多少、能不能接受，
    必须实测。如果不验证就往 K230 上部署，可能出现：
      - 原本 90% 的图，量化后只有 60% -> 大量掉到"疑似/拒识"档，演示时一个都不入库
      - 类别判错，把健康的判成病害
    这两种在 K230 上看板都不会报错，只表现为"识别一直没结果"，极难排查。

用法（⚠️ 本机 .venv-nncase 里没有 tensorflow，跑不通；仅作留档）：
    bash runenv.sh && ./.venv-nncase/Scripts/python.exe verify_kmodel.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_PACKAGES = os.path.join(HERE, '.venv-nncase', 'Lib', 'site-packages')

os.environ.setdefault('DOTNET_ROOT', r'C:\Program Files\dotnet')
os.environ.setdefault('NNCASE_COMPILER',
                      os.path.join(SITE_PACKAGES, 'nncase', 'Nncase.Compiler.dll'))
os.environ.setdefault('NNCASE_PLUGIN_PATH',
                      os.path.join(SITE_PACKAGES, 'nncase', 'modules'))
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

AI_DIR = os.path.dirname(HERE)
TFLITE_PATH = os.path.join(AI_DIR, 'model.tflite')
LABELS_PATH = os.path.join(AI_DIR, 'labels.txt')
KMODEL_PATH = os.path.join(HERE, 'model.kmodel')
CALIB_DIR = os.path.join(HERE, 'calib')
IMG_SIZE = 224


def load_labels():
    with open(LABELS_PATH, encoding='utf-8') as f:
        return [l.strip() for l in f if l.strip()]


def softmax_if_needed(y):
    """与服务端 utils/inference.py 完全同口径：总和≈1 就不再 softmax"""
    import numpy as np
    if abs(float(y.sum()) - 1.0) < 0.01:
        return y
    e = np.exp(y - y.max())
    return e / e.sum()


def main():
    import numpy as np
    from PIL import Image

    print('=' * 68)
    print('  kmodel vs TFLite 精度对比')
    print('=' * 68)

    labels = load_labels()

    # ---- 用模拟器跑 kmodel ----
    import nncase
    print('[1/3] 启动 k230 模拟器 ...')
    sim = nncase.Simulator()
    with open(KMODEL_PATH, 'rb') as f:
        sim.load_model(f.read())

    # ---- TFLite 参考实现 ----
    print('[2/3] 载入 TFLite 参考 ...')
    import tensorflow as tf
    interp = tf.lite.Interpreter(model_path=TFLITE_PATH)
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]

    # ---- 逐张对比 ----
    names = sorted(f for f in os.listdir(CALIB_DIR) if f.endswith('.png'))
    print('[3/3] 对比 %d 张 ...\n' % len(names))

    same = 0
    conf_diffs = []
    rows = []

    for name in names:
        img = Image.open(os.path.join(CALIB_DIR, name)).convert('RGB')
        arr = np.asarray(img)                                     # uint8 0~255
        xf = (arr.astype(np.float32) / 255.0)[None, ...]          # TFLite 口径
        xk = arr[None, ...].astype(np.uint8)                      # kmodel 口径（uint8）

        # TFLite
        interp.set_tensor(inp['index'], xf)
        interp.invoke()
        t = softmax_if_needed(interp.get_tensor(out['index'])[0].astype(np.float32))
        ti = int(t.argmax())
        tc = float(t[ti]) * 100

        # kmodel（模拟器）
        sim.set_input_tensor(0, nncase.RuntimeTensor.from_numpy(xk))
        sim.run()
        k = sim.get_output_tensor(0).to_numpy()[0].astype(np.float32)
        k = softmax_if_needed(k)
        ki = int(k.argmax())
        kc = float(k[ki]) * 100

        ok = (ti == ki)
        same += int(ok)
        conf_diffs.append(kc - tc)
        rows.append((name, labels[ti] if ti < len(labels) else ti, tc,
                     labels[ki] if ki < len(labels) else ki, kc, ok))

    # ---- 汇总 ----
    print('%-14s %-34s %7s | %-34s %7s  %s' %
          ('图', 'TFLite', 'conf', 'kmodel', 'conf', '一致'))
    print('-' * 108)
    for name, tlab, tc, klab, kc, ok in rows:
        print('%-14s %-34s %6.1f%% | %-34s %6.1f%%  %s' %
              (name, tlab, tc, klab, kc, 'OK' if ok else '** 不一致 **'))

    n = len(rows)
    print()
    print('=' * 68)
    print('  一致率      : %d/%d = %.1f%%' % (same, n, same * 100.0 / n))
    print('  平均置信度差: %+.2f 个百分点 (kmodel - TFLite)' % (sum(conf_diffs) / n))
    print('  最大置信度差: %+.2f 个百分点' % max(conf_diffs, key=abs))
    print('=' * 68)
    print()
    if same == n:
        print('[OK] 量化后所有图判定一致，可以上板。')
    elif same >= n * 0.9:
        print('[!] 大部分一致，少量差异。可上板，但演示时留意那几张。')
    else:
        print('[X] 一致率偏低！建议：')
        print('    1) 扩大/更换校准集（当前用的是 uploads 里 60 张，样本偏单一）')
        print('    2) 改用 ptq_options.calibrate_method = "Kld"')
        print('    3) 或在真机 K230 上用同样图片再测一次，模拟器与真机也可能有差异')
    return 0


if __name__ == '__main__':
    sys.exit(main())
