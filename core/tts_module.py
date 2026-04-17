# -*- coding: utf-8 -*-
"""
TTS配音模块 - 支持多种TTS后端
优先级: 讯飞TTS > 百度TTS > edge-tts > gTTS > SAPI
"""
import os
import asyncio
import tempfile
import subprocess
import hmac
import hashlib
import base64
import time
import json
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dotenv import load_dotenv
load_dotenv()

# 讯飞TTS (通过WebSocket v2)
XUNFEI_AVAILABLE = False
try:
    import websockets
    XUNFEI_AVAILABLE = True
except ImportError:
    pass

# edge-tts
EDGE_TTS_AVAILABLE = False
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    pass

# gTTS
GTTS_AVAILABLE = False
try:
    from gtts import gTTS
    GTTS_AVAILABLE = True
except ImportError:
    pass

# Windows SAPI
WIN_SAPI_AVAILABLE = False
try:
    import win32com.client
    import pythoncom
    WIN_SAPI_AVAILABLE = True
except ImportError:
    pass

# 百度TTS
BAIDU_AVAILABLE = False
try:
    from aip import AipSpeech
    BAIDU_AVAILABLE = True
except ImportError:
    pass

from config import OUTPUT_AUDIO_BITRATE

# 加载环境变量
XUNFEI_APPID = os.getenv("XUNFEI_APPID", "")
XUNFEI_APIKEY = os.getenv("XUNFEI_APIKEY", "")
XUNFEI_APISECRET = os.getenv("XUNFEI_APISECRET", "")
BAIDU_APP_ID = os.getenv("BAIDU_APP_ID", "")
BAIDU_API_KEY = os.getenv("BAIDU_API_KEY", "")
BAIDU_SECRET_KEY = os.getenv("BAIDU_SECRET_KEY", "")

XUNFEI_AVAILABLE = all([XUNFEI_APPID, XUNFEI_APIKEY, XUNFEI_APISECRET])


