# -*- coding: utf-8 -*-
"""
自动剪辑引擎 - FFmpeg纯本地视频处理
读取本地素材池视频/图片，自动裁剪9:16竖屏、拼接、加BGM、音量平衡、转场
支持批量生成视频，无水印，1080P高清输出
"""
import os
import json
import subprocess
import random
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from config import (
    OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS, OUTPUT_CRF,
    OUTPUT_AUDIO_BITRATE, OUTPUT_VIDEO_BITRATE, BGM_DIR, MATERIAL_DIR,
    BGM_VOLUME, BG_MUTE_DURATION
)

class VideoModule:
    """FFmpeg视频剪辑模块"""

    def __init__(self):
        """初始化视频剪辑模块"""
        self.output_width = OUTPUT_WIDTH
        self.output_height = OUTPUT_HEIGHT
        self.output_fps = OUTPUT_FPS
        self.output_crf = OUTPUT_CRF

    def create_video_from_images(self, images: List[str], output_path: str,
                                  duration_per_image: int = 3,
                                  transition: str = "fade",
                                  bgm_path: Optional[str] = None,
                                  subtitle_path: Optional[str] = None) -> bool:
        """
        将多张图片合成视频

        参数:
            images: 图片路径列表
            output_path: 输出视频路径
            duration_per_image: 每张图片持续秒数
            transition: 转场效果 (fade/wipe/none)
            bgm_path: BGM音乐路径
            subtitle_path: SRT字幕路径

        返回:
            是否成功
        """
        if not images:
            print("错误: 没有提供图片素材")
            return False

        # 创建临时文件列表
        temp_list = Path(output_path).parent / "temp_file_list.txt"
        total_duration = len(images) * duration_per_image

        # 生成图片序列 (每张图片转为短视频片段)
        video_clips = []
        for i, img in enumerate(images):
            clip_path = Path(output_path).parent / f"temp_clip_{i}.mp4"
            if not self._image_to_clip(img, str(clip_path), duration_per_image, transition if i > 0 else "none"):
                return False
            video_clips.append(str(clip_path))

        # 拼接所有片段
        concat_list = Path(output_path).parent / "temp_concat_list.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for clip in video_clips:
                f.write(f"file '{clip}'\n")

        concat_output = Path(output_path).parent / "temp_concat.mp4"
        if not self._concat_videos(video_clips, str(concat_output)):
            return False

        # 添加BGM和字幕
        if bgm_path or subtitle_path:
            if not self._add_audio_subtitle(str(concat_output), output_path, bgm_path, subtitle_path, total_duration):
                return False
        else:
            # 直接复制
            os.rename(str(concat_output), output_path)

        # 清理临时文件
        self._cleanup_temp_files(video_clips, str(concat_list), str(concat_output))

        return True

    def _image_to_clip(self, image_path: str, output_path: str,
                       duration: int, transition: str = "none") -> bool:
        """将单张图片转为短视频片段"""
        filter_str = ""

        if transition == "fade":
            filter_str = f"fade=t=out:st={duration-1}:d=1,fade=t=in:st=0:d=0.5"

        cmd = [
            "ffmpeg", "-y", "-loop", "1",
            "-i", image_path,
            "-t", str(duration),
            "-vf", f"scale={self.output_width}:{self.output_height}:force_original_aspect_ratio=increase,crop={self.output_width}:{self.output_height},{filter_str}" if filter_str else f"scale={self.output_width}:{self.output_height}:force_original_aspect_ratio=increase,crop={self.output_width}:{self.output_height}",
            "-pix_fmt", "yuv420p",
            "-r", str(self.output_fps),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(self.output_crf),
            output_path
        ]

        return self._run_ffmpeg(cmd)

    def _concat_videos(self, video_paths: List[str], output_path: str) -> bool:
        """拼接多个视频片段"""
        # 方法1: 使用文件列表
        list_file = Path(output_path).parent / "temp_concat_list.txt"
        with open(list_file, "w", encoding="utf-8") as f:
            for path in video_paths:
                f.write(f"file '{path}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(self.output_crf),
            "-pix_fmt", "yuv420p",
            output_path
        ]

        result = self._run_ffmpeg(cmd)
        if list_file.exists():
            list_file.unlink()
        return result

    def _add_audio_subtitle(self, video_path: str, output_path: str,
                            bgm_path: Optional[str], subtitle_path: Optional[str],
                            total_duration: int) -> bool:
        """为视频添加BGM和字幕"""
        audio_filters = []

        # BGM处理
        if bgm_path and os.path.exists(bgm_path):
            # BGM淡入
            audio_filters.append(
                f"volume='if(lt(t,{BG_MUTE_DURATION}),0,{BGM_VOLUME})':eval=frame"
            )

        # 构建ffmpeg命令
        cmd = ["ffmpeg", "-y", "-i", video_path]

        if bgm_path and os.path.exists(bgm_path):
            cmd.extend(["-i", bgm_path])

        if subtitle_path and os.path.exists(subtitle_path):
            cmd.extend(["-i", subtitle_path])

        # 构建filter_complex
        filter_parts = []

        # 视频处理 - 字幕烧录
        if subtitle_path and os.path.exists(subtitle_path):
            filter_parts.append(f"subtitles={subtitle_path}")

        video_filter = ",".join(filter_parts) if filter_parts else "null"

        # 音频处理
        if bgm_path and os.path.exists(bgm_path):
            # 混合原音频(降低)和BGM
            audio_filter = f"[0:a]volume=0.8[a0];[1:a]{audio_filters[0]}[a1];[a0][a1]amix=inputs=2:duration=first[aout]"
            filter_parts.append(audio_filter)

        if filter_parts:
            cmd.extend(["-filter_complex", ";".join(filter_parts)])

        # 映射
        cmd.extend(["-map", "0:v"])
        if bgm_path and os.path.exists(bgm_path):
            cmd.extend(["-map", "[aout]"])
        else:
            cmd.extend(["-map", "0:a"])

        cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(self.output_crf),
            "-c:a", "aac",
            "-b:a", OUTPUT_AUDIO_BITRATE,
            "-shortest",
            output_path
        ])

        return self._run_ffmpeg(cmd)

    def cut_video(self, input_path: str, output_path: str,
                  start_time: float, duration: float) -> bool:
        """切割视频片段"""
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-ss", str(start_time),
            "-t", str(duration),
            "-vf", f"scale={self.output_width}:{self.output_height}:force_original_aspect_ratio=increase,crop={self.output_width}:{self.output_height}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(self.output_crf),
            "-c:a", "aac",
            "-b:a", OUTPUT_AUDIO_BITRATE,
            "-pix_fmt", "yuv420p",
            output_path
        ]
        return self._run_ffmpeg(cmd)

    def extract_audio(self, video_path: str, audio_path: str) -> bool:
        """提取视频音频"""
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-c:a", "libmp3lame",
            "-b:a", OUTPUT_AUDIO_BITRATE,
            audio_path
        ]
        return self._run_ffmpeg(cmd)

    def add_bgm(self, video_path: str, output_path: str, bgm_path: str,
                bgm_volume: float = BGM_VOLUME) -> bool:
        """为视频添加BGM"""
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", bgm_path,
            "-filter_complex",
            f"[0:a]volume=0.7[a0];[1:a]volume={bgm_volume}[a1];[a0][a1]amix=inputs=2:duration=first[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", OUTPUT_AUDIO_BITRATE,
            "-shortest",
            output_path
        ]
        return self._run_ffmpeg(cmd)

    def create_video_with_narration(self, images: List[str], audio_path: str,
                                     output_path: str, subtitle_path: Optional[str] = None) -> bool:
        """根据配音自动同步图片生成视频"""
        # 获取音频时长
        audio_duration = self._get_media_duration(audio_path)

        if audio_duration <= 0:
            print("错误: 无法获取音频时长")
            return False

        # 计算每张图片应持续的时间
        num_images = len(images)
        duration_per_image = audio_duration / num_images

        # 生成各图片对应的视频片段
        video_clips = []
        for i, img in enumerate(images):
            start_time = i * duration_per_image
            clip_path = Path(output_path).parent / f"temp_sync_{i}.mp4"

            # 创建图片片段
            cmd = [
                "ffmpeg", "-y", "-loop", "1",
                "-i", img,
                "-t", str(duration_per_image),
                "-vf", f"scale={self.output_width}:{self.output_height}:force_original_aspect_ratio=increase,crop={self.output_width}:{self.output_height}",
                "-pix_fmt", "yuv420p",
                "-r", str(self.output_fps),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", str(self.output_crf),
                str(clip_path)
            ]
            self._run_ffmpeg(cmd)
            video_clips.append(str(clip_path))

        # 拼接视频
        concat_output = Path(output_path).parent / "temp_sync_concat.mp4"
        self._concat_videos(video_clips, str(concat_output))

        # 添加音频和字幕
        final_cmd = ["ffmpeg", "-y", "-i", str(concat_output), "-i", audio_path]

        filter_parts = []
        if subtitle_path and os.path.exists(subtitle_path):
            filter_parts.append(f"subtitles={subtitle_path}")

        if filter_parts:
            final_cmd.extend(["-vf", ",".join(filter_parts)])

        final_cmd.extend([
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(self.output_crf),
            "-c:a", "aac",
            "-b:a", OUTPUT_AUDIO_BITRATE,
            "-shortest",
            output_path
        ])

        result = self._run_ffmpeg(final_cmd)

        # 清理临时文件
        for clip in video_clips:
            Path(clip).unlink(missing_ok=True)
        if concat_output.exists():
            concat_output.unlink()

        return result

    def _get_media_duration(self, media_path: str) -> float:
        """获取媒体文件时长(秒)"""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            media_path
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return float(result.stdout.strip())
        except:
            return 0

    def _run_ffmpeg(self, cmd: List[str]) -> bool:
        """执行FFmpeg命令"""
        try:
            # 打印执行的命令（方便调试）
            cmd_str = ' '.join(cmd)
            print(f"执行FFmpeg命令: {cmd_str[:200]}...")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            if result.returncode != 0:
                # FFmpeg版本信息在前几行，真正的错误在后面
                stderr_lines = result.stderr.strip().split('\n')
                # 跳过版本信息行，找到真正的错误
                error_lines = []
                skip_patterns = ['ffmpeg version', 'built with', 'configuration:', 'Copyright',
                                 'libavformat', 'libavcodec', 'libavutil', 'libavfilter',
                                 'libswscale', 'libswresample', 'libpostproc', 'FFmpeg']
                for line in stderr_lines:
                    line_stripped = line.strip()
                    if not line_stripped:
                        continue
                    # 跳过空行和版本信息
                    skip = False
                    for pattern in skip_patterns:
                        if pattern.lower() in line_stripped.lower():
                            skip = True
                            break
                    if skip:
                        continue
                    error_lines.append(line_stripped)

                if error_lines:
                    error_msg = ' | '.join(error_lines[:5])  # 最多显示5行
                    print(f"FFmpeg执行失败: {error_msg}")
                else:
                    print(f"FFmpeg执行失败 (返回码: {result.returncode})")
                return False
            return True
        except subprocess.TimeoutExpired:
            print("FFmpeg执行超时 (超过5分钟)")
            return False
        except Exception as e:
            print(f"FFmpeg执行异常: {str(e)}")
            return False

    def _cleanup_temp_files(self, *paths):
        """清理临时文件"""
        for path in paths:
            p = Path(path)
            if p.exists():
                try:
                    p.unlink()
                except:
                    pass

    def get_available_bgm(self) -> List[str]:
        """获取可用的BGM列表"""
        if not BGM_DIR.exists():
            return []
        return [str(f) for f in BGM_DIR.glob("*.mp3")] + [str(f) for f in BGM_DIR.glob("*.wav")]

    def get_material_images(self) -> List[str]:
        """获取素材池中的图片"""
        if not MATERIAL_DIR.exists():
            return []
        images = []
        for ext in ["*.jpg", "*.jpeg", "*.png", "*.webp"]:
            images.extend([str(f) for f in MATERIAL_DIR.glob(ext)])
        return sorted(images)

    def get_material_videos(self) -> List[str]:
        """获取素材池中的视频"""
        if not MATERIAL_DIR.exists():
            return []
        videos = []
        for ext in ["*.mp4", "*.avi", "*.mov", "*.mkv"]:
            videos.extend([str(f) for f in MATERIAL_DIR.glob(ext)])
        return sorted(videos)

    def auto_select_materials(self, count: int = 5) -> List[str]:
        """自动选择素材"""
        images = self.get_material_images()
        if images:
            return random.sample(images, min(count, len(images)))
        return []


# ==================== 便捷函数 ====================
_module_instance = None

def get_video_module() -> VideoModule:
    """获取视频模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = VideoModule()
    return _module_instance

def create_video_from_images(images: List[str], output_path: str,
                               duration: int = 3, bgm: Optional[str] = None) -> bool:
    """快速从图片创建视频"""
    return get_video_module().create_video_from_images(images, output_path, duration, bgm_path=bgm)
