# -*- coding: utf-8 -*-
"""
视频生成API路由
"""
import sys
import os
import shutil
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()


def get_script_module():
    """获取脚本模块单例"""
    from core.script_module import ScriptModule
    return ScriptModule()


def get_video_module():
    """获取视频模块单例"""
    from core.video_module import VideoModule
    return VideoModule()


def get_subtitle_module():
    """获取字幕模块单例"""
    from core.subtitle_module import SubtitleModule
    return SubtitleModule()


def get_platform_module():
    """获取平台模块单例"""
    from core.platform_module import PlatformModule
    return PlatformModule()


@router.post("/api/generate/with-materials")
async def api_generate_with_materials(request: Request):
    """使用用户素材生成视频"""
    try:
        data = await request.json()
        category = data.get('category', '')
        platforms = data.get('platforms', ['抖音', '小红书', 'B站'])
        material_paths = data.get('materials', [])

        from core.topics_module import TopicsModule
        topics = TopicsModule(
            enable_cache=config.CACHE_CONFIG.get("enabled", True),
            preload_count=config.CACHE_CONFIG.get("preload_count", 500)
        )
        scripts = get_script_module()
        video = get_video_module()
        subtitle = get_subtitle_module()
        platform_mod = get_platform_module()

        logs = []

        # ========== 步骤1: 推荐选题 ==========
        logs.append({'step': '选题', 'status': 'running', 'msg': '正在为你推荐热门选题...'})
        topic_list = topics.recommend_topics(
            category=category if category else None,
            count=1
        )

        if not topic_list:
            logs.append({'step': '选题', 'status': 'error', 'msg': '未找到合适的选题，请稍后重试'})
            return JSONResponse({'error': '未找到合适的选题', 'logs': logs}, status_code=400)

        topic = topic_list[0]
        logs.append({'step': '选题', 'status': 'success', 'msg': f'已选择: {topic.get("title", "")}'})

        # ========== 步骤2: 生成脚本 ==========
        logs.append({'step': '脚本', 'status': 'running', 'msg': '正在生成口播脚本...'})
        script_result = scripts.generate_script(topic, platforms[0] if platforms else '抖音', 30)
        logs.append({'step': '脚本', 'status': 'success', 'msg': '脚本生成完成'})

        # ========== 步骤3: 处理素材 ==========
        logs.append({'step': '素材', 'status': 'running', 'msg': '正在扫描和处理素材...'})
        images = []
        audio = None

        for m in material_paths:
            p = Path(m)
            if p.exists():
                ext = p.suffix.lower()
                if ext in ['.jpg', '.jpeg', '.png', '.webp']:
                    images.append(str(p))
                elif ext in ['.mp3', '.wav', '.aac', '.m4a']:
                    audio = str(p)

        if not images:
            images = video.auto_select_materials(count=5)

        if not images:
            logs.append({'step': '素材', 'status': 'error', 'msg': '素材池为空，请先上传素材'})
            return JSONResponse({'error': '素材池为空，请先上传素材', 'logs': logs}, status_code=400)

        logs.append({'step': '素材', 'status': 'success', 'msg': f'已加载 {len(images)} 个素材'})

        # ========== 步骤4: 生成视频 ==========
        logs.append({'step': '剪辑', 'status': 'running', 'msg': '正在拼接视频帧...'})
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(config.OUTPUT_DIR / "临时" / f"video_{timestamp}.mp4")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        duration_per_image = 5
        total_duration = len(images) * duration_per_image

        success = video.create_video_from_images(
            images=images,
            output_path=output_path,
            duration_per_image=duration_per_image,
            transition="fade",
            bgm_path=audio
        )

        if not success:
            logs.append({'step': '剪辑', 'status': 'error', 'msg': '视频拼接失败，请检查FFmpeg是否正确安装'})
            return JSONResponse({'error': '视频生成失败，请检查FFmpeg是否正确安装', 'logs': logs}, status_code=500)

        logs.append({'step': '剪辑', 'status': 'success', 'msg': '视频剪辑完成'})

        # ========== 步骤5: 添加字幕 ==========
        logs.append({'step': '字幕', 'status': 'running', 'msg': '正在烧录字幕到视频...'})
        script_content = script_result.get('full_script', '')
        final_video = output_path.replace('.mp4', '_subtitled.mp4')

        sub_success, srt_path = subtitle.generate_subtitle_video(
            video_path=output_path,
            script=script_content,
            output_path=final_video,
            duration=total_duration,
            use_whisper=False
        )

        if not sub_success:
            logs.append({'step': '字幕', 'status': 'warning', 'msg': '字幕烧录失败，将使用无字幕版本'})
            final_video = output_path
        else:
            logs.append({'step': '字幕', 'status': 'success', 'msg': '字幕烧录完成'})

        # ========== 步骤6: 多平台导出 ==========
        works = []
        for p in platforms:
            logs.append({'step': p, 'status': 'running', 'msg': f'正在生成{p}投稿包...'})
            platform_content = platform_mod.adapt_content(script_result, p)
            export_result = platform_mod.export_package(final_video, platform_content)

            if export_result['success']:
                works.append({
                    'platform': p,
                    'path': export_result['video_path'],
                    'output_dir': export_result['output_dir']
                })
                logs.append({'step': p, 'status': 'success', 'msg': f'{p} 投稿包已生成'})
            else:
                logs.append({'step': p, 'status': 'error', 'msg': f'{p} 投稿包生成失败'})

        # 清理临时文件
        try:
            if Path(output_path).exists() and output_path != final_video:
                Path(output_path).unlink()
        except Exception:
            pass

        return JSONResponse({
            'success': True,
            'topic': topic,
            'works': works,
            'logs': logs,
            'message': f'成功生成 {len(works)} 个平台的作品'
        })

    except Exception as e:
        import traceback
        error_msg = str(e)
        logs.append({'step': '系统', 'status': 'error', 'msg': f'发生错误: {error_msg}'})

        # 针对常见错误给出更友好的提示
        friendly_msg = error_msg
        if 'Hub' in error_msg or 'snapshot' in error_msg or 'ConnectTimeout' in error_msg:
            friendly_msg = "模型下载失败，无法连接到HuggingFace。请检查网络连接，或考虑使用本地模型。"
        elif 'FFmpeg' in error_msg or 'ffmpeg' in error_msg.lower():
            friendly_msg = "FFmpeg未正确安装或未加入PATH环境变量。请确保FFmpeg已安装并配置正确。"
        elif 'SSL' in error_msg or 'EOF' in error_msg:
            friendly_msg = "网络连接被中断，请检查网络或代理设置后重试。"

        return JSONResponse({
            'error': friendly_msg,
            'error_detail': error_msg,
            'logs': logs,
            'trace': traceback.format_exc()
        }, status_code=500)


