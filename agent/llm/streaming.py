# -*- coding: utf-8 -*-
"""
流式输出支持 - 双模式（本地Ollama + 云端API）
"""
import json
import urllib.request
import urllib.error
from typing import Iterator, List, Dict, Generator
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 支持从config读取，若无配置则使用环境变量
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
    """支持流式输出的双模式客户端"""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL
    ):
        self.base_url = base_url
        self.model = model
        self.timeout = OLLAMA_TIMEOUT
        self.api_url = f"{base_url}/api/chat"
        self.cloud_api_base = OPENAI_API_BASE
        self.cloud_model = OPENAI_MODEL
        self.cloud_api_key = OPENAI_API_KEY

    def _check_ollama_available(self) -> bool:
        """检查本地Ollama是否可用"""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method='GET')
            with urllib.request.urlopen(req, timeout=2):
                return True
        except Exception:
            return False

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = 0.7
    ) -> Generator[str, None, None]:
        """
        流式聊天接口 - 自动选择本地或云端
        """
        # 优先使用本地Ollama
        if self._check_ollama_available():
            yield from self._ollama_stream(messages, tools, temperature)
            return

        # 本地不可用，尝试云端
        if self.cloud_api_key:
            yield from self._cloud_stream(messages, tools, temperature)
            return

        # 都不可用
        yield "本地模型未启动且未配置云端API，无法处理请求。\n\n请：\n1. 运行 'ollama serve' 启动本地服务\n2. 或在 config.py 中配置 OPENAI_API_KEY 使用云端API"

    def _ollama_stream(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = 0.7
    ) -> Generator[str, None, None]:
        """本地Ollama流式输出"""
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
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                for line in response:
                    if line:
                        try:
                            chunk = json.loads(line.decode("utf-8"))
                            if 'message' in chunk:
                                content = chunk['message'].get('content', '')
                                if content:
                                    yield content
                                if 'tool_calls' in chunk['message']:
                                    yield json.dumps(chunk['message']['tool_calls'], ensure_ascii=False)
                            if chunk.get('done', False):
                                break
                        except json.JSONDecodeError:
                            continue
        except urllib.error.URLError:
            yield "无法连接到本地Ollama服务"
        except Exception as e:
            yield f"流式输出失败: {e}"

    def _cloud_stream(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = 0.7
    ) -> Generator[str, None, None]:
        """云端API流式输出"""
        payload = {
            "model": self.cloud_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.cloud_api_key}",
        }

        api_url = f"{self.cloud_api_base.rstrip('/')}/chat/completions"
        req = urllib.request.Request(api_url, data=data, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                for line in response:
                    if line:
                        try:
                            line_text = line.decode("utf-8").strip()
                            if not line_text or line_text.startswith(":"):
                                continue
                            if line_text.startswith("data:"):
                                line_text = line_text[5:].strip()
                            if line_text == "[DONE]":
                                break
                            chunk = json.loads(line_text)
                            if "choices" in chunk and len(chunk["choices"]) > 0:
                                delta = chunk["choices"][0].get("delta", {})
                                if "content" in delta and delta["content"]:
                                    yield delta["content"]
                                if "tool_calls" in delta:
                                    for tc in delta["tool_calls"]:
                                        yield json.dumps(tc, ensure_ascii=False)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
        except urllib.error.HTTPError as e:
            yield f"云端API请求失败 (HTTP {e.code})"
        except urllib.error.URLError:
            yield f"无法连接到云端API服务"
        except Exception as e:
            yield f"流式输出失败: {e}"

    def chat(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = 0.7
    ) -> str:
        """非流式聊天接口（兼容）"""
        result = ""
        for chunk in self.chat_stream(messages, tools, temperature):
            result += chunk
        return result


# 全局单例
_streaming_client = None


def get_streaming_client() -> StreamingOllamaClient:
    """获取流式客户端单例"""
    global _streaming_client
    if _streaming_client is None:
        _streaming_client = StreamingOllamaClient()
    return _streaming_client
