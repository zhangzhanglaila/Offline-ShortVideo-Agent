# -*- coding: utf-8 -*-
"""
Ollama LLM客户端 - 兼容qwen2:7b非原生Function Calling
"""
import json
import urllib.request
import urllib.error
from typing import List, Dict, Optional
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT


class OllamaClient:
    """Ollama LLM客户端"""

    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL):
        self.base_url = base_url
        self.model = model
        self.api_url = f"{base_url}/api/chat"
        self.generate_url = f"{base_url}/api/generate"

    def chat(self, messages: List[Dict[str, str]],
             temperature: float = 0.7,
             timeout: int = OLLAMA_TIMEOUT) -> str:
        """聊天接口"""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": 0.9,
            }
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result.get("message", {}).get("content", "")
        except Exception as e:
            return f"Error: {str(e)}"

    def generate(self, prompt: str,
                 temperature: float = 0.7,
                 timeout: int = OLLAMA_TIMEOUT) -> str:
        """生成接口（用于简单生成任务）"""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": 0.9,
                "num_predict": 2048,
            }
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.generate_url,
            data=data,
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return result.get("response", "")
        except Exception as e:
            return f"Error: {str(e)}"

    def extract_json_from_response(self, text: str) -> Optional[Dict]:
        """从文本响应中提取JSON"""
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except:
                pass
        return None


# 全局单例
_llm_client = None


def get_llm_client() -> OllamaClient:
    """获取LLM客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = OllamaClient()
    return _llm_client