@router.post("/api/generate")
async def api_generate(request: Request):
    """一键生成视频"""
    try:
        data = await request.json()
        category = data.get('category', '')
        platforms = data.get('platforms', ['抖音', '小红书', 'B站'])

        from core.topics_module import TopicsModule
        topics = TopicsModule(
            enable_cache=config.CACHE_CONFIG.get("enabled", True),
            preload_count=config.CACHE_CONFIG.get("preload_count", 500)
        )
        scripts = get_script_module()
        video = get_video_module()
        subtitle = get_subtitle_module()
        platform_mod = get_platform_module()

        # 1. 推荐选题
        topic_list = topics.recommend_topics(
            category=category if category else None,
            count=1
        )

        if not topic_list:
            return JSONResponse({'error': '未找到合适的选题'}, status_code=400)

        topic = topic_list[0]

        # 2. 生成脚本
        script_result = scripts.generate_script(topic, platforms[0] if platforms else '抖音', 30)

        # 3. 获取素材
        images = video.auto_select_materials(count=5)
        if not images:
            return JSONResponse({'error': '素材池为空，请先放入素材到 assets/素材池_待剪辑/'}, status_code=400)

        # 4. 生成视频
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(config.OUTPUT_DIR / "临时" / f"video_{timestamp}.mp4")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        success = video.create_video_from_images(
            images=images,
            output_path=output_path,
            duration_per_image=5,
            transition="fade",
            bgm_path=None
        )

        if not success:
            return JSONResponse({'error': '视频生成失败'}, status_code=500)

        # 5. 添加字幕
        script_content = script_result.get('full_script', '')
        final_video = output_path.replace('.mp4', '_subtitled.mp4')

        sub_success, srt_path = subtitle.generate_subtitle_video(
            video_path=output_path,
            script=script_content,
            output_path=final_video,
            duration=30,
            use_whisper=False
        )

        if not sub_success:
            final_video = output_path

        # 6. 多平台导出
        works = []
        for p in platforms:
            platform_content = platform_mod.adapt_content(script_result, p)
            export_result = platform_mod.export_package(final_video, platform_content)

            if export_result['success']:
                works.append({
                    'platform': p,
                    'path': export_result['video_path'],
                    'output_dir': export_result['output_dir']
                })

        # 清理临时文件
        try:
            if Path(output_path).exists():
                Path(output_path).unlink()
        except Exception:
            pass

        return JSONResponse({
            'success': True,
            'topic': topic,
            'works': works,
            'message': f'成功生成 {len(works)} 个平台的作品'
        })

    except Exception as e:
        import traceback
        return JSONResponse({'error': str(e), 'trace': traceback.format_exc()}, status_code=500)
