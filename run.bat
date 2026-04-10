@echo off
chcp 65001 >nul
title Offline-ShortVideo-Agent 短视频AI生产系统

echo.
echo  ╔══════════════════════════════════════════════════════════╗
echo  ║                                                          ║
echo  ║        Offline-ShortVideo-Agent 短视频AI生产系统           ║
echo  ║                                                          ║
echo  ║        零API · 零付费 · 100%%离线 · 无封号风险             ║
echo  ║                                                          ║
echo  ╚══════════════════════════════════════════════════════════╝
echo.

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 检查pip
pip --version >nul 2>&1
if errorlevel 1 (
    echo [错误] pip 未安装
    pause
    exit /b 1
)

:: 检查FFmpeg
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [警告] 未检测到FFmpeg
    echo 请安装FFmpeg: https://ffmpeg.org/download.html
    echo 或运行: winget install ffmpeg
    echo.
)

:: 检查Ollama
where ollama >nul 2>&1
if errorlevel 1 (
    echo [警告] 未检测到Ollama，脚本生成功能将受限
    echo 请安装Ollama: https://ollama.com/download
    echo 安装后运行: ollama pull qwen2:7b
    echo.
)

:: 检查依赖
echo [1/3] 检查Python依赖...
pip show faster-whisper >nul 2>&1
if errorlevel 1 (
    echo 正在安装 faster-whisper...
    pip install faster-whisper -q
)

pip show ollama >nul 2>&1
if errorlevel 1 (
    echo 正在安装 ollama...
    pip install ollama -q
)

:: 初始化数据库
echo [2/3] 初始化数据库...
python -c "from core.db_init import init_topics_db, insert_sample_topics; conn = init_topics_db(); insert_sample_topics(conn); conn.close()" 2>nul
if errorlevel 1 (
    echo [警告] 数据库初始化有问题，但继续启动...
)

:: 启动主程序
echo [3/3] 启动主程序...
echo.
python main.py

pause