class TTSModule:
    """TTS配音模块 - 支持多种后端"""

    # 讯飞TTS音色
    ZH_VOICES_XUNFEI = {
        "xiaoyan": "小燕（女声-温柔）",
        "aisjiuxu": "讯飞许久（男声-成熟）",
        "aisxingchen": "星辰（男声-青年）",
        "aisxiaoyi": "小七（女声-活泼）",
    }

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

    DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
    DEFAULT_RATE = 0

    def __init__(self, voice: str = None, backend: str = None):
        """初始化TTS模块"""
        self.voice = voice or self.DEFAULT_VOICE
        self.rate = self.DEFAULT_RATE
        self.backend = backend or self._detect_backend()
        self.voice_id = self._get_voice_id(voice)

    def _detect_backend(self) -> str:
        """检测可用的TTS后端（优先自然人声edge-tts，其次讯飞"""
        if EDGE_TTS_AVAILABLE:
            return "edge"
        elif XUNFEI_APPID and XUNFEI_APIKEY and XUNFEI_APISECRET and XUNFEI_AVAILABLE:
            return "xunfei"
        elif BAIDU_APP_ID and BAIDU_API_KEY and BAIDU_SECRET_KEY and BAIDU_AVAILABLE:
            return "baidu"
        elif WIN_SAPI_AVAILABLE:
            return "sapi"
        elif GTTS_AVAILABLE:
            return "gtts"
        return "none"

    def _get_voice_id(self, voice: str) -> str:
        """获取voice对应的后端音色ID"""
        if voice in self.ZH_VOICES_XUNFEI:
            return voice
        if voice in self.ZH_VOICES_EDGE:
            return voice
        if voice in self.ZH_VOICES_SAPI:
            return voice
        return self.DEFAULT_VOICE

    def _get_edge_voice(self) -> str:
        """获取edge-tts对应的voice名称"""
        edge_map = {
            "xiaoyan": "zh-CN-XiaoxiaoNeural",
            "aisjiuxu": "zh-CN-YunxiNeural",
            "aisxingchen": "zh-CN-YunxiNeural",
            "aisxiaoyi": "zh-CN-Xiaoyi",
        }
        return edge_map.get(self.voice, "zh-CN-XiaoxiaoNeural")

    # ========== 讯飞TTS (WebSocket v2 官方标准) ==========
    def _generate_xunfei(self, text: str, output_path: str) -> bool:
        """讯飞TTS WebSocket v2 官方标准接口"""
        if not XUNFEI_AVAILABLE:
            return False

        try:
            import websockets
            import os
            # 清除代理
            saved = {}
            for k in ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY']:
                saved[k] = os.environ.pop(k, None)

            try:
                # 生成授权URL (官方标准)
                date_gmt = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
                signature_origin = f"host: tts-api.xfyun.cn\ndate: {date_gmt}\nGET /v2/tts HTTP/1.1"
                signature_sha = hmac.new(
                    XUNFEI_APISECRET.encode(),
                    signature_origin.encode(),
                    digestmod=hashlib.sha256
                ).digest()
                signature = base64.b64encode(signature_sha).decode()
                authorization_origin = (
                    f'api_key="{XUNFEI_APIKEY}", algorithm="hmac-sha256", '
                    f'headers="host date request-line", signature="{signature}"'
                )
                authorization = base64.b64encode(authorization_origin.encode()).decode()
                uri = (
                    f"wss://tts-api.xfyun.cn/v2/tts"
                    f"?host=tts-api.xfyun.cn&date={urllib.parse.quote(date_gmt)}&authorization={urllib.parse.quote(authorization)}"
                )

                async def run_tts():
                    async with websockets.connect(uri) as ws:
                        frame = {
                            "common": {"app_id": XUNFEI_APPID},
                            "business": {
                                "aue": "lame",
                                "sfl": 1,
                                "vcn": self.voice or "xiaoyan",
                                "speed": 50,
                                "volume": 50
                            },
                            "data": {
                                "status": 2,
                                "text": base64.b64encode(text.encode("utf-8")).decode()
                            }
                        }
                        await ws.send(json.dumps(frame))
                        audio_data = b""
                        while True:
                            msg = await ws.recv()
                            resp = json.loads(msg)
                            if resp["code"] != 0:
                                print(f"讯飞TTS错误：code={resp['code']}, desc={resp.get('desc','')}")
                                return False
                            if isinstance(resp.get("data"), dict) and "audio" in resp["data"]:
                                audio_data += base64.b64decode(resp["data"]["audio"])
                            if resp.get("data", {}).get("status") == 2:
                                break
                        with open(output_path, "wb") as f:
                            f.write(audio_data)
                        return Path(output_path).exists()

                return asyncio.run(run_tts())

            finally:
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v

        except Exception as e:
            print(f"讯飞TTS失败：{str(e)}")
            return False

    # ========== 百度TTS (官方SDK) ==========
    def _generate_baidu(self, text: str, output_path: str) -> bool:
        """使用百度TTS官方SDK生成音频"""
        if not BAIDU_AVAILABLE:
            return False
        if not BAIDU_APP_ID or not BAIDU_API_KEY or not BAIDU_SECRET_KEY:
            return False

        try:
            client = AipSpeech(BAIDU_APP_ID, BAIDU_API_KEY, BAIDU_SECRET_KEY)
            result = client.synthesis(text, 'zh', 1, {
                'vol': 5,
                'spd': 5,
                'pit': 5,
                'per': 4  # 4=度丫丫，0=度小宇，1=度小美，3=度逍遥
            })

            if not isinstance(result, dict):
                with open(output_path, 'wb') as f:
                    f.write(result)
                return Path(output_path).exists()
            else:
                print(f"百度TTS错误：{result}")
                return False

        except Exception as e:
            print(f"百度TTS失败：{e}")
            return False

    # ========== SAPI ==========
    def _generate_sapi(self, text: str, output_path: str) -> bool:
        """使用Windows SAPI生成音频"""
        if not WIN_SAPI_AVAILABLE:
            return False

        try:
            import pythoncom
            pythoncom.CoInitialize()
            speaker = win32com.client.Dispatch('SAPI.SpVoice')

            if self.rate != 0:
                speaker.Rate = self.rate

            try:
                voices = speaker.GetVoices()
                for i in range(voices.Count):
                    voice = voices.Item(i)
                    if self.voice_id.lower() in voice.GetDescription().lower():
                        speaker.Voice = voice
                        break
            except:
                pass

            stream = win32com.client.Dispatch('SAPI.SpFileStream')
            stream.Open(output_path, 3)
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

    # ========== edge-tts ==========
    async def _generate_edge_async(self, text: str, output_path: str, voice: str = None) -> bool:
        if not EDGE_TTS_AVAILABLE:
            return False
        try:
            voice_to_use = voice or self._get_edge_voice()
            rate_str = f"{self.rate:+.0f}%" if self.rate != 0 else "+0%"
            communicate = edge_tts.Communicate(text, voice=voice_to_use, rate=rate_str)
            await communicate.save(output_path)
            return Path(output_path).exists()
        except Exception as e:
            print(f"Edge-TTS失败: {str(e)}")
            return False

    def _generate_edge(self, text: str, output_path: str, voice: str = None) -> bool:
        if not EDGE_TTS_AVAILABLE:
            return False
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self._generate_edge_async(text, output_path, voice))
                    return future.result()
            else:
                return asyncio.run(self._generate_edge_async(text, output_path, voice))
        except Exception as e:
            print(f"TTS生成失败: {str(e)}")
            return False

    def _generate_edge_no_proxy(self, text: str, output_path: str, voice: str = None) -> bool:
        if not EDGE_TTS_AVAILABLE:
            return False
        saved = {}
        proxy_keys = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'all_proxy']
        for k in proxy_keys:
            saved[k] = os.environ.pop(k, None)
        try:
            return self._generate_edge(text, output_path, voice)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    # ========== gTTS ==========
    def _generate_gtts(self, text: str, output_path: str) -> bool:
        if not GTTS_AVAILABLE:
            return False
        try:
            tts = gTTS(text=text, lang='zh-cn', slow=False)
            tts.save(output_path)
            return Path(output_path).exists()
        except Exception as e:
            print(f"gTTS失败: {str(e)}")
            return False

    # ========== 主生成函数 ==========
    def generate_audio(self, text: str, output_path: str) -> bool:
        """
        生成配音音频 - 优先级: 讯飞 > 百度 > edge > gTTS > SAPI
        """
        if not text or not text.strip():
            return False

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 讯飞
        if self.backend == "xunfei":
            result = self._generate_xunfei(text, output_path)
            if not result and BAIDU_AVAILABLE:
                print("讯飞TTS失败，尝试百度...")
                result = self._generate_baidu(text, output_path)
            if not result and EDGE_TTS_AVAILABLE:
                print("百度TTS失败，尝试edge-tts...")
                result = self._generate_edge_no_proxy(text, output_path)
            if not result and GTTS_AVAILABLE:
                print("edge-tts失败，尝试gTTS...")
                result = self._generate_gtts(text, output_path)
            if not result and WIN_SAPI_AVAILABLE:
                print("gTTS失败，尝试SAPI...")
                result = self._generate_sapi(text, output_path)
            return result
        # 百度
        elif self.backend == "baidu":
            result = self._generate_baidu(text, output_path)
            if not result and XUNFEI_AVAILABLE:
                print("百度TTS失败，尝试讯飞...")
                result = self._generate_xunfei(text, output_path)
            if not result and EDGE_TTS_AVAILABLE:
                print("讯飞失败，尝试edge-tts...")
                result = self._generate_edge_no_proxy(text, output_path)
            if not result and GTTS_AVAILABLE:
                print("edge-tts失败，尝试gTTS...")
                result = self._generate_gtts(text, output_path)
            if not result and WIN_SAPI_AVAILABLE:
                print("gTTS失败，尝试SAPI...")
                result = self._generate_sapi(text, output_path)
            return result
        # edge
        elif self.backend == "edge":
            result = self._generate_edge_no_proxy(text, output_path)
            if not result and BAIDU_AVAILABLE:
                print("Edge-TTS失败，尝试百度...")
                result = self._generate_baidu(text, output_path)
            if not result and XUNFEI_AVAILABLE:
                print("百度TTS失败，尝试讯飞...")
                result = self._generate_xunfei(text, output_path)
            if not result and GTTS_AVAILABLE:
                print("讯飞失败，尝试gTTS...")
                result = self._generate_gtts(text, output_path)
            if not result and WIN_SAPI_AVAILABLE:
                print("gTTS失败，尝试SAPI...")
                result = self._generate_sapi(text, output_path)
            return result
        # SAPI
        elif self.backend == "sapi":
            result = self._generate_sapi(text, output_path)
            if not result and GTTS_AVAILABLE:
                print("SAPI失败，尝试gTTS...")
                result = self._generate_gtts(text, output_path)
            if not result and EDGE_TTS_AVAILABLE:
                print("gTTS失败，尝试edge-tts...")
                result = self._generate_edge_no_proxy(text, output_path)
            if not result and BAIDU_AVAILABLE:
                print("edge-tts失败，尝试百度...")
                result = self._generate_baidu(text, output_path)
            if not result and XUNFEI_AVAILABLE:
                print("百度失败，尝试讯飞...")
                result = self._generate_xunfei(text, output_path)
            return result
        # gTTS
        elif self.backend == "gtts":
            result = self._generate_gtts(text, output_path)
            if not result and WIN_SAPI_AVAILABLE:
                print("gTTS失败，尝试SAPI...")
                result = self._generate_sapi(text, output_path)
            if not result and EDGE_TTS_AVAILABLE:
                print("SAPI失败，尝试edge-tts...")
                result = self._generate_edge_no_proxy(text, output_path)
            if not result and BAIDU_AVAILABLE:
                print("edge-tts失败，尝试百度...")
                result = self._generate_baidu(text, output_path)
            if not result and XUNFEI_AVAILABLE:
                print("百度失败，尝试讯飞...")
                result = self._generate_xunfei(text, output_path)
            return result
        else:
            print("错误: 没有可用的TTS后端")
            return False

    def generate_from_segments(self, segments: List[Dict], output_dir: str,
                               voice: Optional[str] = None) -> Tuple[bool, List[str]]:
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
        if voice:
            self.voice = voice
            self.voice_id = self._get_voice_id(voice)

        if not segments:
            return False

        temp_dir = tempfile.mkdtemp()
        success, audio_files = self.generate_from_segments(segments, temp_dir)

        if not success or not audio_files:
            for f in Path(temp_dir).glob("*"):
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass
            try:
                Path(temp_dir).rmdir()
            except FileNotFoundError:
                pass
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
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        try:
            Path(temp_dir).rmdir()
        except FileNotFoundError:
            pass

        return success

    def set_rate(self, rate: int):
        """设置语速，-10 到 +10"""
        self.rate = max(-10, min(10, rate))
        return self

    def get_available_voices(self) -> Dict[str, str]:
        """获取可用的配音人列表"""
        voices = {}
        voices.update(self.ZH_VOICES_XUNFEI)
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
