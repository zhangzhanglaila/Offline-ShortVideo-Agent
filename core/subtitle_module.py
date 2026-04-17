# -*- coding: utf-8 -*-
"""
离线字幕模块 - faster-whisper纯本地语音转文字
纯本地语音转文字，自动生成SRT字幕、时间轴对齐，白字黑边硬字幕烧录
支持脚本直接生成字幕，双重方案
"""
# 【重要】必须在 import faster_whisper 之前设置镜像站点，否则会尝试连接 HuggingFace 失败
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import re
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

from config import WHISPER_MODEL, WHISPER_LANGUAGE, OUTPUT_WIDTH, OUTPUT_HEIGHT

# 日志回调 - 用于实时推送日志到前端
_subtitle_log_callback = None

def set_subtitle_log_callback(callback):
    """设置字幕模块日志回调"""
    global _subtitle_log_callback
    _subtitle_log_callback = callback

def _log(msg: str, level: str = 'info'):
    """发送日志"""
    if _subtitle_log_callback:
        try:
            _subtitle_log_callback(msg, level)
        except Exception:
            pass

class SubtitleModule:
    """字幕生成模块 - 基于faster-whisper"""

    def __init__(self, model_size: str = WHISPER_MODEL):
        """初始化字幕模块"""
        self.model_size = model_size
        self.model = None
        self._load_model()

    def _load_model(self):
        """加载Whisper模型"""
        # 设置HuggingFace镜像，解决网络问题
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

        if not WHISPER_AVAILABLE:
            print("[WARNING] faster-whisper not installed, subtitle will use fallback script-based generation")
            print("   To enable speech recognition, run: pip install faster-whisper")
            return

        # 本地模型路径
        local_model_path = Path(__file__).parent.parent / "models" / f"faster-whisper-{self.model_size}"
        model_dir = local_model_path.parent

        try:
            # 检查本地模型是否存在
            if local_model_path.exists() and (local_model_path / "model.bin").exists():
                print(f"[LOAD] Loading Whisper model '{self.model_size}' from local...")
                self.model = WhisperModel(
                    str(local_model_path),
                    device="cpu",
                    compute_type="int8"
                )
                print(f"[OK] Whisper model '{self.model_size}' loaded successfully")
            else:
                # 本地模型不存在，尝试从镜像下载
                print(f"[DOWNLOAD] Downloading/loading Whisper model '{self.model_size}'...")
                print(f"   (首次使用会从 HuggingFace 下载模型，约500MB，请耐心等待)")
                self.model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8"
                )
                print(f"[OK] Whisper model '{self.model_size}' loaded successfully")
        except Exception as e:
            error_msg = str(e)
            print(f"[FAIL] Model loading failed: {error_msg}")
            if 'Hub' in error_msg or 'snapshot' in error_msg or 'ConnectTimeout' in error_msg:
                print(f"   Reason: Cannot connect to HuggingFace to download model")
                print("   Solutions:")
                print("   1. Retry when network recovers")
                print("   2. Or manually download model to models/ directory")
                print("   3. Or use smaller model: tiny/base (smaller but slightly less accurate)")
            elif 'SSL' in error_msg or 'EOF' in error_msg:
                print("   Reason: SSL connection interrupted, possibly due to network instability or proxy")
                print("   Solution: Check network/proxy settings, or retry later")
            elif 'model' in error_msg.lower() and 'not found' in error_msg.lower():
                print("   Reason: Local model file does not exist or is corrupted")
                print(f"   Model path: {local_model_path}")
                print("   Please ensure model files (model.bin, config.json, tokenizer.json) are in this directory")
            self.model = None

    def transcribe_audio(self, audio_path: str, language: str = WHISPER_LANGUAGE) -> List[Dict]:
        """
        语音转文字

        参数:
            audio_path: 音频文件路径
            language: 语言代码

        返回:
            字幕段列表，每段包含 start, end, text
        """
        if not self.model:
            return self._fallback_transcribe(audio_path)

        try:
            segments, info = self.model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=True,  # 语音活动检测
                vad_parameters=dict(min_silence_duration_ms=500)
            )

            results = []
            for segment in segments:
                results.append({
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip()
                })

            return results

        except Exception as e:
            print(f"语音识别失败: {str(e)}")
            return []

    def transcribe_video(self, video_path: str, language: str = WHISPER_LANGUAGE) -> List[Dict]:
        """从视频提取音频并转写"""
        # 提取音频
        from core.video_module import get_video_module
        video_mod = get_video_module()

        audio_path = Path(video_path).parent / f"temp_audio_{Path(video_path).stem}.wav"
        if video_mod.extract_audio(video_path, str(audio_path)):
            result = self.transcribe_audio(str(audio_path), language)
            # 清理临时音频
            if audio_path.exists():
                audio_path.unlink()
            return result
        return []

    def generate_srt(self, segments: List[Dict], output_path: str) -> bool:
        """
        生成SRT字幕文件

        参数:
            segments: 字幕段列表
            output_path: 输出SRT文件路径

        返回:
            是否成功
        """
        if not segments:
            return False

        try:
            with open(output_path, "w", encoding="utf-8") as f:
                for i, seg in enumerate(segments, 1):
                    # 时间码格式: HH:MM:SS,mmm
                    start_time = self._format_timestamp(seg["start"])
                    end_time = self._format_timestamp(seg["end"])
                    text = seg["text"]

                    f.write(f"{i}\n")
                    f.write(f"{start_time} --> {end_time}\n")
                    f.write(f"{text}\n")
                    f.write("\n")

            return True
        except Exception as e:
            print(f"SRT生成失败: {str(e)}")
            return False

    def generate_srt_from_script(self, script: str, duration: float,
                                 max_chars_per_line: int = 18) -> List[Dict]:
        """
        根据脚本直接生成字幕(无需语音识别)

        参数:
            script: 口播脚本
            duration: 视频总时长(秒)
            max_chars_per_line: 每行最大字符数

        返回:
            字幕段列表
        """
        # 按标点符号分割脚本
        sentences = self._split_sentences(script)

        if not sentences:
            return []

        # 计算每句时长
        total_chars = sum(len(s) for s in sentences)
        if total_chars == 0:
            return []

        avg_duration = duration / len(sentences)

        segments = []
        current_time = 0.0

        for sentence in sentences:
            # 进一步按每行字符数分割
            words = sentence
            lines = []
            while len(words) > max_chars_per_line:
                # 在空格处分割
                split_pos = words[:max_chars_per_line].rfind(" ")
                if split_pos <= 0:
                    split_pos = max_chars_per_line
                lines.append(words[:split_pos].strip())
                words = words[split_pos:].strip()

            if words:
                lines.append(words)

            # 为每行分配时间
            line_duration = avg_duration / max(len(lines), 1)

            for line in lines:
                if line:
                    segments.append({
                        "start": current_time,
                        "end": current_time + line_duration,
                        "text": line
                    })
                    current_time += line_duration

        # 确保总时长不超过视频时长
        if segments and segments[-1]["end"] > duration:
            scale = duration / segments[-1]["end"]
            for seg in segments:
                seg["start"] *= scale
                seg["end"] *= scale

        return segments

    def burn_subtitles(self, video_path: str, srt_path: str,
                       output_path: str, style: str = "white_black_edge") -> bool:
        """
        烧录硬字幕到视频

        参数:
            video_path: 输入视频路径
            srt_path: SRT字幕文件路径
            output_path: 输出视频路径
            style: 字幕样式 (white_black_edge/white/yellow)

        返回:
            是否成功
        """
        import subprocess

        # 字幕样式配置
        style_configs = {
            "white_black_edge": {
                "fontfile": "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
                "font_size": "56",
                "font_color": "white",
                "border_w": "3",
                "border_color": "black",
            },
            "white": {
                "fontfile": "C:/Windows/Fonts/msyh.ttc",
                "font_size": "56",
                "font_color": "white",
                "border_w": "2",
                "border_color": "white",
            },
            "yellow": {
                "fontfile": "C:/Windows/Fonts/msyh.ttc",
                "font_size": "56",
                "font_color": "yellow",
                "border_w": "3",
                "border_color": "black",
            },
        }

        style_cfg = style_configs.get(style, style_configs["white_black_edge"])

        # 字幕位置: 底部居中
        margin_bottom = 60

        filter_str = (
            f"subtitles='{srt_path}'"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", filter_str,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "copy",
            "-pix_fmt", "yuv420p",
            output_path
        ]

        _log("正在烧录字幕到视频...", 'info')
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600
            )
            if result.returncode != 0:
                print(f"字幕烧录失败: {result.stderr[:200]}")
                _log(f"字幕烧录失败: {result.stderr[:200]}", 'error')
                return False
            _log("字幕烧录完成", 'info')
            return True
        except subprocess.TimeoutExpired:
            print("字幕烧录超时")
            _log("字幕烧录超时", 'error')
            return False
        except Exception as e:
            print(f"字幕烧录异常: {str(e)}")
            _log(f"字幕烧录异常: {str(e)}", 'error')
            return False

    def generate_subtitle_video(self, video_path: str, script: str,
                                 output_path: str, duration: float = None,
                                 use_whisper: bool = False) -> Tuple[bool, str]:
        """
        生成带字幕的视频

        参数:
            video_path: 输入视频
            script: 字幕脚本
            output_path: 输出路径
            duration: 视频时长(秒)
            use_whisper: 是否使用语音识别

        返回:
            (是否成功, 字幕/SRT文件路径)
        """
        # 如果未指定时长，尝试获取
        if duration is None:
            from core.video_module import get_video_module
            video_mod = get_video_module()
            duration = video_mod._get_media_duration(video_path)
            if duration <= 0:
                duration = 30  # 默认30秒

        srt_path = Path(output_path).with_suffix(".srt")

        if use_whisper and self.model:
            # 使用Whisper识别
            print("  正在使用Whisper进行语音识别...")
            segments = self.transcribe_video(video_path)
            if segments:
                self.generate_srt(segments, str(srt_path))
            else:
                # 降级到脚本生成
                segments = self.generate_srt_from_script(script, duration)
                self.generate_srt(segments, str(srt_path))
        else:
            # 直接从脚本生成字幕
            print("  正在根据脚本生成字幕...")
            segments = self.generate_srt_from_script(script, duration)
            self.generate_srt(segments, str(srt_path))

        # 烧录字幕
        if self.burn_subtitles(video_path, str(srt_path), output_path):
            return True, str(srt_path)

        return False, str(srt_path)

    def _format_timestamp(self, seconds: float) -> str:
        """格式化时间戳为SRT格式"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _split_sentences(self, text: str) -> List[str]:
        """按标点符号分割句子"""
        # 按中英文标点分割
        pattern = r'[。！？.!?；;，,]'
        parts = re.split(pattern, text)
        return [p.strip() for p in parts if p.strip()]

    def _fallback_transcribe(self, audio_path: str) -> List[Dict]:
        """降级转写方案"""
        print("警告: 使用降级转写方案(基于Silence)")
        # 返回空列表，由调用方使用脚本生成
        return []


# ==================== 便捷函数 ====================
_module_instance = None

def get_subtitle_module() -> SubtitleModule:
    """获取字幕模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = SubtitleModule()
    return _module_instance

def generate_subtitle_file(script: str, duration: float, output_path: str) -> bool:
    """快速生成字幕文件"""
    module = get_subtitle_module()
    segments = module.generate_srt_from_script(script, duration)
    return module.generate_srt(segments, output_path)

def transcribe_to_srt(audio_path: str, output_path: str) -> bool:
    """语音转SRT字幕"""
    module = get_subtitle_module()
    segments = module.transcribe_audio(audio_path)
    if segments:
        return module.generate_srt(segments, output_path)
    return False
