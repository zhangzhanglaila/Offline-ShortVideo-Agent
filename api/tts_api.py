# -*- coding: utf-8 -*-
"""
TTS配音API路由
"""
import sys
import os
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, FileResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()


def get_tts_module():
    """获取TTS模块单例"""
    from core.tts_module import get_tts_module as _get_module
    return _get_module()


@router.get("/api/tts/voices")
async def api_get_voices():
    """获取可用的配音人列表"""
    try:
        tts = get_tts_module()
        voices = tts.get_available_voices()
        return JSONResponse({
            'success': True,
            'voices': voices,
            'default': tts.DEFAULT_VOICE
        })
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.post("/api/tts/generate")
async def api_generate_tts(request: Request):
    """生成TTS配音"""
    try:
        data = await request.json()
        text = data.get('text', '')
        voice = data.get('voice', 'zh-CN-XiaoxiaoNeural')
        rate = data.get('rate', '+0%')
        output_path = data.get('output', '')

        if not text:
            return JSONResponse({'error': '文本不能为空', 'status_code': 400})

        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = config.OUTPUT_DIR / "临时"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"tts_{timestamp}.wav")

        tts = get_tts_module()
        tts.voice = voice
        if rate:
            # 解析rate字符串，如 "+20%" 或 "-10%" 或 "0"
            rate_str = str(rate).replace('%', '').replace('+', '')
            try:
                rate_int = int(rate_str)
                tts.set_rate(rate_int)
            except:
                pass

        success = tts.generate_audio(text, output_path)

        if success:
            duration = tts.get_audio_duration(output_path)
            return JSONResponse({
                'success': True,
                'path': output_path,
                'duration': duration,
                'voice': voice
            })
        else:
            return JSONResponse({'error': 'TTS生成失败，请检查edge-tts是否安装', 'status_code': 500})

    except Exception as e:
        import traceback
        return JSONResponse({'error': str(e), 'trace': traceback.format_exc()}, status_code=500)


@router.post("/api/tts/generate-from-script")
async def api_generate_from_script(request: Request):
    """从脚本生成TTS配音"""
    try:
        data = await request.json()
        script = data.get('script', '')
        voice = data.get('voice', 'zh-CN-XiaoxiaoNeural')
        duration = data.get('duration', 30)

        if not script:
            return JSONResponse({'error': '脚本不能为空', 'status_code': 400})

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = config.OUTPUT_DIR / "临时"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"tts_script_{timestamp}.wav")

        from core.tts_module import generate_tts_from_script
        success, result_path = generate_tts_from_script(
            script=script,
            output_path=output_path,
            duration=duration,
            voice=voice
        )

        if success:
            from core.tts_module import get_tts_module
            tts = get_tts_module()
            actual_duration = tts.get_audio_duration(result_path)
            return JSONResponse({
                'success': True,
                'path': result_path,
                'duration': actual_duration,
                'voice': voice
            })
        else:
            return JSONResponse({'error': 'TTS生成失败', 'status_code': 500})

    except Exception as e:
        import traceback
        return JSONResponse({'error': str(e), 'trace': traceback.format_exc()}, status_code=500)


@router.get("/api/tts/audio/{filename}")
async def api_get_audio(filename: str):
    """获取生成的音频文件"""
    try:
        audio_path = config.OUTPUT_DIR / "临时" / filename
        if not audio_path.exists():
            audio_path = config.OUTPUT_DIR / filename

        if not audio_path.exists():
            return JSONResponse({'error': '文件不存在'}, status_code=404)

        return FileResponse(
            path=str(audio_path),
            media_type='audio/wav',
            filename=filename
        )
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)
