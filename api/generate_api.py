# -*- coding: utf-8 -*-
"""
视频生成API路由 - 完整Pipeline版
支持：FFmpeg图片拼接 / Remotion动画视频 / TTS配音 / 字幕烧录
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
    from core.script_module import ScriptModule
    return ScriptModule()


def get_video_module():
    from core.video_module import VideoModule
    return VideoModule()


def get_subtitle_module():
    from core.subtitle_module import SubtitleModule
    return SubtitleModule()


def get_platform_module():
    from core.platform_module import PlatformModule
    return PlatformModule()


def get_remotion_bridge():
    from core.remotion_bridge import RemotionBridge
    return RemotionBridge()


def get_tts_module():
    from core.tts_module import TTSModule
    return TTSModule()


def storyboard_to_layout(storyboard: list, width: int = 1080, height: int = 1920) -> dict:
    """
    将 ScriptModule 生成的 storyboard 转换为 Remotion layout
    每个 storyboard item -> 一个 TimelineBox

    storyboard item: {
        "时间点": "0-3秒",
        "画面描述": "...",
        "字幕要点": "...",
        "时长": 3
    }
    """
    boxes = []
    y_start = 300
    box_height = 280
    box_spacing = 100
    fps = 30

    for i, item in enumerate(storyboard):
        duration_sec = item.get("时长", 5)
        duration_frames = duration_sec * fps
        show_from = sum(
            (storyboard[j].get("时长", 5) * fps)
            for j in range(i)
        )
        # 跳过时长为0的项
        if duration_frames <= 0:
            continue

        label = item.get("字幕要点", f"步骤{i+1}")
        # 截断过长文案
        if len(label) > 20:
            label = label[:20] + "..."

        colors = ["#4EC9B0", "#CE9178", "#DCDCAA", "#569CD6", "#D7BA7D"]
        color = colors[i % len(colors)]

        box = {
            "id": f"step_{i+1}",
            "label": label,
            "subLabel": item.get("画面描述", "")[:30],
            "x": 200,
            "y": y_start + i * (box_height + box_spacing),
            "width": 680,
            "height": box_height,
            "color": color,
            "fillColor": f"rgba({int(color[1:3], 16)}, {int(color[3:5], 16)}, {int(color[5:7], 16)}, 0.15)",
            "textColor": "#FFFFFF",
            "fontSize": 56,
            "showFrom": show_from,
            "durationInFrames": duration_frames,
            "zIndex": 2,
        }
        boxes.append(box)

    # 计算总时长
    total_frames = sum(
        max(1, item.get("时长", 5)) * fps
        for item in storyboard
    )

    return {
        "backgroundImage": "",
        "width": width,
        "height": height,
        "boxes": boxes,
        "arrows": [],
    }


@router.post("/api/generate/remotion")
async def api_generate_remotion(request: Request):
    """
    Remotion动画视频生成

    完整Pipeline:
    1. 选题推荐
    2. 脚本+分镜生成（LLM）
    3. 分镜 -> Remotion Timeline Layout
    4. Remotion 渲染动画视频（无音频）
    5. TTS 配音生成
    6. FFmpeg 合成视频+配音
    """
    logs = []
    try:
        data = await request.json()
        category = data.get('category', '')
        platforms = data.get('platforms', ['抖音'])
        topic_input = data.get('topic', None)  # 可选：指定选题

        # ========== 步骤1: 选题 ==========
        logs.append({'step': '选题', 'status': 'running', 'msg': '正在推荐选题...'})
        from core.topics_module import TopicsModule
        topics = TopicsModule(
            enable_cache=config.CACHE_CONFIG.get("enabled", True),
            preload_count=config.CACHE_CONFIG.get("preload_count", 500)
        )

        if topic_input:
            topic = {"title": topic_input, "category": category, "hook": "", "tags": []}
        else:
            topic_list = topics.recommend_topics(category=category if category else None, count=1)
            if not topic_list:
                logs.append({'step': '选题', 'status': 'error', 'msg': '未找到合适选题'})
                return JSONResponse({'error': '未找到合适选题', 'logs': logs}, status_code=400)
            topic = topic_list[0]

        logs.append({'step': '选题', 'status': 'success', 'msg': f'已选择: {topic.get("title", "")}'})

        # ========== 步骤2: 生成脚本+分镜 ==========
        logs.append({'step': '脚本', 'status': 'running', 'msg': '正在生成脚本和分镜...'})
        scripts = get_script_module()
        script_result = scripts.generate_script(topic, platforms[0] if platforms else '抖音', 30)
        logs.append({'step': '脚本', 'status': 'success', 'msg': '脚本生成完成'})

        storyboard = script_result.get('storyboard', [])
        if not storyboard:
            # 没有分镜时生成默认3步
            storyboard = [
                {"时间点": "0-5秒", "画面描述": "开场介绍", "字幕要点": topic.get("title", "欢迎观看"), "时长": 5},
                {"时间点": "5-10秒", "画面描述": "核心内容", "字幕要点": topic.get("hook", "一起来学习"), "时长": 5},
                {"时间点": "10-15秒", "画面描述": "总结号召", "字幕要点": "关注我们", "时长": 5},
            ]

        logs.append({'step': '分镜', 'status': 'running', 'msg': f'已生成 {len(storyboard)} 个分镜'})

        # ========== 步骤3: 生成 Remotion Layout ==========
        logs.append({'step': 'Remotion', 'status': 'running', 'msg': '正在构建动画布局...'})
        layout = storyboard_to_layout(storyboard)
        logs.append({'step': 'Remotion', 'status': 'success', 'msg': f'布局就绪: {len(layout["boxes"])} 个Box'})

        # ========== 步骤4: 启动Remotion服务并渲染 ==========
        logs.append({'step': '渲染', 'status': 'running', 'msg': '正在渲染动画视频（这可能需要30-60秒）...'})
        bridge = get_remotion_bridge()

        try:
            bridge.start_server(timeout=60)
        except Exception as e:
            logs.append({'step': '渲染', 'status': 'error', 'msg': f'Remotion服务启动失败: {e}'})
            return JSONResponse({'error': f'Remotion服务启动失败: {e}', 'logs': logs}, status_code=500)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        work_dir = config.OUTPUT_DIR / "_work" / f"remotion_{timestamp}"
        work_dir.mkdir(parents=True, exist_ok=True)

        # 渲染视频
        video_path = str(work_dir / "animated_video.mp4")
        result = bridge.render_sync(layout, output_path=video_path, timeout=300)

        if not result:
            logs.append({'step': '渲染', 'status': 'error', 'msg': 'Remotion渲染失败'})
            return JSONResponse({'error': 'Remotion渲染失败，请检查服务是否正常', 'logs': logs}, status_code=500)

        logs.append({'step': '渲染', 'status': 'success', 'msg': f'动画渲染完成: {result}'})

        # ========== 步骤5: TTS配音 ==========
        logs.append({'step': '配音', 'status': 'running', 'msg': '正在生成配音...'})
        tts = get_tts_module()
        audio_path = str(work_dir / "narration.mp3")
        full_script = script_result.get('full_script', topic.get('title', ''))

        try:
            tts_success = tts.generate_audio(
                text=full_script,
                output_path=audio_path,
            )
            if not tts_success or not Path(audio_path).exists():
                # TTS失败时跳过配音
                audio_path = None
                logs.append({'step': '配音', 'status': 'warning', 'msg': '配音生成失败，将使用静音版本'})
            else:
                logs.append({'step': '配音', 'status': 'success', 'msg': '配音生成完成'})
        except Exception as e:
            audio_path = None
            logs.append({'step': '配音', 'status': 'warning', 'msg': f'配音异常: {e}，跳过配音'})

        # ========== 步骤6: FFmpeg合成视频+配音 ==========
        logs.append({'step': '合成', 'status': 'running', 'msg': '正在合成最终视频...'})

        if audio_path and Path(audio_path).exists():
            # 有配音：混合动画视频原声和TTS配音
            final_path = str(work_dir / "final_with_audio.mp4")
            try:
                from core.video_module import get_video_module
                vm = get_video_module()
                # 直接替换音频
                success = vm.add_bgm(result, final_path, audio_path)
                if not success:
                    # fallback: 直接复制
                    shutil.copy2(result, final_path)
                    logs.append({'step': '合成', 'status': 'warning', 'msg': '音频混合失败，使用静音版'})
                else:
                    logs.append({'step': '合成', 'status': 'success', 'msg': '视频+配音合成完成'})
            except Exception as e:
                shutil.copy2(result, final_path)
                logs.append({'step': '合成', 'status': 'warning', 'msg': f'合成异常: {e}'})
        else:
            # 无配音：直接复制
            final_path = str(work_dir / "final_no_audio.mp4")
            shutil.copy2(result, final_path)
            logs.append({'step': '合成', 'status': 'success', 'msg': '无配音版本生成完成'})

        return JSONResponse({
            'success': True,
            'topic': topic,
            'script': script_result,
            'storyboard': storyboard,
            'remotion_video': result,
            'final_video': final_path,
            'logs': logs,
            'message': 'Remotion动画视频生成完成'
        })

    except Exception as e:
        import traceback
        error_msg = str(e)
        logs.append({'step': '系统', 'status': 'error', 'msg': f'发生错误: {error_msg}'})
        return JSONResponse({
            'error': error_msg,
            'logs': logs,
            'trace': traceback.format_exc()
        }, status_code=500)


# ==================== 原有的端点（保持兼容） ====================

@router.post("/api/generate/with-materials")
async def api_generate_with_materials(request: Request):
    """使用用户素材生成视频（FFmpeg版，保持原有逻辑）"""
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

        # 步骤1: 推荐选题
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

        # 步骤2: 生成脚本
        logs.append({'step': '脚本', 'status': 'running', 'msg': '正在生成口播脚本...'})
        script_result = scripts.generate_script(topic, platforms[0] if platforms else '抖音', 30)
        logs.append({'step': '脚本', 'status': 'success', 'msg': '脚本生成完成'})

        # 步骤3: 处理素材
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

        # 步骤4: 生成视频
        logs.append({'step': '剪辑', 'status': 'running', 'msg': '正在拼接视频帧...'})
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(config.OUTPUT_DIR / "_work" / f"video_{timestamp}.mp4")
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

        # 步骤5: 添加字幕
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

        # 步骤6: 多平台导出
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
    """一键生成视频（FFmpeg版，保持原有逻辑）"""
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
        output_path = str(config.OUTPUT_DIR / "_work" / f"video_{timestamp}.mp4")
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
