# -*- coding: utf-8 -*-
"""准备 nncase 量化校准集（calibration set）。

为什么需要这一步：
    nncase 把 float32 的 TFLite 压成 int8 的 kmodel 时，需要一小批**真实图片**
    来统计每一层激活值的动态范围。校准集的质量直接决定量化后的精度——
    校准集如果用噪点图，量出来的范围完全对不上真实场景，精度能掉 10 个点以上。

本脚本做什么：
    把 uploads/ 里 67 张**真实采集的农田图**统一 resize 成 224x224、存成 PNG，
    放到 k230/calib/ 下，供 to_kmodel.py 使用。

为什么用 uploads 而不是原始数据集：
    项目里没有留原始训练集（没有 datasets/ 目录），但 uploads 里全是
    从 ES32-CAM / D200 实拍上传的图，**分布和推理时的输入完全一致**，
    做校准反而比训练集更贴近实际。

用法：
    python prepare_calib.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.dirname(HERE))          # -> app
UPLOAD_DIR = os.path.join(APP_DIR, 'Python_Backend', 'uploads')
OUT_DIR = os.path.join(HERE, 'calib')

# 校准集张数：nncase 官方建议 20% 训练集，实际 30~100 张足够统计动态范围。
# 太多只会拖慢转换，精度提升可以忽略。
MAX_IMAGES = 60


def main():
    try:
        from PIL import Image
    except ImportError:
        print('[X] 缺少 Pillow：pip install pillow')
        return 1

    if not os.path.isdir(UPLOAD_DIR):
        print('[X] 找不到 uploads 目录：%s' % UPLOAD_DIR)
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)

    names = sorted(n for n in os.listdir(UPLOAD_DIR)
                   if n.lower().endswith(('.jpg', '.jpeg', '.png')))
    if not names:
        print('[X] uploads 里没有图片')
        return 1

    # 均匀抽样：不要只取前 N 张（时间上集中在同一天/同一光照）
    step = max(1, len(names) // MAX_IMAGES)
    picked = names[::step][:MAX_IMAGES]

    ok, bad = 0, 0
    for i, name in enumerate(picked):
        src = os.path.join(UPLOAD_DIR, name)
        try:
            img = Image.open(src).convert('RGB').resize((224, 224), Image.BILINEAR)
            # nncase 的 CalibDataset 接受 png/jpg 均可，png 无损、转换更稳
            img.save(os.path.join(OUT_DIR, 'calib_%03d.png' % i))
            ok += 1
        except Exception as e:
            bad += 1
            print('  [!] 跳过 %s: %s' % (name, e))

    print('[OK] 校准集已生成：%s' % OUT_DIR)
    print('     成功 %d 张，失败 %d 张（从 %d 张候选中均匀抽样）' % (ok, bad, len(names)))
    print('     下一步：运行 to_kmodel.py 完成转换')
    return 0


if __name__ == '__main__':
    sys.exit(main())
