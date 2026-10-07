# -*- coding: utf-8 -*-
"""用模拟器跑 kmodel，把每张校准图的输出 dump 成 json，供 3.11 侧（有 TF）与 TFLite 对比。

⚠️⚠️ 2026-10-02 实测结论：**PC 模拟器只能加载"单段(纯 CPU)"kmodel**
    —— 带 KPU 段的模型（也就是正常的 k230 模型）一律 `RuntimeError`，
    **连官方在线平台导出的 kmodel 也加载不了**。
    ⇒ 本脚本现在只对"退路模型"有意义；**真正能跑的 kmodel 必须在板上验精度**。
    判别方法：读头部 offset 16，官方 kmodel = 2（stackvm + kpu 两段），单段 = 1。

⚠️ 本脚本必须用 .venv-nncase 的 Python 3.10 跑（nncase 只到 cp310）：
    ./.venv-nncase/Scripts/python.exe dump_kmodel.py [kmodel 路径] [输出 json]

输入 dtype **自动探测**（2026-10-02 新增）：
    先按 kmodel 声明的 uint8（0~255 原值）喂；若 kpu/模拟器不接受，
    重新加载模型后回退 float32（0~1）。探测结果会打印出来，别猜。
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
SP = os.path.join(HERE, '.venv-nncase', 'Lib', 'site-packages')
os.environ.setdefault('DOTNET_ROOT', r'C:/Program Files/dotnet')
os.environ.setdefault('NNCASE_COMPILER', os.path.join(SP, 'nncase', 'Nncase.Compiler.dll'))
os.environ.setdefault('NNCASE_PLUGIN_PATH', os.path.join(SP, 'nncase', 'modules'))
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

import numpy as np
from PIL import Image
import nncase

KMODEL = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'model.kmodel')
OUT_JSON = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'kmodel_out.json')
CALIB_DIR = os.path.join(HERE, 'calib')


def load_sim(path):
    sim = nncase.Simulator()
    with open(path, 'rb') as f:
        sim.load_model(f.read())
    return sim


def make_input(arr, dtype_name):
    if dtype_name == 'uint8':
        return arr[None, ...].astype(np.uint8)
    return (arr.astype(np.float32) / 255.0)[None, ...]


def main():
    print('[sim] kmodel  : %s (%d bytes)' % (KMODEL, os.path.getsize(KMODEL)), flush=True)
    names = sorted(f for f in os.listdir(CALIB_DIR) if f.endswith('.png'))
    print('[sim] 校准图  : %d 张' % len(names), flush=True)

    probe = np.asarray(Image.open(os.path.join(CALIB_DIR, names[0])).convert('RGB'))

    # ---- dtype 自动探测：先试 uint8，失败就重载模型再试 float32 ----
    dtype_name = None
    sim = None
    for cand in ('uint8', 'float32'):
        s = load_sim(KMODEL)
        try:
            x = make_input(probe, cand)
            s.set_input_tensor(0, nncase.RuntimeTensor.from_numpy(x))
            s.run()
            y = s.get_output_tensor(0).to_numpy()
            print('[sim] 输入 dtype = %s ✔（试跑输出 shape=%s）' % (cand, y.shape), flush=True)
            dtype_name, sim = cand, s
            break
        except Exception as e:
            print('[sim] 输入 dtype = %s ✘ %s: %s' % (cand, type(e).__name__, str(e)[:160]),
                  flush=True)
            del s
    if sim is None:
        print('[X] 两种 dtype 都跑不通，见上面报错')
        return 1

    res = {}
    for name in names:
        arr = np.asarray(Image.open(os.path.join(CALIB_DIR, name)).convert('RGB'))
        if arr.shape[:2] != (224, 224):
            arr = np.asarray(Image.open(os.path.join(CALIB_DIR, name)).convert('RGB')
                             .resize((224, 224), Image.BILINEAR))
        x = make_input(arr, dtype_name)
        sim.set_input_tensor(0, nncase.RuntimeTensor.from_numpy(x))
        sim.run()
        y = sim.get_output_tensor(0).to_numpy()[0].astype(np.float32)
        res[name] = y.tolist()
        print('   %-14s top1=%2d  conf=%.2f%%' % (name, int(y.argmax()), float(y.max()) * 100),
              flush=True)

    with open(OUT_JSON, 'w') as f:
        json.dump({'input_dtype': dtype_name, 'pred': res}, f)
    print('[OK] 写出 %s（输入口径 %s），共 %d 张' % (OUT_JSON, dtype_name, len(res)), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
