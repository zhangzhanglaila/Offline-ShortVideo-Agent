# -*- coding: utf-8 -*-
"""
TTS配音模块 - 支持多种TTS后端
1. Windows SAPI (离线，Windows内置)
2. edge-tts (Microsoft Edge在线TTS)
3. gtts (Google TTS)
"""
import asyncio
import tempfile
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# 尝试导入各TTS后端
EDGE_TTS_AVAILABLE = False
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    pass

GTTS_AVAILABLE = False
try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    pass

# Windows SAPI (离线，优先使用)
WIN_SAPI_AVAILABLE = False
try:
    import win32com.client
    import pythoncom
    WIN_SAPI_AVAILABLE = True
except ImportError:
    pass

from config import OUTPUT_AUDIO_BITRATE


class TTSModule:
    """TTS配音模块 - 支持多种后端"""

    # edge-tts 中文配音人
    ZH_VOICES_EDGE = {
        "zh-CN-XiaoxiaoNeural": "晓晓（女声-年轻）",
        "zh-CN-YunxiNeural": "云希（男声-年轻）",
        "zh-CN-YunyangNeural": "云扬（男声-新闻）",
        "zh-CN-Xiaoyi": "小艺（女声-温柔）",
        "zh-CN-Zhiyu": "志宇（男声-成熟）",
        "zh-CN-Xiaomo": "小墨（女声-活力）",
        "zh-CN-Guiso": "贵SO（男声-稳重）",
        "zh-CN-Yunxia": "云夏（女声-可爱）",
    }

    # Windows SAPI 中文配音人
    ZH_VOICES_SAPI = {
        "Huihui": "慧慧（女声-中文）",
        "Kangkang": "康康（男声-中文）",
        "Yaoyao": "瑶瑶（女声-中文）",
        "WangYi": "王毅（男声-中文）",
    }

    DEFAULT_VOICE = "Huihui"
    DEFAULT_RATE = 0

    def __init__(self, voice: str = None, backend: str = None):
        """初始化TTS模块"""
        self.voice = voice or self.DEFAULT_VOICE
        self.rate = self.DEFAULT_RATE
        self.backend = backend or self._detect_backend()
        self.voice_id = self._get_voice_id(voice)

    def _detect_backend(self) -> str:
        """检测可用的TTS后端"""
        if WIN_SAPI_AVAILABLE:
            return "sapi"
        elif EDGE_TTS_AVAILABLE:
            return "edge"
        elif GTTS_AVAILABLE:
            return "gtts"
        return "none"

    def _get_voice_id(self, voice: str) -> str:
        """获取voice对应的ID"""
        if voice in self.ZH_VOICES_SAPI:
            return voice
        # 映射edge-tts voice到SAPI voice
        edge_to_sapi = {
            "zh-CN-XiaoxiaoNeural": "Huihui",
            "zh-CN-YunxiNeural": "Kangkang",
            "zh-CN-Xiaoyi": "Yaoyao",
            "zh-CN-Zhiyu": "WangYi",
        }
        return edge_to_sapi.get(voice, self.DEFAULT_VOICE)

    def _generate_sapi(self, text: str, output_path: str) -> bool:
        """使用Windows SAPI生成音频"""
        if not WIN_SAPI_AVAILABLE:
            return False

        try:
            import pythoncom
            pythoncom.CoInitialize()
            speaker = win32com.client.Dispatch('SAPI.SpVoice')

            # 设置语速 (-10 到 +10)
            if self.rate != 0:
                speaker.Rate = self.rate

            # 尝试选择指定语音
            try:
                voices = speaker.GetVoices()
                for i in range(voices.Count):
                    voice = voices.Item(i)
                    if self.voice_id.lower() in voice.GetDescription().lower():
                        speaker.Voice = voice
                        break
            except:
                pass

            # 保存为WAV然后转换
            stream = win32com.client.Dispatch('SAPI.SpFileStream')
            stream.Open(output_path, 3)  # SSFMCreateForWrite
            speaker.AudioOutputStream = stream
            speaker.Speak(text)
            stream.Close()
            pythoncom.CoUninitialize()
            return Path(output_path).exists()

        except Exception as e:
            print(f"SAPI TTS失败: {str(e)}")
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except:
                pass
            return False

    async def _generate_edge_async(self, text: str, output_path: str) -> bool:
        """使用edge-tts异步生成音频"""
        if not EDGE_TTS_AVAILABLE:
            return False

        try:
            rate_str = f"{self.rate:+.0f}%" if self.rate != 0 else "+0%"
            communicate = edge_tts.Communicate(text, voice=self.voice, rate=rate_str)
            await communicate.save(output_path)
            return Path(output_path).exists()
        except Exception as e:
            print(f"Edge-TTS失败: {str(e)}")
            return False

    def _generate_edge(self, text: str, output_path: str) -> bool:
        """使用edge-tts生成音频"""
        if not EDGE_TTS_AVAILABLE:
            return False
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(self._generate_edge_async(text, output_path))
        except Exception as e:
            print(f"TTS生成失败: {str(e)}")
            return False

    def _generate_gtts(self, text: str, output_path: str) -> bool:
        """使用Google TTS生成音频"""
        if not GTTS_AVAILABLE:
            return False
        try:
            tts = gTTS(text=text, lang='zh-cn', slow=False)
            tts.save(output_path)
            return Path(output_path).exists()
        except Exception as e:
            print(f"gTTS失败: {str(e)}")
            return False

    def generate_audio(self, text: str, output_path: str) -> bool:
        """
        生成配音音频

        参数:
            text: 要转换的文本
            output_path: 输出音频路径

        返回:
            是否成功
        """
        if not text or not text.strip():
            return False

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 根据后端生成
        if self.backend == "sapi":
            return self._generate_sapi(text, output_path)
        elif self.backend == "edge":
            return self._generate_edge(text, output_path)
        elif self.backend == "gtts":
            return self._generate_gtts(text, output_path)
        else:
            print(f"错误: 没有可用的TTS后端")
            print(f"请安装TTS后端: pip install edge-tts (推荐) 或确保Windows SAPI可用")
            return False

    def generate_from_segments(self, segments: List[Dict], output_dir: str,
                               voice: Optional[str] = None) -> Tuple[bool, List[str]]:
        """
        根据字幕时间段生成配音音频

        参数:
            segments: 字幕段列表
            output_dir: 输出目录
            voice: 配音人（可选）

        返回:
            (是否成功, 音频文件列表)
        """
        if voice:
            self.voice = voice
            self.voice_id = self._get_voice_id(voice)

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        audio_files = []
        for i, seg in enumerate(segments):
            text = seg.get("text", "").strip()
            if not text:
                continue

            audio_path = str(Path(output_dir) / f"segment_{i:03d}.wav")
            if self.generate_audio(text, audio_path):
                audio_files.append(audio_path)

        return len(audio_files) > 0, audio_files

    def generate_combined_audio(self, segments: List[Dict], output_path: str,
                                 voice: Optional[str] = None) -> bool:
        """
        生成合并的配音音频

        参数:
            segments: 字幕段列表
            output_path: 输出音频路径
            voice: 配音人（可选）

        返回:
            是否成功
        """
        if voice:
            self.voice = voice
            self.voice_id = self._get_voice_id(voice)

        if not segments:
            return False

        temp_dir = tempfile.mkdtemp()
        success, audio_files = self.generate_from_segments(segments, temp_dir)

        if not success or not audio_files:
            for f in Path(temp_dir).glob("*"):
                f.unlink(missing_ok=True)
            Path(temp_dir).rmdir(missing_ok=True)
            return False

        # 使用FFmpeg合并音频
        concat_list = Path(temp_dir) / "concat_list.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for audio_file in audio_files:
                abs_path = Path(audio_file).absolute()
                f.write(f"file '{abs_path.as_posix()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c:a", "libmp3lame",
            "-b:a", OUTPUT_AUDIO_BITRATE,
            output_path
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace', timeout=300)
            success = result.returncode == 0 and Path(output_path).exists()
        except Exception as e:
            print(f"音频合并失败: {str(e)}")
            success = False

        for f in Path(temp_dir).glob("*"):
            f.unlink(missing_ok=True)
        Path(temp_dir).rmdir(missing_ok=True)

        return success

    def set_rate(self, rate: int):
        """设置语速，-10 到 +10"""
        self.rate = max(-10, min(10, rate))
        return self

    def get_available_voices(self) -> Dict[str, str]:
        """获取可用的配音人列表"""
        voices = {}
        voices.update(self.ZH_VOICES_EDGE)
        voices.update(self.ZH_VOICES_SAPI)
        return voices

    def get_backend_name(self) -> str:
        """获取当前使用的后端"""
        return self.backend

    @staticmethod
    def get_audio_duration(audio_path: str) -> float:
        """获取音频时长（秒）"""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return float(result.stdout.strip())
        except:
            return 0.0


_module_instance = None


def get_tts_module(voice: str = TTSModule.DEFAULT_VOICE) -> TTSModule:
    """获取TTS模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = TTSModule(voice)
    return _module_instance


def generate_tts(text: str, output_path: str, voice: str = TTSModule.DEFAULT_VOICE) -> bool:
    """快速生成TTS音频"""
    return get_tts_module(voice).generate_audio(text, output_path)


def generate_tts_from_script(script: str, output_path: str,
                               duration: float = None,
                               voice: str = TTSModule.DEFAULT_VOICE) -> Tuple[bool, str]:
    """从脚本生成TTS音频（自动分句）"""
    from core.subtitle_module import get_subtitle_module

    sub_mod = get_subtitle_module()
    duration = duration or len(script) / 3.5
    segments = sub_mod.generate_srt_from_script(script, duration)
    tts = get_tts_module(voice)
    success = tts.generate_combined_audio(segments, output_path)
    return success, output_path if success else ""
