# -*- coding: utf-8 -*-
"""
时间轴同步模块 - 基于faster-whisper语音识别
自动对齐：脚本分句、字幕时间轴、配音时间轴、图片切换时机
"""
# 【重要】必须在 import faster_whisper 之前设置镜像站点
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

import re
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from difflib import SequenceMatcher

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

from config import WHISPER_MODEL, WHISPER_LANGUAGE


class TimelineSyncModule:
    """时间轴同步模块 - 基于Whisper识别"""

    def __init__(self, model_size: str = WHISPER_MODEL):
        """初始化时间轴同步模块"""
        self.model_size = model_size
        self.model = None
        self._load_model()

    def _load_model(self):
        """加载Whisper模型"""
        # 设置HuggingFace镜像，解决网络问题
        import os
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

        if not WHISPER_AVAILABLE:
            print("[WARNING] faster-whisper not installed, timeline sync capability will be limited")
            print("   To enable this feature, run: pip install faster-whisper")
            return

        # 本地模型路径
        local_model_path = Path(__file__).parent.parent / "models" / f"faster-whisper-{self.model_size}"

        try:
            # 检查本地模型是否存在
            if local_model_path.exists() and (local_model_path / "model.bin").exists():
                print(f"[LOAD] Loading timeline sync model '{self.model_size}' from local...")
                self.model = WhisperModel(
                    str(local_model_path),
                    device="cpu",
                    compute_type="int8"
                )
                print(f"[OK] Timeline sync model '{self.model_size}' loaded successfully")
            else:
                print(f"[DOWNLOAD] Loading timeline sync model '{self.model_size}'...")
                self.model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8"
                )
                print(f"[OK] Timeline sync model '{self.model_size}' loaded successfully")
        except Exception as e:
            error_msg = str(e)
            print(f"[FAIL] Timeline sync model loading failed: {error_msg}")
            if 'Hub' in error_msg or 'snapshot' in error_msg:
                print("   Reason: Cannot connect to HuggingFace")
                print("   Solution: Manually download model or use fallback")
            elif 'model' in error_msg.lower() and 'not found' in error_msg.lower():
                print("   Reason: Local model file does not exist or corrupted")
                print(f"   Model path: {local_model_path}")
            self.model = None

    def transcribe_audio(self, audio_path: str, language: str = WHISPER_LANGUAGE) -> List[Dict]:
        """
        语音转文字（带时间戳）

        参数:
            audio_path: 音频文件路径
            language: 语言代码

        返回:
            识别结果列表，每项包含 start, end, text
        """
        if not self.model:
            return []

        try:
            segments, info = self.model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300)
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

    def align_script_with_audio(self, script_sentences: List[str],
                                audio_segments: List[Dict],
                                tolerance: float = 2.0) -> List[Dict]:
        """
        将脚本分句与音频识别结果对齐

        参数:
            script_sentences: 脚本分句列表
            audio_segments: Whisper识别的音频片段
            tolerance: 允许的时间误差（秒）

        返回:
            对齐后的时间轴列表，每项包含 start, end, text, sentence_index
        """
        if not script_sentences or not audio_segments:
            return self._fallback_align(script_sentences, audio_segments)

        aligned = []
        audio_idx = 0
        current_time = 0.0

        for i, sentence in enumerate(script_sentences):
            if not sentence.strip():
                continue

            # 清理文本用于匹配
            clean_sentence = self._clean_text(sentence)

            # 在音频片段中查找匹配
            best_match = None
            best_score = 0

            while audio_idx < len(audio_segments):
                audio_text = self._clean_text(audio_segments[audio_idx].get("text", ""))

                # 计算相似度
                score = self._calculate_similarity(clean_sentence, audio_text)

                if score > 0.5 and score > best_score:
                    best_match = audio_segments[audio_idx]
                    best_score = score

                    # 如果相似度很高，跳到下一个音频片段
                    if score > 0.8:
                        audio_idx += 1
                        break

                audio_idx += 1
                if audio_idx >= len(audio_segments):
                    break

            if best_match:
                aligned.append({
                    "start": best_match["start"],
                    "end": best_match["end"],
                    "text": sentence,
                    "sentence_index": i,
                    "confidence": best_score
                })
                current_time = best_match["end"]
            else:
                # 未能匹配，使用估算时间
                duration = len(sentence) / 4.0  # 约4字/秒
                aligned.append({
                    "start": current_time,
                    "end": current_time + duration,
                    "text": sentence,
                    "sentence_index": i,
                    "confidence": 0.0
                })
                current_time += duration

        return aligned

    def generate_timeline_from_audio(self, audio_path: str,
                                     num_images: int = None) -> List[Dict]:
        """
        根据音频自动生成时间轴（用于图片切换）

        参数:
            audio_path: 配音音频路径
            num_images: 图片数量（可选）

        返回:
            时间轴列表，每项包含 start, end, image_index
        """
        segments = self.transcribe_audio(audio_path)
        if not segments:
            return []

        # 合并识别结果为完整句子
        sentences = []
        current_sentence = ""
        current_start = 0.0

        for seg in segments:
            text = seg["text"]
            if not current_sentence:
                current_start = seg["start"]

            current_sentence += text

            # 检测句子结束（中英文句号）
            if re.search(r'[。！？.!?]', text):
                sentences.append({
                    "start": current_start,
                    "end": seg["end"],
                    "text": current_sentence.strip()
                })
                current_sentence = ""

        if current_sentence:
            sentences.append({
                "start": current_start,
                "end": segments[-1]["end"] if segments else 0,
                "text": current_sentence.strip()
            })

        # 如果指定了图片数量，重新分配时间
        if num_images and num_images > 0:
            total_duration = sentences[-1]["end"] if sentences else 0
            duration_per_image = total_duration / num_images

            result = []
            for i in range(num_images):
                start = i * duration_per_image
                end = (i + 1) * duration_per_image

                # 找到该时间段对应的文本
                text = ""
                for s in sentences:
                    if s["start"] < end and s["end"] > start:
                        text += s["text"] + " "

                result.append({
                    "start": start,
                    "end": end,
                    "image_index": i,
                    "text": text.strip()[:50]
                })

            return result

        return sentences

    def sync_subtitles_to_audio(self, audio_path: str,
                                 subtitle_segments: List[Dict] = None,
                                 original_script: str = None) -> Tuple[List[Dict], List[Dict]]:
        """
        同步字幕时间轴到音频

        参数:
            audio_path: 配音音频路径
            subtitle_segments: 原始字幕段（基于脚本生成的）
            original_script: 原始脚本文本

        返回:
            (对齐后的字幕列表, 音频识别结果列表)
        """
        # 语音识别
        audio_segments = self.transcribe_audio(audio_path)

        if not audio_segments:
            return subtitle_segments or [], []

        # 如果没有提供字幕片段，基于脚本生成分句
        if not subtitle_segments and original_script:
            from core.subtitle_module import get_subtitle_module
            sub_mod = get_subtitle_module()
            sentences = sub_mod._split_sentences(original_script)
            subtitle_segments = []
            total_duration = audio_segments[-1]["end"] if audio_segments else 30
            duration_per = total_duration / len(sentences) if sentences else 3
            for i, s in enumerate(sentences):
                subtitle_segments.append({
                    "start": i * duration_per,
                    "end": (i + 1) * duration_per,
                    "text": s
                })

        # 对齐
        script_sentences = [s.get("text", "") for s in subtitle_segments]
        aligned = self.align_script_with_audio(script_sentences, audio_segments)

        return aligned, audio_segments

    def _clean_text(self, text: str) -> str:
        """清理文本用于匹配"""
        # 移除标点、空白
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', '', text)
        return text.lower()

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """计算两个文本的相似度"""
        if not text1 or not text2:
            return 0.0
        return SequenceMatcher(None, text1, text2).ratio()

    def _fallback_align(self, sentences: List[str],
                        audio_segments: List[Dict]) -> List[Dict]:
        """降级对齐：当Whisper不可用时使用平均分配"""
        if not sentences:
            return []

        total_duration = audio_segments[-1]["end"] if audio_segments else len(sentences) * 3
        duration_per = total_duration / len(sentences)

        result = []
        current_time = 0.0

        for i, sentence in enumerate(sentences):
            start = i * duration_per
            end = (i + 1) * duration_per
            result.append({
                "start": start,
                "end": end,
                "text": sentence,
                "sentence_index": i,
                "confidence": 0.0
            })

        return result

    def export_timeline_json(self, timeline: List[Dict], output_path: str) -> bool:
        """导出时间轴为JSON文件"""
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(timeline, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"时间轴导出失败: {str(e)}")
            return False


# ==================== 便捷函数 ====================
_module_instance = None


def get_timeline_module(model_size: str = WHISPER_MODEL) -> TimelineSyncModule:
    """获取时间轴同步模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = TimelineSyncModule(model_size)
    return _module_instance


def sync_audio_timeline(audio_path: str, script: str = None,
                         num_images: int = None) -> List[Dict]:
    """快速同步音频时间轴"""
    module = get_timeline_module()
    return module.generate_timeline_from_audio(audio_path, num_images)
