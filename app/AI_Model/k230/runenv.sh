#!/bin/bash
# nncase 运行环境变量（在跑 to_kmodel.py / dump_kmodel.py 前 source 本文件）
#
# 为什么需要这些变量（详见 README.md 第 3.4~3.6 节）：
#   DOTNET_ROOT          - nncase 2.x 编译器是 .NET 程序
#   NNCASE_COMPILER      - 必须指向 Nncase.Compiler.dll，否则 CompileOptions() 直接段错误
#   NNCASE_PLUGIN_PATH   - KPU 后端插件目录（k230 target 需要）
#   KMP_DUPLICATE_LIB_OK - 允许 libomp 与其它 BLAS 共存，否则会 abort

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SP="$HERE/.venv-nncase/Lib/site-packages"

export DOTNET_ROOT="C:/Program Files/dotnet"
export NNCASE_COMPILER="$SP/nncase/Nncase.Compiler.dll"
export NNCASE_PLUGIN_PATH="$SP/nncase/modules"
export KMP_DUPLICATE_LIB_OK=True
