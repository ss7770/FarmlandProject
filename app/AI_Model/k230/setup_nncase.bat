@echo off
REM ============================================================
REM  nncase 2.9.0 环境一键安装（Windows）
REM
REM  为什么需要这个脚本：
REM    nncase 2.9.0 的 whl 最高只支持 Python 3.10，本机是 3.11 —— 直接
REM    pip install 必然失败。所以要在独立目录建一个 3.10 环境。
REM
REM  本脚本已被实测跑通（2026-10-01），踩过的坑都写在下面注释里。
REM ============================================================
setlocal
set K230DIR=%~dp0
set WHLDIR=%K230DIR%whl
set VENV=%K230DIR%.venv-nncase

echo.
echo ============================================================
echo   步骤 1/6：准备 Python 3.10
echo ============================================================
REM 本机原来只有 3.11。用 uv 装 3.10 最快（uv 已在本机可用）。
where uv >nul 2>nul
if errorlevel 1 (
    echo [!] 没装 uv。可以改用官方安装包：
    echo     https://www.python.org/downloads/release/python-31011/
    echo     装到 D:\software\Python\Python310 后重跑本脚本。
    pause
    exit /b 1
)
uv python install 3.10
uv venv --python 3.10 "%VENV%"
if errorlevel 1 ( echo [X] 创建 3.10 虚拟环境失败 & pause & exit /b 1 )
echo [OK] 虚拟环境：%VENV%

echo.
echo ============================================================
echo   步骤 2/6：检查 .NET
echo ============================================================
REM nncase 2.x 编译器是 .NET 写的。2.9.0 的 runtimeconfig 声明 net7.0，
REM 本机装了 7.0.20 和 9.0.10 两个 runtime，7.0.20 可直接满足。
dotnet --list-runtimes | findstr "Microsoft.NETCore.App 7."
if errorlevel 1 (
    echo [!] 没找到 .NET 7 运行时。nncase 2.9.0 声明依赖 net7.0。
    echo     下载：https://dotnet.microsoft.com/download/dotnet/7.0
    echo     注意装的是 Runtime，不是 SDK。
    pause
)

echo.
echo ============================================================
echo   步骤 3/6：检查 whl 文件
echo ============================================================
REM PyPI 上只有 nncase 2.10/2.11，没有 2.9.0。
REM 2.9.0 必须从 GitHub Release 下（本机代理可能挡 GitHub，见 README）。
if not exist "%WHLDIR%\nncase-2.9.0-cp310-cp310-win_amd64.whl" (
    echo [X] 缺少 nncase-2.9.0-cp310-cp310-win_amd64.whl
    echo     下载：https://github.com/kendryte/nncase/releases/tag/v2.9.0
    echo     放到 %WHLDIR%\
    pause & exit /b 1
)
if not exist "%WHLDIR%\nncase_kpu-2.9.0-cp310-cp310-win_amd64.whl" (
    echo [X] 缺少 nncase_kpu 包（注意要用 cp310 标签那份，本仓库已修好打包问题）
    pause & exit /b 1
)

echo.
echo ============================================================
echo   步骤 4/6：安装 nncase
echo ============================================================
uv pip install --python "%VENV%\Scripts\python.exe" --no-deps --link-mode=copy ^
    "%WHLDIR%\nncase-2.9.0-cp310-cp310-win_amd64.whl"
uv pip install --python "%VENV%\Scripts\python.exe" --no-deps --link-mode=copy ^
    "%WHLDIR%\nncase_kpu-2.9.0-cp310-cp310-win_amd64.whl"

echo.
echo ============================================================
echo   步骤 5/6：安装 Python 依赖
echo ============================================================
uv pip install --python "%VENV%\Scripts\python.exe" --link-mode=copy numpy pillow

echo.
echo ============================================================
echo   步骤 6/6：处理 libomp140（关键坑！）
echo ============================================================
REM Nncase.Runtime.Native.dll 依赖 libomp140.x86_64.dll。
REM 官方文档说复制到 C:\Windows\System32，但那要管理员权限；
REM **实测放在 site-packages 同目录也生效**，所以这里走免权限路线。
set SP=%VENV%\Lib\site-packages
if exist "%WHLDIR%\libomp140.x86_64.dll" (
    copy /Y "%WHLDIR%\libomp140.x86_64.dll" "%SP%\libomp140.x86_64.dll" >nul
    echo [OK] libomp140.x86_64.dll 已放入 site-packages
) else (
    echo [!] 没有 libomp140.x86_64.dll 源文件。
    echo     没有它 nncase 会报 "DLL load failed while importing _nncase"。
    echo     可从 intel-openmp 包提取（本仓库 whl 目录里已备好）。
)

echo.
echo ============================================================
echo   验证
echo ============================================================
set DOTNET_ROOT=C:\Program Files\dotnet
set NNCASE_COMPILER=%SP%\nncase\Nncase.Compiler.dll
set NNCASE_PLUGIN_PATH=%SP%\nncase\modules
set KMP_DUPLICATE_LIB_OK=True
"%VENV%\Scripts\python.exe" -c "import nncase; print('[OK] nncase', 'target k230 =', nncase.check_target('k230'))"

echo.
echo ============================================================
echo  安装完成。下一步：
echo    cd app\AI_Model\k230
echo    .venv-nncase\Scripts\python.exe prepare_calib.py    ^(生成校准集^)
echo    .venv-nncase\Scripts\python.exe to_kmodel.py        ^(转出 model.kmodel^)
echo    .venv-nncase\Scripts\python.exe verify_kmodel.py    ^(验证精度^)
echo ============================================================
pause
