# -*- coding: utf-8 -*-
"""
视频生成API路由 - V2 Pipeline
支持：Remotion电影场景 / TTS配音 / FFmpeg合成 / 自动素材获取
"""
import sys
import os
import shutil
import json
import time
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()


# ==================== 模块获取 ====================

def get_script_module():
    from core.script_module import ScriptModule
    return ScriptModule()


def get_image_fetch_module():
    from core.image_fetch_module import ImageFetchModule
    return ImageFetchModule()


def get_remotion_bridge():
    from core.remotion_bridge import RemotionBridge
    return RemotionBridge()


def get_tts_module():
    from core.tts_module import TTSModule
    return TTSModule()


# ==================== V2: 视觉提示词生成器 ====================

def generate_visual_prompts(storyboard: list, topic_title: str) -> list:
    """为每个分镜生成视觉提示词（DeepSeek API）"""
    try:
        import os
        import requests as _http
        from config import get_cloud_llm_config

        cfg = get_cloud_llm_config()
        if not cfg["api_key"] or cfg["api_key"] == "sk-your-key-here":
            print("[VisualPrompt] 未配置 DeepSeek API Key，跳过视觉增强")
            raise ValueError("No API key")

        prompt = f"""为短视频分镜生成视觉描述词。

选题：{topic_title}

分镜内容：
{json.dumps(storyboard, ensure_ascii=False, indent=2)}

要求：
1. 为每个分镜生成一个英文图片描述（适合做背景，科技感、电影感）
2. 为每个分镜指定一个图库搜索关键词
3. 为每个分镜指定一个强调色（hex格式，如 #4EC9B0）
4. 输出JSON数组，每个元素：{{"visual_prompt":"...", "bg_keywords":"...", "accent_color":"#"}}

请直接输出JSON，不要有其他文字:"""

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": cfg["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 512
        }

        session = _http.Session()
        session.trust_env = False
        resp = session.post(
            f'{cfg["api_base"]}/chat/completions',
            json=payload,
            headers=headers,
            timeout=5,
        )
        session.close()
        resp.raise_for_status()
        content_text = resp.json()["choices"][0]["message"]["content"].strip()

        json_start = content_text.find("[")
        json_end = content_text.rfind("]") + 1
        if json_start >= 0 and json_end > json_start:
            visual_data = json.loads(content_text[json_start:json_end])
            enriched = []
            for i, item in enumerate(storyboard):
                vd = visual_data[i] if i < len(visual_data) else {}
                enriched.append({
                    **item,
                    "visual_prompt": vd.get("visual_prompt", "tech background dark blue"),
                    "bg_keywords": vd.get("bg_keywords", topic_title),
                    "accent_color": vd.get("accent_color", "#4EC9B0"),
                })
            return enriched
    except Exception as e:
        print(f"[VisualPrompt] 生成失败: {e}")

    # 降级：返回默认视觉数据
    default_colors = ["#4EC9B0", "#CE9178", "#DCDCAA", "#569CD6", "#D7BA7D"]
    enriched = []
    for i, item in enumerate(storyboard):
        enriched.append({
            **item,
            "visual_prompt": f"{topic_title} technology dark background",
            "bg_keywords": topic_title,
            "accent_color": default_colors[i % len(default_colors)],
        })
    return enriched

# ==================== V2: 素材获取 ====================

def fetch_background_image(
    keywords: str,
    output_dir: Path,
    fallback_color: str = "#0D1B2A"
) -> str:
    """
    根据关键词从 Pexels/Unsplash/Bing 获取背景图

    Returns: 本地文件路径 或 空字符串（失败时用纯色背景）
    """
    try:
        img_fetch = get_image_fetch_module()
        results, local_paths = img_fetch.fetch_and_download(keywords, count=3)

        if local_paths:
            chosen = local_paths[0]
            print(f"[素材] 获取背景图成功: {chosen}")
            return chosen
    except Exception as e:
        print(f"[素材] 获取失败: {e}")

    # 网络不可用时直接跳过（不阻塞渲染）
    print(f"[素材] 网络不可用，跳过背景图，使用渐变背景")
    return ""


# ==================== V2: Cinematic Layout 构建 ====================

