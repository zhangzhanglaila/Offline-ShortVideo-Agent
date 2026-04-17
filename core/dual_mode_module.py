# -*- coding: utf-8 -*-
"""
双模式视频生成模块
模式A【题材全自动生成】：题材→联网选题→脚本→TTS→配图抓取→动画视频→字幕→多轨道合成
模式B【素材智能剪辑】：用户上传素材→仅剪辑拼接→转场→字幕烧录→多轨道合成
"""
import os
import re
import json
import time
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

import config
from core.tts_module import get_tts_module, TTSModule
from core.subtitle_module import get_subtitle_module
from core.video_module import get_video_module
from core.animation_module import get_animation_module
from core.timeline_sync_module import get_timeline_module
from core.image_fetch_module import get_image_fetch_module
from core.topics_module import TopicsModule
from core.script_module import ScriptModule
from core.diagram_animation_module import get_diagram_module

# 日志回调 - 实时推送进度到前端
_dual_log_callback = None

def set_dual_log_callback(callback):
    global _dual_log_callback
    _dual_log_callback = callback

def _log(msg: str, level: str = 'info'):
    if _dual_log_callback:
        try:
            _dual_log_callback(msg, level)
        except Exception:
            pass


class DualModeVideoGenerator:
    """双模式视频生成器"""

    # 模式枚举
    MODE_AUTO = "mode_a"      # 题材全自动生成
    MODE_CLIP = "mode_b"      # 素材智能剪辑

    def __init__(self):
        self.tts = get_tts_module()
        self.subtitle = get_subtitle_module()
        self.video = get_video_module()
        self.animation = get_animation_module()
        self.diagram = get_diagram_module()
        self.timeline = get_timeline_module()
        self.image_fetch = get_image_fetch_module()
        self.topics = TopicsModule(
            enable_cache=config.CACHE_CONFIG.get("enabled", True),
            preload_count=config.CACHE_CONFIG.get("preload_count", 500)
        )
        self.script_mod = ScriptModule()

    # 技术讲座风格触发的赛道分类
    TECH_LECTURE_CATEGORIES = {"科技数码", "技术教程", "编程教学", "极客科普"}
    TECH_LECTURE_STYLE = "tech_lecture"  # 中性标识，不含外部关键词

    def generate_mode_a(
        self,
        topic_keyword: str = None,
        category: str = None,
        platform: str = "抖音",
        duration: int = 30,
        voice: str = "zh-CN-XiaoxiaoNeural",
        use_whisper_subtitle: bool = True,
        add_bgm: bool = True,
        fetch_images: bool = True,
        style: str = "normal",
    ) -> Dict:
        """
        模式A：题材全自动生成

        参数:
            topic_keyword: 题材关键词（联网选题用）
            category: 赛道分类
            platform: 目标平台
            duration: 视频时长（秒）
            voice: 配音人
            use_whisper_subtitle: 字幕是否用Whisper对齐
            add_bgm: 是否添加BGM
            fetch_images: 是否联网抓取配图
            style: 动画风格 ("normal"=默认KenBurns | "tech_lecture"=技术讲座风格)

        返回:
            生成结果字典
        """

        # 判断是否触发技术讲座风格
        is_tech_lecture = (
            style == self.TECH_LECTURE_STYLE or
            (category and category in self.TECH_LECTURE_CATEGORIES)
        )
        result = {
            "mode": self.MODE_AUTO,
            "success": False,
            "steps": [],
            "topic": None,
            "script": None,
            "audio": None,
            "images": [],
            "timeline": None,
            "video": None,
            "final_video": None,
        }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 临时工作目录（不暴露给用户）
        output_dir = config.OUTPUT_DIR / "_work" / timestamp
        output_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: 获取选题
        _log("🔍 正在获取选题...", 'info')
        print("[Mode A] Step 1: 获取选题...")
        if topic_keyword:
            # 有关键词 → 直接用LLM生成选题
            _log(f"📡 正在用AI根据「{topic_keyword}」生成选题...", 'info')
            generated = self._generate_topic_from_keyword(topic_keyword, category)
            if generated:
                topic_list = [generated]
                _log(f"✅ AI选题生成成功: {generated.get('title', '')}", 'info')
            else:
                _log("⚠️ AI连接失败，回退到本地选题库...", 'warn')
                topic_list = self.topics.recommend_topics(category=category, count=10)
        elif category:
            topic_list = self.topics.get_topics_by_category(category, limit=10)
        else:
            topic_list = self.topics.recommend_topics(count=5)

        if not topic_list:
            result["error"] = "未找到合适选题"
            _log("❌ 未找到合适选题", 'error')
            return result

        topic = topic_list[0]
        result["topic"] = topic
        result["steps"].append({"step": "topic", "status": "success", "data": topic.get("title")})
        _log(f"✅ 选题: {topic.get('title')}", 'info')
        print(f"  选题: {topic.get('title')}")

        # Step 2: 生成脚本
        _log("✍️ 正在生成AI脚本...", 'info')
        print("[Mode A] Step 2: 生成脚本...")
        script_result = self.script_mod.generate_script(topic, platform, duration)
        result["script"] = script_result
        result["steps"].append({"step": "script", "status": "success", "preview": script_result.get("full_script", "")[:100]})
        script_preview = script_result.get('full_script', '')[:80]
        _log(f"✅ 脚本生成完成: {script_preview}...", 'info')
        print(f"  脚本生成完成: {script_preview}...")

        # Step 3: 分句TTS配音
        _log("🎙️ 正在生成配音...", 'info')
        print("[Mode A] Step 3: TTS配音...")
        sentences = self._split_sentences(script_result.get("full_script", ""))
        audio_path = str(output_dir / "narration.wav")

        audio_success = self._generate_tts_segments(sentences, audio_path, voice)
        if not audio_success:
            result["error"] = "TTS生成失败"
            result["steps"].append({"step": "tts", "status": "failed"})
            _log("❌ TTS生成失败", 'error')
            return result

        result["audio"] = audio_path
        result["steps"].append({"step": "tts", "status": "success", "path": audio_path})
        _log("✅ 配音生成完成", 'info')
        print(f"  配音生成完成: {audio_path}")

        # Step 4: 联网抓取配图
        _log("🖼️ 正在抓取配图...", 'info')
        print("[Mode A] Step 4: 联网抓取配图...")
        if fetch_images:
            script_text = script_result.get("full_script", "")
            _, image_paths = self.image_fetch.fetch_by_script_keywords(script_text, count_per_keyword=2)
        else:
            image_paths = self.video.auto_select_materials(count=len(sentences))

        if not image_paths:
            image_paths = self.video.auto_select_materials(count=5)

        result["images"] = image_paths
        result["steps"].append({"step": "image_fetch", "status": "success", "count": len(image_paths)})
        _log(f"✅ 配图获取完成 ({len(image_paths)}张)", 'info')
        print(f"  配图: {len(image_paths)} 张")

        # Step 5: 时间轴同步
        _log("⏱️ 正在进行时间轴同步...", 'info')
        print("[Mode A] Step 5: 时间轴同步...")
        timeline = self._generate_timeline(sentences, image_paths)
        result["timeline"] = timeline
        result["steps"].append({"step": "timeline", "status": "success", "segments": len(timeline)})
        _log(f"✅ 时间轴同步完成 ({len(timeline)}个片段)", 'info')
        print(f"  时间轴: {len(timeline)} 个片段")

        # Step 6: 动画视频生成
        _log("🎬 正在生成动画视频...", 'info')
        print("[Mode A] Step 6: 动画视频生成...")
        raw_video_path = str(output_dir / "raw_video.mp4")

        if is_tech_lecture:
            # 技术讲座风格：优先使用2D流程图动画，其次用Ken Burns图文布局
            _log("📺 使用技术讲座风格生成", 'info')

            # 尝试从脚本中提取流程图布局描述（检查 full_script 和 raw_llm_response）
            script_text = script_result.get("full_script", "")
            raw_response = script_result.get("raw_llm_response", "")
            # 优先从 raw_llm_response 提取（包含完整的LLM输出，包括```diagram块）
            combined_text = raw_response if raw_response else script_text
            layout = self._parse_diagram_layout(combined_text, topic)

            if layout and len(layout) >= 2:
                # 有有效布局 → 使用2D流程图动画
                _log("📊 检测到架构/流程描述，生成2D示意图动画", 'info')
                diagram_success = self.diagram.generate_from_layout(
                    layout=layout,
                    output_path=raw_video_path,
                    fps=30,
                    auto_duration=True,
                )
                animation_success = diagram_success
                if animation_success:
                    _log("✅ 2D流程图动画生成完成", 'info')
                else:
                    _log("⚠️ 流程图生成失败，降级到Ken Burns图文风格", 'warn')
                    animation_success = self.animation.create_animated_video_from_segments(
                        images=image_paths if image_paths else [],
                        segments=timeline,
                        output_path=raw_video_path,
                        animation_style="ken_burns",
                        transition="fade"
                    )
            else:
                # 无布局描述 → 使用Ken Burns图文风格（顶部标题+左侧知识点+右侧代码）
                _log("📺 无流程图描述，使用Ken Burns图文风格", 'info')
                if image_paths:
                    bg_image = image_paths[0]
                else:
                    bg_image = image_paths[0] if image_paths else None

                lecture_title = topic.get("title", "技术分享")[:30]
                lecture_points = [s[:40] for s in sentences[:5] if s.strip()]
                code_match = re.search(r'```[\w]*\n(.*?)```', script_text, re.DOTALL)
                lecture_code = code_match.group(1).strip()[:300] if code_match else ""
                code_lang = "python"

                if bg_image and Path(bg_image).exists():
                    animation_success = self.animation.create_tech_lecture_video(
                        bg_image=bg_image,
                        output_path=raw_video_path,
                        title=lecture_title,
                        points=lecture_points,
                        code=lecture_code,
                        code_lang=code_lang,
                        duration=float(duration),
                        animation_style="ken_burns"
                    )
                else:
                    animation_success = False
                    _log("⚠️ 素材池无可用图片，无法生成Ken Burns视频", 'warn')
        else:
            # 默认Ken Burns动画风格
            animation_success = self.animation.create_animated_video_from_segments(
                images=image_paths,
                segments=timeline,
                output_path=raw_video_path,
                animation_style="ken_burns",
                transition="fade"
            )

        if not animation_success:
            result["error"] = "动画视频生成失败"
            result["steps"].append({"step": "animation", "status": "failed"})
            _log("❌ 动画视频生成失败", 'error')
            return result

        result["video"] = raw_video_path
        result["steps"].append({"step": "animation", "status": "success"})
        _log("✅ 动画视频生成完成", 'info')
        print(f"  动画视频: {raw_video_path}")

        # Step 7: 字幕生成
        _log("📝 正在生成字幕...", 'info')
        print("[Mode A] Step 7: 字幕生成...")
        srt_path = str(output_dir / "subtitle.srt")

        if use_whisper_subtitle:
            aligned_timeline, _ = self.timeline.sync_subtitles_to_audio(
                audio_path=audio_path,
                original_script=script_result.get("full_script", "")
            )
            self.subtitle.generate_srt(aligned_timeline if aligned_timeline else timeline, srt_path)
        else:
            self.subtitle.generate_srt(timeline, srt_path)

        result["steps"].append({"step": "subtitle", "status": "success", "path": srt_path})
        _log("✅ 字幕生成完成", 'info')

        # Step 8: 多轨道合成
        _log("🎵 正在进行多轨道合成...", 'info')
        print("[Mode A] Step 8: 多轨道合成...")
        final_video_path = str(output_dir / "final_video.mp4")

        bgm_path = None
        if add_bgm:
            available_bgm = self.video.get_available_bgm()
            if available_bgm:
                bgm_path = available_bgm[0]

        composite_success = self._multitrack_composite(
            video_path=raw_video_path,
            audio_path=audio_path,
            subtitle_path=srt_path,
            bgm_path=bgm_path,
            output_path=final_video_path
        )

        if not composite_success:
            result["error"] = "多轨道合成失败"
            result["steps"].append({"step": "composite", "status": "failed"})
            _log("❌ 多轨道合成失败", 'error')
            return result

        result["final_video"] = final_video_path
        result["steps"].append({"step": "composite", "status": "success"})
        result["success"] = True
        print(f"  最终视频: {final_video_path}")

        return result

    def generate_mode_b(
        self,
        material_paths: List[str],
        platform: str = "抖音",
        transition: str = "fade",
        add_bgm: bool = True,
        add_subtitles: bool = True,
        use_whisper: bool = False,
        duration_per_image: int = 4,
    ) -> Dict:
        """
        模式B：素材智能剪辑

        参数:
            material_paths: 用户上传的素材路径（图片/视频/音频）
            platform: 目标平台
            transition: 转场效果
            add_bgm: 是否添加BGM
            add_subtitles: 是否添加字幕
            use_whisper: 是否用Whisper识别字幕
            duration_per_image: 每张图片持续秒数

        返回:
            生成结果字典
        """
        result = {
            "mode": self.MODE_CLIP,
            "success": False,
            "steps": [],
            "materials": material_paths,
            "video": None,
            "final_video": None,
        }

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = config.OUTPUT_DIR / "_work" / timestamp
        output_dir.mkdir(parents=True, exist_ok=True)

        # 分离素材类型
        images = []
        videos = []
        audio = None

        for p in material_paths:
            path = Path(p)
            if not path.exists():
                continue
            ext = path.suffix.lower()
            if ext in ['.jpg', '.jpeg', '.png', '.webp']:
                images.append(str(path))
            elif ext in ['.mp4', '.avi', '.mov', '.mkv']:
                videos.append(str(path))
            elif ext in ['.mp3', '.wav', '.aac', '.m4a']:
                audio = str(path)

        print(f"[Mode B] 素材: {len(images)} 图片, {len(videos)} 视频, 音频: {bool(audio)}")

        # Step 1: 素材拼接
        print("[Mode B] Step 1: 素材拼接...")

        if videos:
            # 视频素材直接拼接
            raw_video_path = str(output_dir / "raw_video.mp4")
            concat_success = self._concat_videos(videos, raw_video_path)
            if not concat_success:
                result["error"] = "视频拼接失败"
                return result
            result["steps"].append({"step": "concat_videos", "status": "success"})
        elif images:
            # 图片素材生成视频
            raw_video_path = str(output_dir / "raw_video.mp4")
            video_success = self.video.create_video_from_images(
                images=images,
                output_path=raw_video_path,
                duration_per_image=duration_per_image,
                transition=transition
            )
            if not video_success:
                result["error"] = "图片转视频失败"
                return result
            result["steps"].append({"step": "images_to_video", "status": "success"})
        else:
            result["error"] = "没有有效素材"
            return result

        result["video"] = raw_video_path
        print(f"  素材拼接完成: {raw_video_path}")

        # Step 2: 添加音频
        final_video_path = raw_video_path

        if audio:
            print("[Mode B] Step 2: 添加音频...")
            final_video_path = str(output_dir / "with_audio.mp4")
            audio_success = self._add_narration(raw_video_path, audio, final_video_path)
            if audio_success:
                result["steps"].append({"step": "add_audio", "status": "success"})
            else:
                final_video_path = raw_video_path

        # Step 3: 添加BGM
        if add_bgm and not audio:
            print("[Mode B] Step 3: 添加BGM...")
            available_bgm = self.video.get_available_bgm()
            if available_bgm:
                bgm_path = available_bgm[0]
                temp_path = str(output_dir / "with_bgm.mp4")
                bgm_success = self.video.add_bgm(final_video_path, temp_path, bgm_path)
                if bgm_success:
                    final_video_path = temp_path
                    result["steps"].append({"step": "add_bgm", "status": "success"})

        # Step 4: 字幕处理
        if add_subtitles:
            print("[Mode B] Step 4: 字幕处理...")

            video_duration = self.video._get_media_duration(final_video_path)
            srt_path = str(output_dir / "subtitle.srt")

            if audio and use_whisper:
                # 用Whisper从配音识别字幕
                whisper_segments = self.timeline.transcribe_audio(audio)
                if whisper_segments:
                    self.subtitle.generate_srt(whisper_segments, srt_path)
                else:
                    # 降级：静默字幕
                    self.subtitle.generate_srt_from_script(" ", video_duration, srt_path)
            elif audio:
                # 从脚本生成分句字幕
                from core.script_module import ScriptModule
                script = ScriptModule().generate_script(
                    {"title": "素材剪辑"}, platform, int(video_duration)
                )
                sentences = self._split_sentences(script.get("full_script", ""))
                segments = []
                per_duration = video_duration / max(len(sentences), 1)
                for i, s in enumerate(sentences):
                    segments.append({
                        "start": i * per_duration,
                        "end": (i + 1) * per_duration,
                        "text": s
                    })
                self.subtitle.generate_srt(segments, srt_path)
            else:
                # 无配音只烧录空字幕（显示时间戳）
                self.subtitle.generate_srt_from_script(" ", video_duration, srt_path)

            # 烧录字幕
            subtitled_path = str(output_dir / "subtitled.mp4")
            burn_success = self.subtitle.burn_subtitles(final_video_path, srt_path, subtitled_path)
            if burn_success:
                final_video_path = subtitled_path
                result["steps"].append({"step": "burn_subtitles", "status": "success"})

        result["final_video"] = final_video_path
        result["success"] = True
        result["steps"].append({"step": "complete", "status": "success"})
        print(f"[Mode B] 完成: {final_video_path}")

        return result

    def _generate_topic_from_keyword(self, keyword: str, category: str = None) -> Optional[Dict]:
        """用LLM根据关键词直接生成选题"""
        import requests
        import os
        try:
            api_key = os.environ.get('DEEPSEEK_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')
            api_base = os.environ.get('DEEPSEEK_API_BASE', '') or os.environ.get('OPENAI_API_BASE', 'https://api.deepseek.com/v1')
            model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')

            if not api_key:
                return None

            cat_hint = f"赛道：{category}，" if category else ""

            prompt = f"""你是一个短视频选题专家。请根据用户输入的关键词生成一个爆款短视频选题。

关键词：{keyword}
{cat_hint}要求：
1. 标题要吸引人、有悬念或痛点
2. 符合短视频平台传播规律
3. 输出JSON格式：
{{"title": "标题", "hook": "3秒钩子", "category": "分类", "tags": ["标签1", "标签2"]}}

只输出JSON，不要其他文字："""

            response = requests.post(
                f'{api_base}/chat/completions',
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'model': model,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 256,
                    'temperature': 0.8
                },
                timeout=30,
                proxies={'http': None, 'https': None}
            )
            result = response.json()
            content = result['choices'][0]['message']['content']

            # 解析JSON
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                topic = json.loads(json_match.group())
                topic['id'] = 0  # 标记为LLM生成
                return topic
        except Exception as e:
            print(f"LLM生成选题失败: {e}")
        return None

    def _split_sentences(self, text: str) -> List[str]:
        """分句（按标点）"""
        if not text:
            return []
        pattern = r'[。！？.!?；;，,]'
        parts = re.split(pattern, text)
        return [p.strip() for p in parts if p.strip()]

    def _generate_tts_segments(self, sentences: List[str], output_path: str, voice: str) -> bool:
        """生成分句配音并合并"""
        if not sentences:
            return False

        temp_dir = Path(output_path).parent / "temp_tts"
        temp_dir.mkdir(parents=True, exist_ok=True)

        audio_files = []
        self.tts.voice = voice

        for i, sent in enumerate(sentences):
            if not sent.strip():
                continue
            seg_path = str(temp_dir / f"seg_{i:03d}.wav")
            if self.tts.generate_audio(sent, seg_path):
                audio_files.append(seg_path)

        if not audio_files:
            return False

        # 合并音频
        concat_list = temp_dir / "concat_list.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for af in audio_files:
                f.write(f"file '{Path(af).absolute().as_posix()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c:a", "libmp3lame",
            "-b:a", config.OUTPUT_AUDIO_BITRATE,
            output_path
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace', timeout=300)
            success = result.returncode == 0 and Path(output_path).exists()
        except:
            success = False

        # 清理
        for f in temp_dir.glob("*"):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        try:
            temp_dir.rmdir()
        except FileNotFoundError:
            pass

        return success

    def _generate_timeline(self, sentences: List[str], images: List[str]) -> List[Dict]:
        """生成时间轴"""
        if not sentences:
            return []

        total_duration = sum(len(s) / 4.0 for s in sentences)
        per_sentence_duration = total_duration / len(sentences) if sentences else 3.0

        timeline = []
        current_time = 0.0

        for i, sent in enumerate(sentences):
            img_idx = i % len(images) if images else 0
            end_time = current_time + per_sentence_duration

            timeline.append({
                "start": current_time,
                "end": end_time,
                "text": sent,
                "image_index": img_idx,
                "sentence_index": i
            })

            current_time = end_time

        return timeline

    def _multitrack_composite(
        self,
        video_path: str,
        audio_path: str,
        subtitle_path: str,
        bgm_path: str,
        output_path: str
    ) -> bool:
        """多轨道合成 - 使用两-pass方法避免filter_complex中subtitles的pad绑定问题"""
        import subprocess
        from pathlib import Path

        # 第一步：烧录字幕到视频（单独使用 -vf subtitles=）
        subtitled_video = Path(output_path).parent / "video_with_subs.mp4"

        if subtitle_path and Path(subtitle_path).exists():
            # 使用相对路径避免FFmpeg将绝对路径中的冒号解析为选项分隔符
            try:
                rel_sub_path = Path(subtitle_path).relative_to(Path.cwd()).as_posix()
            except ValueError:
                rel_sub_path = Path(subtitle_path).as_posix()

            cmd_sub = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", f"subtitles={rel_sub_path}",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", str(config.OUTPUT_CRF),
                "-c:a", "copy",
                str(subtitled_video)
            ]

            try:
                result = subprocess.run(cmd_sub, capture_output=True, text=True, timeout=600)
                if result.returncode != 0 or not subtitled_video.exists():
                    _log(f"字幕烧录失败: {result.stderr[:300]}", 'error')
                    return False
            except Exception:
                return False
        else:
            # 无字幕则跳过第一步
            subtitled_video = Path(video_path)

        # 第二步：混合音频
        input_video = str(subtitled_video)
        cmd_audio = ["ffmpeg", "-y", "-i", input_video, "-i", audio_path]

        input_idx = 2
        if bgm_path and Path(bgm_path).exists():
            cmd_audio.append("-i")
            cmd_audio.append(bgm_path)
            bgm_idx = input_idx
            input_idx += 1
        else:
            bgm_idx = None

        # 音频混合
        if bgm_idx:
            audio_filter = (
                f"[1:a]volume=1.0[narration];"
                f"[{bgm_idx}:a]volume={config.BGM_VOLUME}[bgmtrack];"
                f"[narration][bgmtrack]amix=inputs=2:duration=first[aout]"
            )
        else:
            audio_filter = "[1:a]volume=1.0[aout]"

        cmd_audio.extend([
            "-filter_complex", audio_filter,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy" if input_video == str(subtitled_video) else "libx264",
            "-preset", "fast",
            "-crf", str(config.OUTPUT_CRF),
            "-c:a", "aac",
            "-b:a", config.OUTPUT_AUDIO_BITRATE,
            "-shortest",
            output_path
        ])

        try:
            result = subprocess.run(cmd_audio, capture_output=True, text=True, timeout=600)
            return result.returncode == 0 and Path(output_path).exists()
        except:
            return False

    def _concat_videos(self, video_paths: List[str], output_path: str) -> bool:
        """拼接视频"""
        list_file = Path(output_path).parent / "concat_list.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for vp in video_paths:
                f.write(f"file '{Path(vp).absolute().as_posix()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(config.OUTPUT_CRF),
            "-pix_fmt", "yuv420p",
            output_path
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace', timeout=600)
            return result.returncode == 0 and Path(output_path).exists()
        except:
            return False
        finally:
            try:
                list_file.unlink()
            except FileNotFoundError:
                pass

    def _add_narration(self, video_path: str, audio_path: str, output_path: str) -> bool:
        """添加配音"""
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-filter_complex", "[0:a]volume=0.5[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", config.OUTPUT_AUDIO_BITRATE,
            "-shortest",
            output_path
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace', timeout=600)
            return result.returncode == 0 and Path(output_path).exists()
        except:
            return False

    def _parse_diagram_layout(self, script_text: str, topic: Dict) -> List[Dict]:
        """
        从脚本文本中提取流程图/架构图布局描述

        支持两种格式：
        1. Markdown 格式：```diagram ... ``` 包裹的布局描述
        2. 结构化描述：带有 "→" 或 "->" 箭头的流程描述

        返回 layout 列表，供 diagram_animation_module.generate_from_layout() 使用
        """
        layout = []

        # 预处理：将JSON字符串中的转义换行还原为普通换行
        # raw_llm_response 中的 `\n` 是转义字符，实际存储为 `\\n`
        script_text_clean = script_text.replace('\\n', '\n').replace('\\"', '"')

        # 格式1: 检查 ```diagram 代码块
        diagram_block = re.search(r'```diagram\s*\n(.*?)```', script_text_clean, re.DOTALL)
        if diagram_block:
            lines = diagram_block.group(1).strip().split('\n')
            return self._parse_dsl_layout(lines)

        # 格式2: 检查流程箭头模式 "模块A → 模块B → 模块C"
        arrow_pattern = re.search(r'([^\n→\->]{2,20})\s*(?:→|->)\s*([^\n→\->]{2,20})', script_text_clean)
        if arrow_pattern:
            # 提取所有箭头连接的节点
            nodes = []
            full_flow = re.findall(r'([^\n→\->]{2,20})\s*(?:→|->)\s*', script_text_clean)
            if full_flow:
                # 找到起始节点（箭头左边的第一个）
                first_node = re.match(r'^([^\n→\->]{2,20})', script_text_clean.strip())
                if first_node:
                    nodes.append(first_node.group(1).strip())
                nodes.extend(full_flow)
            if len(nodes) >= 2:
                layout = self._build_flow_layout(nodes)
                return layout

        return layout

    def _parse_dsl_layout(self, lines: List[str]) -> List[Dict]:
        """解析 DSL 格式的布局描述"""
        layout = []
        rect_index_map = {}
        scheme_colors = ["teal", "blue", "blue", "orange", "purple", "teal"]

        for i, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # 节点定义: [id] label (x, y, w, h)
            # 格式: [api] API网关 (400, 100) 或 [api] API网关 (400, 100, 200, 80)
            node_match = re.match(r'\[(\w+)\]\s+([^\(]+?)(?=\s*\(|\s*$)', line)
            if node_match:
                node_id = node_match.group(1)
                label = node_match.group(2).strip()

                # 单独匹配坐标
                coord_match = re.search(r'\((\d+),(\d+)(?:,(\d+),(\d+))?\)', line)
                if coord_match:
                    x = int(coord_match.group(1))
                    y = int(coord_match.group(2))
                    w = int(coord_match.group(3)) if coord_match.group(3) else 180
                    h = int(coord_match.group(4)) if coord_match.group(4) else 80
                else:
                    # 自动布局
                    x = 350
                    y = 150 + i * 120
                    w = 180
                    h = 80
                scheme = scheme_colors[i % len(scheme_colors)]

                idx = len(layout)
                rect_index_map[node_id] = idx
                layout.append({
                    "type": "rect",
                    "id": node_id,
                    "label": label,
                    "x": x, "y": y, "w": w, "h": h,
                    "scheme": scheme
                })
                continue

            # 箭头定义: [from] -> [to] label?
            arrow_match = re.match(r'\[(\w+)\]\s*(?:→|->)\s*\[(\w+)\](?:\s*["""](.+?)["""])?', line)
            if arrow_match:
                from_id = arrow_match.group(1)
                to_id = arrow_match.group(2)
                label = arrow_match.group(3) or ""
                if from_id in rect_index_map and to_id in rect_index_map:
                    layout.append({
                        "type": "arrow",
                        "from": from_id, "to": to_id,
                        "label": label
                    })

        return layout

    def _build_flow_layout(self, nodes: List[str]) -> List[Dict]:
        """将线性节点列表转换为网格布局"""
        layout = []
        scheme_colors = ["teal", "blue", "blue", "orange", "purple", "teal"]
        canvas_w = 1080
        node_w, node_h = 180, 80
        cols = 3
        padding = 40
        gap_x = (canvas_w - cols * node_w - (cols - 1) * padding) // 2

        for i, label in enumerate(nodes):
            col = i % cols
            row = i // cols
            x = gap_x + col * (node_w + padding)
            y = 150 + row * (node_h + padding + 30)
            scheme = scheme_colors[i % len(scheme_colors)]
            layout.append({
                "type": "rect",
                "id": f"node_{i}",
                "label": label[:15],
                "x": x, "y": y, "w": node_w, "h": node_h,
                "scheme": scheme
            })
            if i > 0:
                layout.append({
                    "type": "arrow",
                    "from": f"node_{i-1}",
                    "to": f"node_{i}",
                    "label": ""
                })

        return layout


# ==================== 便捷函数 ====================
_module_instance = None


def get_dual_mode_generator() -> DualModeVideoGenerator:
    """获取双模式生成器单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = DualModeVideoGenerator()
        # 设置日志回调
        if _dual_log_callback:
            _module_instance._log = _dual_log_callback
    return _module_instance


def generate_mode_a(**kwargs) -> Dict:
    """快速模式A生成"""
    return get_dual_mode_generator().generate_mode_a(**kwargs)


def generate_mode_b(material_paths: List[str], **kwargs) -> Dict:
    """快速模式B生成"""
    return get_dual_mode_generator().generate_mode_b(material_paths, **kwargs)
