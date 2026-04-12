# -*- coding: utf-8 -*-
"""
云端LLM客户端 - OpenAI格式API兼容（通义千问/DeepSeek/GLM-4/GPT等）
支持原生Function Calling
"""
import json
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Generator, Any
import sys
import os
import time

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 支持从config读取，若无配置则使用环境变量或默认值
try:
    from config import (
        OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
        OPENAI_API_KEY, OPENAI_API_BASE, OPENAI_MODEL
    )
except ImportError:
    OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-14b")
    OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "120"))
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_API_BASE = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# 默认云端API配置
DEFAULT_CLOUD_CONFIG = {
    "api_key": OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", ""),
    "api_base": OPENAI_API_BASE or os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
    "model": OPENAI_MODEL or os.environ.get("OPENAI_MODEL", "gpt-4o"),
    "timeout": OLLAMA_TIMEOUT,
}


class OllamaClient:
    """云端LLM客户端 - OpenAI格式API兼容"""

    def __init__(
        self,
        base_url: str = None,
        model: str = None,
        api_key: str = None,
        model_name: str = None
    ):
        # 优先使用传入参数，其次环境变量，最后默认配置
        self.base_url = base_url or DEFAULT_CLOUD_CONFIG["api_base"]
        self.model = model or model_name or DEFAULT_CLOUD_CONFIG["model"]
        self.api_key = api_key or DEFAULT_CLOUD_CONFIG["api_key"]
        self.timeout = DEFAULT_CLOUD_CONFIG["timeout"]
        self.api_url = f"{self.base_url.rstrip('/')}/chat/completions"

    def _make_request(self, payload: Dict, timeout: int = None) -> Dict:
        """发送API请求"""
        if timeout is None:
            timeout = self.timeout

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers=headers
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                pass
            if e.code == 401:
                raise ConnectionError(f"API密钥无效或已过期，请检查配置")
            elif e.code == 403:
                raise ConnectionError(f"API访问被拒绝，请检查API密钥权限")
            elif e.code == 429:
                raise ConnectionError(f"API请求频率超限，请稍后重试")
            else:
                raise ConnectionError(f"API请求失败 (HTTP {e.code}): {error_body[:200]}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"无法连接到云端API服务 ({self.base_url})。请检查网络连接或API地址配置")
        except Exception as e:
            raise ConnectionError(f"云端API调用失败: {str(e)}")

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        timeout: int = None,
        tools: List[Dict] = None,
        **kwargs
    ) -> str:
        """
        聊天接口 - 支持OpenAI格式

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            temperature: 温度参数
            timeout: 超时时间
            tools: 工具列表，OpenAI格式

        Returns:
            AI回复文本
        """
        # 检查API配置
        if not self.api_key:
            raise ConnectionError(
                "云端API密钥未配置。\n\n请在 config.py 中设置 OPENAI_API_KEY 环境变量：\n"
                "1. 登录云端API控制台获取API密钥\n"
                "2. 在 config.py 中配置：OPENAI_API_KEY = 'your-api-key'\n"
                "3. 或设置环境变量：export OPENAI_API_KEY='your-api-key'"
            )

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        result = self._make_request(payload, timeout)

        # 解析OpenAI格式响应
        if "choices" in result and len(result["choices"]) > 0:
            choice = result["choices"][0]
            # 检查是否有函数调用
            if "message" in choice:
                msg = choice["message"]
                if "tool_calls" in msg:
                    # 返回工具调用信息
                    return json.dumps({
                        "content": msg.get("content", ""),
                        "tool_calls": msg["tool_calls"]
                    }, ensure_ascii=False)
                elif "content" in msg:
                    return msg.get("content", "")
            if "text" in choice:
                return choice.get("text", "")

        raise ConnectionError(f"API响应格式异常: {str(result)[:100]}")

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> Generator[str, None, None]:
        """
        流式聊天接口

        Yields:
            每个 token 作为字符串yield出来
        """
        if not self.api_key:
            error_msg = (
                "云端API密钥未配置。\n\n"
                "请在 config.py 中设置 OPENAI_API_KEY 环境变量：\n"
                "1. 登录云端API控制台获取API密钥\n"
                "2. 在 config.py 中配置：OPENAI_API_KEY = 'your-api-key'\n"
                "3. 或设置环境变量：export OPENAI_API_KEY='your-api-key'"
            )
            yield error_msg
            return

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers=headers
        )

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
                                # 处理增量内容
                                if "content" in delta and delta["content"]:
                                    yield delta["content"]
                                # 处理工具调用
                                if "tool_calls" in delta:
                                    tool_calls = delta["tool_calls"]
                                    for tc in tool_calls:
                                        yield json.dumps(tc, ensure_ascii=False)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
        except urllib.error.HTTPError as e:
            try:
                error_body = json.loads(e.read().decode("utf-8"))
                error_msg = error_body.get("error", {}).get("message", str(e))
            except Exception:
                error_msg = str(e)
            if e.code == 401:
                yield "API密钥无效或已过期，请检查配置"
            elif e.code == 403:
                yield "API访问被拒绝，请检查API密钥权限"
            elif e.code == 429:
                yield "API请求频率超限，请稍后重试"
            else:
                yield f"API请求失败 (HTTP {e.code}): {error_msg[:100]}"
        except urllib.error.URLError as e:
            yield f"无法连接到云端API服务 ({self.base_url})。请检查网络连接或API地址配置"
        except Exception as e:
            yield f"流式输出失败: {str(e)}"

    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        timeout: int = None,
        **kwargs
    ) -> str:
        """
        生成接口（用于简单生成任务）
        将 prompt 包装为单条用户消息
        """
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, temperature, timeout, **kwargs)

    def extract_json_from_response(self, text: str) -> Optional[Dict]:
        """从文本响应中提取JSON"""
        import re
        # 尝试提取JSON对象
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        # 尝试解析整个响应为JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        return None

    def convert_tools_to_openai_format(self, tools: List[Any]) -> List[Dict]:
        """将工具转换为OpenAI格式"""
        openai_tools = []
        for tool in tools:
            if hasattr(tool, 'definition'):
                # 标准工具格式
                definition = tool.definition
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": definition.name,
                        "description": definition.description,
                        "parameters": definition.to_openai_format().get("parameters", {
                            "type": "object",
                            "properties": {},
                            "required": []
                        })
                    }
                })
            elif isinstance(tool, dict):
                # 已经是字典格式
                openai_tools.append(tool)
        return openai_tools

    def check_connection(self) -> bool:
        """检查API连接是否正常"""
        if not self.api_key:
            return False
        try:
            # 发送一个简单的测试请求
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
                "stream": False
            }
            self._make_request(payload, timeout=10)
            return True
        except Exception:
            return False


# 全局单例
_llm_client = None


def get_llm_client() -> OllamaClient:
    """获取LLM客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = OllamaClient()
    return _llm_client


def reset_llm_client():
    """重置LLM客户端（用于重新配置）"""
    global _llm_client
    _llm_client = None