def storyboard_to_cinematic_layout(
    storyboard: list,
    background_image: str = "",
    width: int = 1080,
    height: int = 1920
) -> dict:
    """
    将带 visual_prompt 的 storyboard 转换为 CinematicScene layout
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
        if duration_frames <= 0:
            continue

        label = item.get("字幕要点", f"步骤{i+1}")
        if len(label) > 25:
            label = label[:25] + "..."

        sub_label = item.get("画面描述", "")
        if len(sub_label) > 35:
            sub_label = sub_label[:35] + "..."

        # 使用分镜指定的 accent_color
        color = item.get("accent_color", "#4EC9B0")

        # rgba 填充色（基于 accent_color 半透明）
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        fill_color = f"rgba({r}, {g}, {b}, 0.18)"

        box = {
            "id": f"step_{i+1}",
            "label": label,
            "subLabel": sub_label,
            "x": 200,
            "y": y_start + i * (box_height + box_spacing),
            "width": 680,
            "height": box_height,
            "color": color,
            "fillColor": fill_color,
            "textColor": "#FFFFFF",
            "fontSize": 54,
            "showFrom": show_from,
            "durationInFrames": duration_frames,
            "zIndex": 2,
        }
        boxes.append(box)

    return {
        "backgroundImage": background_image,
        "backgroundImageAlt": "",
        "width": width,
        "height": height,
        "boxes": boxes,
        "arrows": [],
    }


# ==================== 原有 timeline layout（兼容） ====================

def storyboard_to_layout(storyboard: list, width: int = 1080, height: int = 1920) -> dict:
    """将 storyboard 转换为 TimelineScene layout（用于旧接口）"""
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
        if duration_frames <= 0:
            continue

        label = item.get("字幕要点", f"步骤{i+1}")
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

    return {
        "backgroundImage": "",
        "width": width,
        "height": height,
        "boxes": boxes,
        "arrows": [],
    }


# ==================== V2: Remotion 电影场景端点 ====================

@router.post("/api/generate/remotion")
async def api_generate_remotion(request: Request):
    """
    Remotion V2 电影场景视频生成

    完整Pipeline V2:
    1. 选题推荐
    2. 脚本+分镜生成（LLM）
    3. 视觉提示词生成（LLM）
    4. 图库素材获取（Pexels/Unsplash/Bing）
    5. CinematicScene 布局构建
    6. Remotion CinematicFlow 渲染
    7. TTS 配音生成
    8. FFmpeg 合成视频+配音
    """
    logs = []
    try:
        data = await request.json()
        category = data.get('category', '')
        platforms = data.get('platforms', ['抖音'])
        topic_input = data.get('topic', None)
        use_cinematic = data.get('cinematic', True)  # 是否使用电影场景

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
            storyboard = [
                {"时间点": "0-5秒", "画面描述": "开场介绍", "字幕要点": topic.get("title", "欢迎观看"), "时长": 5},
                {"时间点": "5-10秒", "画面描述": "核心内容", "字幕要点": topic.get("hook", "一起来学习"), "时长": 5},
                {"时间点": "10-15秒", "画面描述": "总结号召", "字幕要点": "关注我们", "时长": 5},
            ]

        logs.append({'step': '分镜', 'status': 'running', 'msg': f'已生成 {len(storyboard)} 个分镜'})

        # ========== V2 步骤3: 视觉提示词生成 ==========
        topic_title = topic.get("title", "")
        logs.append({'step': '视觉', 'status': 'running', 'msg': '正在生成视觉提示词...'})
        storyboard = generate_visual_prompts(storyboard, topic_title)
        logs.append({'step': '视觉', 'status': 'success', 'msg': '视觉提示词生成完成'})

        # ========== V2 步骤4: 获取背景素材 ==========
        logs.append({'step': '素材', 'status': 'running', 'msg': '正在获取背景素材...'})
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        work_dir = config.OUTPUT_DIR / "_work" / f"remotion_{timestamp}"
        work_dir.mkdir(parents=True, exist_ok=True)

        # 提取所有分镜的关键词，统一获取一张主背景图
        all_keywords = [item.get("bg_keywords", topic_title) for item in storyboard]
        primary_keyword = all_keywords[0] if all_keywords else topic_title

        bg_image = fetch_background_image(primary_keyword, work_dir)
        if bg_image:
            logs.append({'step': '素材', 'status': 'success', 'msg': f'背景图获取成功'})
        else:
            logs.append({'step': '素材', 'status': 'warning', 'msg': '背景图获取失败，将使用渐变背景'})

        # ========== V2 步骤5: 构建 CinematicScene 布局 ==========
        logs.append({'step': '布局', 'status': 'running', 'msg': '正在构建电影感布局...'})
        if use_cinematic:
            layout = storyboard_to_cinematic_layout(storyboard, bg_image)
        else:
            layout = storyboard_to_layout(storyboard)
        logs.append({'step': '布局', 'status': 'success', 'msg': f'布局就绪: {len(layout["boxes"])} 个Box'})

        # ========== 步骤6: 启动Remotion服务并渲染 ==========
        logs.append({'step': '渲染', 'status': 'running', 'msg': '正在渲染电影场景视频（这可能需要30-60秒）...'})
        bridge = get_remotion_bridge()

        try:
            bridge.start_server(timeout=60)
        except Exception as e:
            logs.append({'step': '渲染', 'status': 'error', 'msg': f'Remotion服务启动失败: {e}'})
            return JSONResponse({'error': f'Remotion服务启动失败: {e}', 'logs': logs}, status_code=500)

        # 渲染视频
        video_path = str(work_dir / "animated_video.mp4")
        result = bridge.render_sync(layout, output_path=video_path, timeout=300)

        if not result:
            logs.append({'step': '渲染', 'status': 'error', 'msg': 'Remotion渲染失败'})
            return JSONResponse({'error': 'Remotion渲染失败，请检查服务是否正常', 'logs': logs}, status_code=500)

        logs.append({'step': '渲染', 'status': 'success', 'msg': f'动画渲染完成'})

        # ========== 步骤7: TTS配音 ==========
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
                audio_path = None
                logs.append({'step': '配音', 'status': 'warning', 'msg': '配音生成失败，将使用静音版本'})
            else:
                logs.append({'step': '配音', 'status': 'success', 'msg': '配音生成完成'})
        except Exception as e:
            audio_path = None
            logs.append({'step': '配音', 'status': 'warning', 'msg': f'配音异常: {e}，跳过配音'})

        # ========== 步骤8: FFmpeg合成 ==========
        logs.append({'step': '合成', 'status': 'running', 'msg': '正在合成最终视频...'})

        if audio_path and Path(audio_path).exists():
            final_path = str(work_dir / "final_with_audio.mp4")
            try:
                from core.video_module import VideoModule
                vm = VideoModule()
                success = vm.add_bgm(result, final_path, audio_path)
                if not success:
                    shutil.copy2(result, final_path)
                    logs.append({'step': '合成', 'status': 'warning', 'msg': '音频混合失败，使用静音版'})
                else:
                    logs.append({'step': '合成', 'status': 'success', 'msg': '视频+配音合成完成'})
            except Exception as e:
                shutil.copy2(result, final_path)
                logs.append({'step': '合成', 'status': 'warning', 'msg': f'合成异常: {e}'})
        else:
            final_path = str(work_dir / "final_no_audio.mp4")
            shutil.copy2(result, final_path)
            logs.append({'step': '合成', 'status': 'success', 'msg': '无配音版本生成完成'})

        return JSONResponse({
            'success': True,
            'topic': topic,
            'script': script_result,
            'storyboard': storyboard,
            'background_image': bg_image,
            'remotion_video': result,
            'final_video': final_path,
            'logs': logs,
            'message': 'Remotion V2 电影场景视频生成完成'
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
        from core.video_module import VideoModule
        from core.subtitle_module import SubtitleModule
        from core.platform_module import PlatformModule
        video = VideoModule()
        subtitle = SubtitleModule()
        platform_mod = PlatformModule()

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
            logs.append({'step': '剪辑', 'status': 'error', 'msg': '视频拼接失败'})
            return JSONResponse({'error': '视频生成失败', 'logs': logs}, status_code=500)

        logs.append({'step': '剪辑', 'status': 'success', 'msg': '视频剪辑完成'})

        # 步骤5: 添加字幕
        logs.append({'step': '字幕', 'status': 'running', 'msg': '正在烧录字幕...'})
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
            logs.append({'step': '字幕', 'status': 'warning', 'msg': '字幕烧录失败'})
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
        return JSONResponse({
            'error': error_msg,
            'logs': logs,
            'trace': traceback.format_exc()
        }, status_code=500)


@router.post("/api/generate")
async def api_generate(request: Request):
    """一键生成视频（FFmpeg版）"""
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
        from core.video_module import VideoModule
        from core.subtitle_module import SubtitleModule
        from core.platform_module import PlatformModule
        video = VideoModule()
        subtitle = SubtitleModule()
        platform_mod = PlatformModule()

        topic_list = topics.recommend_topics(
            category=category if category else None,
            count=1
        )

        if not topic_list:
            return JSONResponse({'error': '未找到合适的选题'}, status_code=400)

        topic = topic_list[0]

        script_result = scripts.generate_script(topic, platforms[0] if platforms else '抖音', 30)

        images = video.auto_select_materials(count=5)
        if not images:
            return JSONResponse({'error': '素材池为空'}, status_code=400)

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

        return JSONResponse({
            'success': True,
            'topic': topic,
            'works': works,
            'message': f'成功生成 {len(works)} 个平台的作品'
        })

    except Exception as e:
        import traceback
        return JSONResponse({'error': str(e), 'trace': traceback.format_exc()}, status_code=500)
