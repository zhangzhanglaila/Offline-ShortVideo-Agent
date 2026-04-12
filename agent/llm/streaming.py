# -*- coding: utf-8 -*-
"""
流式输出支持 - SSE实时推送AI回复
"""
import json
import urllib.request
import urllib.error
from typing import Iterator, List, Dict, Generator
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT, OPENAI_API_KEY, OPENAI_API_BASE, OPENAI_MODEL
except ImportError:
    OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-14b")
    OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")


class StreamingOllamaClient:
    """支持流式输出的Ollama客户端"""

    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL):
        self.base_url = base_url
        self.model = model
        self.api_url = f"{base_url}/api/chat"

    def chat_stream(self, messages: List[Dict[str, str]],
                    tools: List[Dict] = None,
                    temperature: float = 0.7) -> Generator[str, None, None]:
        """
        流式聊天接口

        Yields:
            每个 token 作为字符串yield出来
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "top_p": 0.9,
            }
        }

        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as response:
                for line in response:
                    if line:
                        try:
                            chunk = json.loads(line.decode("utf-8"))
                            if 'message' in chunk:
                                content = chunk['message'].get('content', '')
                                if content:
                                    yield content
                            # 检查是否完成
                            if chunk.get('done', False):
                                break
                        except json.JSONDecodeError:
                            continue

        except urllib.error.URLError as e:
            raise ConnectionError(f"无法连接到 Ollama 服务: {e}")
        except Exception as e:
            raise ConnectionError(f"流式输出失败: {e}")

    def chat(self, messages: List[Dict[str, str]],
             tools: List[Dict] = None,
             temperature: float = 0.7) -> str:
        """非流式聊天接口（兼容）"""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": 0.9,
            }
        }

        if tools:
            payload["tools"] = tools

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result.get("message", {}).get("content", "")
        except Exception as e:
            raise ConnectionError(f"Ollama 调用失败: {e}")


# 全局单例
_streaming_client = None


def get_streaming_client() -> StreamingOllamaClient:
    """获取流式客户端单例"""
    global _streaming_client
    if _streaming_client is None:
        _streaming_client = StreamingOllamaClient()
    return _streaming_client
