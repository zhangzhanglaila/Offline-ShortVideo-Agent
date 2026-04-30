# -*- coding: utf-8 -*-
"""
动画生成模块 - 基于FFmpeg动态效果
支持：Ken Burns缩放、文字逐字出现、转场动画、关键帧动画、技术讲座风格多图层合成
"""
import subprocess
import random
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from config import OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS, OUTPUT_CRF
from core.utils.ffmpeg_runner import run_ffmpeg_safe


class AnimationModule:
    """动画生成模块 - FFmpeg动态效果"""

    # 转场效果列表
    TRANSITIONS = ["fade", "dissolve", "wipe", "blur", "none"]

    def __init__(self):
        """初始化动画模块"""
        self.output_width = OUTPUT_WIDTH
        self.output_height = OUTPUT_HEIGHT
        self.output_fps = OUTPUT_FPS
        self.output_crf = OUTPUT_CRF

    def create_ken_burns_clip(self, image_path: str, output_path: str,
                               duration: float = 3.0,
                               zoom_in: bool = True,
                               zoom_range: Tuple[float, float] = (1.0, 1.3),
                               pan_x: float = 0.0,
                               pan_y: float = 0.0) -> bool:
        """
        创建Ken Burns效果（缩放+平移）

        参数:
            image_path: 输入图片路径
            output_path: 输出视频路径
            duration: 持续时间（秒）
            zoom_in: True=放大，False=缩小
            zoom_range: 缩放范围 (起始, 结束)
            pan_x: 水平平移量（-1到1）
            pan_y: 垂直平移量（-1到1）

        返回:
            是否成功
        """
        if not Path(image_path).exists():
            print(f"[错误] 图片不存在: {image_path}")
            return False

        zoom_start, zoom_end = zoom_range
        if not zoom_in:
            zoom_start, zoom_end = zoom_end, zoom_start

        # 构建zoompan滤镜
        # zoom变量从1.0开始，min(zoom+增量, 最大值) 实现平滑放大
        total_frames = int(duration * OUTPUT_FPS)
        zoom_delta = (zoom_end - zoom_start) / total_frames
        filter_str = (
            f"zoompan=z='min(zoom+{zoom_delta:.6f},{zoom_end})':"
            f"x=iw/2-(iw/zoom/2)+{int(pan_x * 100)}':"
            f"y=ih/2-(ih/zoom/2)+{int(pan_y * 100)}':"
            f"d={total_frames}:s={self.output_width}x{self.output_height}:fps={self.output_fps}"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-framerate", str(self.output_fps),
            "-i", image_path,
            "-vf", filter_str,
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(self.output_crf),
            "-t", str(duration),
            output_path
        ]

        return self._run_ffmpeg(cmd)

    def create_pan_zoom_clip(self, image_path: str, output_path: str,
                              duration: float = 3.0,
                              effect: str = "zoom_in") -> bool:
        """
        创建简化的推拉缩放效果

        参数:
            image_path: 输入图片路径
            output_path: 输出视频路径
            duration: 持续时间（秒）
            effect: 效果类型 (zoom_in/zoom_out/pan_left/pan_right/pan_up/pan_down/static)

        返回:
            是否成功
        """
        if not Path(image_path).exists():
            return False

        # 计算缩放参数
        zoom_effects = {
            "zoom_in": ("1.0", "1.5"),
            "zoom_out": ("1.5", "1.0"),
            "pan_left": ("1.0", "1.0"),
            "pan_right": ("1.0", "1.0"),
            "pan_up": ("1.0", "1.0"),
            "pan_down": ("1.0", "1.0"),
            "static": ("1.0", "1.0"),
        }

        zoom_start, zoom_end = zoom_effects.get(effect, ("1.0", "1.0"))

        # 构建缩放滤镜
        if effect.startswith("pan"):
            direction = effect.split("_")[1]
            pan_filter = self._get_pan_filter(direction, duration)
            filter_str = (
                f"scale={self.output_width}:{self.output_height}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={self.output_width}:{self.output_height},"
                f"{pan_filter}"
            )
        else:
            total_frames = int(duration * self.output_fps)
            zoom_delta = (float(zoom_end) - float(zoom_start)) / total_frames
            filter_str = (
                f"zoompan=z='min(zoom+{zoom_delta:.6f},{zoom_end})':"
                f"x=iw/2-(iw/zoom/2):y=ih/2-(ih/zoom/2):"
                f"d={total_frames}:s={self.output_width}x{self.output_height}:fps={self.output_fps}"
            )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-framerate", str(self.output_fps),
            "-i", image_path,
            "-vf", filter_str,
            "-pix_fmt", "yuv420p",
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(self.output_crf),
            output_path
        ]

        return self._run_ffmpeg(cmd)

    def _get_pan_filter(self, direction: str, duration: float) -> str:
        """获取平移滤镜"""
        # 计算移动距离（约10%的画面宽度/高度）
        pixels = int(min(self.output_width, self.output_height) * 0.1)

        pan_filters = {
            "left": f"crop={self.output_width}:{self.output_height}:{pixels}:0,zoompan=z=1:d={int(duration * self.output_fps)}:s={self.output_width}x{self.output_height}",
            "right": f"crop={self.output_width}:{self.output_height}:0:0,zoompan=z=1:d={int(duration * self.output_fps)}:s={self.output_width}x{self.output_height}",
            "up": f"crop={self.output_width}:{self.output_height}:0:{pixels},zoompan=z=1:d={int(duration * self.output_fps)}:s={self.output_width}x{self.output_height}",
            "down": f"crop={self.output_width}:{self.output_height}:0:0,zoompan=z=1:d={int(duration * self.output_fps)}:s={self.output_width}x{self.output_height}",
        }

        return pan_filters.get(direction, "")

    def create_text_animation(self, video_path: str, output_path: str,
                              text: str,
                              font_color: str = "white",
                              font_size: int = 56,
                              position: str = "bottom",
                              animation: str = "fade_in",
                              duration: float = 3.0,
                              border: bool = True) -> bool:
        """
        创建文字动画叠加

        参数:
            video_path: 输入视频路径
            output_path: 输出视频路径
            text: 显示的文字
            font_color: 字体颜色
            font_size: 字体大小
            position: 位置 (top/center/bottom)
            animation: 动画类型 (fade_in/slide_up/typewriter/none)
            duration: 文字持续时间
            border: 是否有描边

        返回:
            是否成功
        """
        # 位置参数
        positions = {
            "top": f"x=(w-text_w)/2:y=60",
            "center": f"x=(w-text_w)/2:y=(h-text_h)/2",
            "bottom": f"x=(w-text_w)/2:y=h-text_h-60"
        }

        pos = positions.get(position, positions["bottom"])

        # 动画滤镜
        animations = {
            "fade_in": f"fade=t=in:st=0:d=0.5,fade=t=out:st={duration-0.5}:d=0.5",
            "slide_up": f"fade=t=in:st=0:d=0.3,translate=y=50:0:linear:t=0-0.3",
            "typewriter": None,  # 特殊处理
            "none": ""
        }

        # 基础drawtext滤镜
        if border:
            border_w = "3"
            border_color = "black"
        else:
            border_w = "0"
            border_color = "white"

        if animation == "typewriter":
            # 逐字出现效果
            return self._create_typewriter_effect(video_path, output_path, text, font_size, pos, duration)
        else:
            anim_filter = animations.get(animation, "")

            drawtext_filter = (
                f"drawtext=text='{text}':"
                f"fontsize={font_size}:"
                f"fontcolor={font_color}:"
                f"borderw={border_w}:"
                f"bordercolor={border_color}:"
                f"{pos}"
            )

            if anim_filter:
                filter_str = f"{drawtext_filter},{anim_filter}"
            else:
                filter_str = drawtext_filter

            cmd = [
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", filter_str,
                "-c:a", "copy",
                output_path
            ]

            return self._run_ffmpeg(cmd)

    def _create_typewriter_effect(self, video_path: str, output_path: str,
                                    text: str, font_size: int,
                                    position: str, duration: float) -> bool:
        """创建打字机效果"""
        # 每字符持续时间
        char_duration = duration / len(text) if text else 1

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", (
                f"drawtext=text='':"
                f"fontsize={font_size}:"
                f"fontcolor=white:"
                f"borderw=3:"
                f"bordercolor=black:"
                f"{position}"
            ),
            "-c:a", "copy",
            output_path
        ]

        # 简化处理：使用enable参数控制显示
        filter_str = (
            f"drawtext=text='{text}':"
            f"fontsize={font_size}:"
            f"fontcolor=white:"
            f"borderw=3:"
            f"bordercolor=black:"
            f"enable='between(t,0,{duration})':"
            f"{position}"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", filter_str,
            "-c:a", "copy",
            output_path
        ]

        return self._run_ffmpeg(cmd)

    def create_animated_video_from_segments(self, images: List[str],
                                            segments: List[Dict],
                                            output_path: str,
                                            animation_style: str = "ken_burns",
                                            transition: str = "fade") -> bool:
        """
        根据时间轴创建动画视频（核心功能）

        参数:
            images: 图片路径列表
            segments: 时间轴列表，每项包含 start, end, text, image_index
            output_path: 输出视频路径
            animation_style: 动画风格 (ken_burns/pan_zoom/static)
            transition: 转场效果

        返回:
            是否成功
        """
        if not images or not segments:
            return False

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 生成各片段视频
        video_clips = []
        temp_dir = Path(output_path).parent / "temp_animation"
        temp_dir.mkdir(parents=True, exist_ok=True)

        for i, seg in enumerate(segments):
            image_idx = seg.get("image_index", i % len(images))
            if image_idx >= len(images):
                image_idx = i % len(images)

            image_path = images[image_idx]
            start = seg.get("start", 0)
            end = seg.get("end", 3)
            duration = end - start

            clip_path = str(temp_dir / f"clip_{i:03d}.mp4")

            # 选择动画效果
            if animation_style == "ken_burns":
                zoom_in = random.choice([True, False])
                self.create_ken_burns_clip(
                    image_path, clip_path,
                    duration=duration,
                    zoom_in=zoom_in,
                    zoom_range=(1.0, random.uniform(1.2, 1.5))
                )
            elif animation_style == "pan_zoom":
                effects = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]
                effect = random.choice(effects)
                self.create_pan_zoom_clip(image_path, clip_path, duration=duration, effect=effect)
            else:
                # static - 简单缩放
                self._create_simple_clip(image_path, clip_path, duration)

            if Path(clip_path).exists():
                video_clips.append((clip_path, duration, start))

        # 合并视频片段
        if not video_clips:
            return False

        # 按时间排序
        video_clips.sort(key=lambda x: x[2])

        # 创建合并列表
        concat_list = temp_dir / "concat_list.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for clip_path, _, _ in video_clips:
                abs_path = Path(clip_path).absolute()
                f.write(f"file '{abs_path.as_posix()}'\n")

        # 合并
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(self.output_crf),
            "-pix_fmt", "yuv420p",
            output_path
        ]

        success = self._run_ffmpeg(cmd)

        # 清理临时文件
        for clip in video_clips:
            try:
                Path(clip[0]).unlink()
            except FileNotFoundError:
                pass
        try:
            concat_list.unlink()
        except FileNotFoundError:
            pass
        try:
            temp_dir.rmdir()
        except FileNotFoundError:
            pass

        return success

    def _create_simple_clip(self, image_path: str, output_path: str, duration: float) -> bool:
        """创建简单缩放片段"""
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", image_path,
            "-t", str(duration),
            "-vf", (
                f"scale={self.output_width}:{self.output_height}:"
                f"force_original_aspect_ratio=increase,"
                f"crop={self.output_width}:{self.output_height}"
            ),
            "-pix_fmt", "yuv420p",
            "-r", str(self.output_fps),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(self.output_crf),
            output_path
        ]
        return self._run_ffmpeg(cmd)

    def add_transition(self, clip1_path: str, clip2_path: str,
                       output_path: str, transition: str = "fade",
                       duration: float = 0.5) -> bool:
        """
        在两个片段之间添加转场

        参数:
            clip1_path: 前一段视频路径
            clip2_path: 后一段视频路径
            output_path: 输出视频路径
            transition: 转场类型 (fade/dissolve)
            duration: 转场持续时间

        返回:
            是否成功
        """
        if transition == "fade":
            # 使用crossfade
            cmd = [
                "ffmpeg", "-y",
                "-i", clip1_path,
                "-i", clip2_path,
                "-filter_complex", f"crossfade=duration={duration}:offset=0",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", str(self.output_crf),
                output_path
            ]
        else:
            # 默认使用直接拼接
            concat_list = Path(output_path).parent / "transition_concat.txt"
            with open(concat_list, "w", encoding="utf-8") as f:
                abs1 = Path(clip1_path).absolute()
                abs2 = Path(clip2_path).absolute()
                f.write(f"file '{abs1.as_posix()}'\n")
                f.write(f"file '{abs2.as_posix()}'\n")

            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", str(self.output_crf),
                output_path
            ]

            result = self._run_ffmpeg(cmd)
            try:
                concat_list.unlink()
            except FileNotFoundError:
                pass
            return result

        return self._run_ffmpeg(cmd)

    def _run_ffmpeg(self, cmd: List[str]) -> bool:
        return run_ffmpeg_safe(cmd)

    def _render_code_image(self, code: str, lang: str = "python",
                           width: int = 500, height: int = 600,
                           font_size: int = 18) -> Optional[str]:
        """
        渲染代码为语法高亮图片（使用Pygments）

        返回:
            生成的临时图片路径，失败返回None
        """
        try:
            from pygments import highlight
            from pygments.lexers import get_lexer_by_name
            from pygments.formatters import ImageFormatter
            from pygments.styles import get_style_by_name
            from PIL import Image, ImageDraw
            import io
        except ImportError:
            print("[警告] 需要安装 pygments 和 Pillow 来渲染代码高亮")
            return None

        try:
            lexer = get_lexer_by_name(lang)
            formatter = ImageFormatter(
                font_size=font_size,
                style=get_style_by_name('monokai'),
                line_numbers=True,
                background_color='#1e1e1e',
                image_size=(width, height)
            )
            img_data = highlight(code, lexer, formatter)

            # 保存到临时文件
            temp_path = tempfile.mktemp(suffix='.png')
            with open(temp_path, 'wb') as f:
                f.write(img_data)
            return temp_path
        except Exception as e:
            print(f"[代码渲染] 失败: {e}")
            return None

    def _build_lecture_overlay(
        self,
        title: str,
        points: List[str],
        code: str,
        code_lang: str,
        duration: float,
        temp_dir: Path
    ) -> Tuple[Optional[str], Optional[str], List[Tuple[str, float, float]]]:
        """
        构建技术讲座风格的字幕/Overlay文件

        返回:
            (title_srt_path, points_srt_path, code_clip_times)
            title_srt_path: 顶部标题SRT路径
            points_srt_path: 左侧知识点SRT路径
            code_clip_times: [(code_frame_path, start_time, end_time), ...]
        """
        title_srt = temp_dir / "lecture_title.srt"
        points_srt = temp_dir / "lecture_points.srt"
        code_frames = []

        # ---------- 1. 生成标题SRT（顶部居中，淡入淡出） ----------
        start_t = 0.0
        end_t = duration

        # 标题分句（按逗号或换行拆分）
        title_lines = []
        for part in title.replace('\n', '，').split('，'):
            part = part.strip()
            if part:
                title_lines.append(part)

        if not title_lines:
            title_lines = [title]

        seg_duration = duration / max(len(title_lines), 1)
        with open(title_srt, "w", encoding="utf-8") as f:
            for i, line in enumerate(title_lines):
                f.write(f"{i+1}\n")
                st = i * seg_duration
                et = (i + 1) * seg_duration
                f.write(f"{self._fmt_time(st)} --> {self._fmt_time(et)}\n")
                f.write(f"{line}\n\n")

        # ---------- 2. 生成知识点SRT（逐行出现，左侧） ----------
        point_start = 1.0  # 延迟1秒开始
        with open(points_srt, "w", encoding="utf-8") as f:
            for i, pt in enumerate(points):
                f.write(f"{i+1}\n")
                st = point_start + i * 1.5
                et = st + 2.0
                if et > duration:
                    et = duration
                f.write(f"{self._fmt_time(st)} --> {self._fmt_time(et)}\n")
                f.write(f"• {pt}\n\n")

        # ---------- 3. 生成代码帧序列（打字机效果） ----------
        if code:
            # 把代码渲染成一张高亮图
            code_img_path = self._render_code_image(
                code, code_lang,
                width=min(500, self.output_width // 2),
                height=min(600, self.output_height - 200),
                font_size=16
            )
            if code_img_path:
                # 生成代码片段视频（持续全程）
                code_clip_path = str(temp_dir / "code_clip.mp4")
                zoom_in = random.choice([True, False])
                self.create_ken_burns_clip(
                    code_img_path, code_clip_path,
                    duration=duration,
                    zoom_in=zoom_in,
                    zoom_range=(1.0, 1.1)
                )
                if Path(code_clip_path).exists():
                    code_frames.append((code_clip_path, 0.0, duration))

        return str(title_srt), str(points_srt), code_frames

    def _fmt_time(self, seconds: float) -> str:
        """将秒数格式化为SRT时间码 HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def create_tech_lecture_video(
        self,
        bg_image: str,
        output_path: str,
        title: str,
        points: List[str],
        code: str = "",
        code_lang: str = "python",
        duration: float = 8.0,
        animation_style: str = "ken_burns"
    ) -> bool:
        """
        创建技术讲座风格视频
        仅生成Ken Burns背景视频 + 保留标题/知识点/代码信息到SRT文件
        字幕叠加由dual_mode_module的Step7/8统一处理
        """
        if not Path(bg_image).exists():
            print(f"[错误] 背景图片不存在: {bg_image}")
            return False

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(output_path).parent / "temp_lecture"
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Step 1: 生成Ken Burns背景视频
            if animation_style == "ken_burns":
                zoom_in = random.choice([True, False])
                success = self.create_ken_burns_clip(
                    bg_image, output_path,
                    duration=duration,
                    zoom_in=zoom_in,
                    zoom_range=(1.0, random.uniform(1.1, 1.3))
                )
            else:
                success = self._create_simple_clip(bg_image, output_path, duration)

            if not success or not Path(output_path).exists():
                print("[错误] 背景视频生成失败")
                return False

            # Step 2: 生成SRT字幕文件（由Step7/8使用）
            title_srt = str(temp_dir / "lecture_title.srt")
            points_srt = str(temp_dir / "lecture_points.srt")

            # 标题SRT（顶部居中，逐句出现）
            title_lines = title.replace('\n', '，').split('，')
            title_lines = [t.strip() for t in title_lines if t.strip()]
            if not title_lines:
                title_lines = [title]
            seg_dur = duration / max(len(title_lines), 1)
            with open(title_srt, "w", encoding="utf-8") as f:
                for i, line in enumerate(title_lines):
                    f.write(f"{i+1}\n")
                    st, et = i * seg_dur, (i + 1) * seg_dur
                    f.write(f"{self._fmt_time(st)} --> {self._fmt_time(et)}\n")
                    f.write(f"{line}\n\n")

            # 知识点SRT（左侧，逐行出现）
            with open(points_srt, "w", encoding="utf-8") as f:
                for i, pt in enumerate(points):
                    f.write(f"{i+1}\n")
                    st = 1.0 + i * 1.5
                    et = st + 2.0
                    if et > duration:
                        et = duration
                    f.write(f"{self._fmt_time(st)} --> {self._fmt_time(et)}\n")
                    f.write(f"• {pt}\n\n")

            print(f"[TechLecture] 背景视频生成成功: {output_path}")
            print(f"[TechLecture] 标题SRT: {title_srt}")
            print(f"[TechLecture] 知识点SRT: {points_srt}")
            return True

        finally:
            import shutil
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    def _run_ffmpeg(self, cmd: List[str]) -> bool:
        return run_ffmpeg_safe(cmd)


# ==================== 便捷函数 ====================
_anim_instance = None


def get_animation_module() -> AnimationModule:
    global _anim_instance
    if _anim_instance is None:
        _anim_instance = AnimationModule()
    return _anim_instance
