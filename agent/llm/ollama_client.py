# -*- coding: utf-8 -*-
"""
LLM客户端 - 本地Ollama + 云端API双模式支持
优先使用本地Ollama，若不可用则自动尝试云端API
支持原生Function Calling
"""
import json
import urllib.request
import urllib.error
from typing import List, Dict, Optional, Generator, Any
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ========== 配置读取 ==========
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


# ========== 错误消息定义 ==========
class LLMError:
    """LLM错误消息"""

    # 场景1：本地Ollama未启动
    OLLAMA_NOT_RUNNING = (
        "未检测到本地Ollama服务，可配置云端API继续使用。\n\n"
        "本地Ollama未启动，若已安装可运行 'ollama serve' 启动。\n"
        "或配置下方云端API直接使用在线大模型。"
    )

    # 场景2：未配置云端API密钥
    CLOUD_API_NOT_CONFIGURED = (
        "未配置云端API密钥，无法使用云端大模型。\n\n"
        "请在 config.py 中填写以下配置：\n"
        "OPENAI_API_KEY = 'your-api-key'        # API密钥\n"
        "OPENAI_API_BASE = 'https://xxx/v1'     # 接口地址\n"
        "OPENAI_MODEL = 'gpt-4o'                # 模型名称"
    )

    # 场景3：两种方式都不可用
    ALL_UNAVAILABLE = (
        "本地模型未启动且未配置云端API，无法处理请求。\n\n"
        "请选择以下任一方式解决：\n"
        "方式1 - 启动本地Ollama：\n"
        "  • 已安装：运行 'ollama serve' 启动服务\n"
        "  • 未安装：到 https://ollama.com 下载安装\n"
        "  • 下载模型：ollama pull qwen2.5-14b\n\n"
        "方式2 - 配置云端API：\n"
        "  • 在 config.py 中填写 OPENAI_API_KEY 等配置"
    )

    # Ollama启动但调用失败
    OLLAMA_CALL_FAILED = (
        "本地Ollama服务异常，调用失败：{error}\n\n"
        "可配置云端API作为备选方案。"
    )

    # 云API调用失败
    CLOUD_CALL_FAILED = (
        "云端API调用失败：{error}\n\n"
        "请检查：\n"
        "1. API密钥是否正确\n"
        "2. API地址是否可达\n"
        "3. 是否有足够的API额度"
    )


# ========== 本地Ollama客户端 ==========
class OllamaClient:
    """本地Ollama客户端"""

    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL, timeout: int = OLLAMA_TIMEOUT):
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.api_url = f"{base_url}/api/chat"

    def check_available(self) -> bool:
        """检查Ollama是否可用"""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/tags",
                method='GET'
            )
            with urllib.request.urlopen(req, timeout=2) as response:
                return response.status == 200
        except Exception:
            return False

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        timeout: int = None,
        tools: List[Dict] = None,
        **kwargs
    ) -> str:
        """聊天接口"""
        if timeout is None:
            timeout = self.timeout

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
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                # 解析Ollama响应格式
                if "message" in result:
                    msg = result["message"]
                    if "tool_calls" in msg:
                        return json.dumps({
                            "content": msg.get("content", ""),
                            "tool_calls": msg["tool_calls"]
                        }, ensure_ascii=False)
                    return msg.get("content", "")
        except urllib.error.URLError as e:
            raise ConnectionError(f"无法连接到本地Ollama服务 ({self.base_url})")
        except Exception as e:
            raise ConnectionError(f"Ollama调用失败: {str(e)}")

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> Generator[str, None, None]:
        """流式聊天接口"""
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
                                # 处理工具调用
                                if 'tool_calls' in chunk['message']:
                                    yield json.dumps(chunk['message']['tool_calls'], ensure_ascii=False)
                            if chunk.get('done', False):
                                break
                        except json.JSONDecodeError:
                            continue
        except urllib.error.URLError:
            raise ConnectionError("无法连接到本地Ollama服务")
        except Exception as e:
            raise ConnectionError(f"流式输出失败: {str(e)}")

    def generate(self, prompt: str, temperature: float = 0.7, timeout: int = None, **kwargs) -> str:
        """生成接口"""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, temperature, timeout, **kwargs)


# ========== 云端API客户端 ==========
class CloudClient:
    """云端API客户端 - OpenAI格式"""

    def __init__(
        self,
        api_base: str = None,
        model: str = None,
        api_key: str = None,
        timeout: int = OLLAMA_TIMEOUT
    ):
        self._api_base = api_base
        self._model = model
        self._api_key = api_key
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        if self._api_base:
            return self._api_base
        # 每次读取最新配置
        from config import OPENAI_API_BASE
        return OPENAI_API_BASE or "https://api.openai.com/v1"

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        from config import OPENAI_MODEL
        return OPENAI_MODEL or "gpt-4o"

    @property
    def api_key(self) -> str:
        if self._api_key:
            return self._api_key
        from config import OPENAI_API_KEY
        return OPENAI_API_KEY or ""

    @property
    def api_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"

    def check_available(self) -> bool:
        """检查云端API是否可用（不抛异常）"""
        try:
            self._check_api_key()
            return True
        except Exception:
            return False

    def _check_api_key(self):
        """检查API密钥是否有效（会抛异常）"""
        if not self.api_key:
            raise ConnectionError("未配置云端API密钥")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
            "stream": False
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        req = urllib.request.Request(self.api_url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ConnectionError("API密钥无效或已过期，请检查配置")
            raise

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

        req = urllib.request.Request(self.api_url, data=data, headers=headers)

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
                raise ConnectionError("API密钥无效或已过期，请检查配置")
            elif e.code == 403:
                raise ConnectionError("API访问被拒绝，请检查API密钥权限")
            elif e.code == 429:
                raise ConnectionError("API请求频率超限，请稍后重试")
            else:
                raise ConnectionError(f"API请求失败 (HTTP {e.code}): {error_body[:200]}")
        except urllib.error.URLError as e:
            raise ConnectionError(f"无法连接到云端API服务 ({self.base_url})，请检查网络或API地址配置")
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
        """聊天接口"""
        if not self.api_key:
            raise ConnectionError("未配置云端API密钥")

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
            if "message" in choice:
                msg = choice["message"]
                if "tool_calls" in msg:
                    return json.dumps({
                        "content": msg.get("content", ""),
                        "tool_calls": msg["tool_calls"]
                    }, ensure_ascii=False)
                elif "content" in msg:
                    return msg.get("content", "")

        raise ConnectionError(f"API响应格式异常")

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> Generator[str, None, None]:
        """流式聊天接口"""
        if not self.api_key:
            yield "未配置云端API密钥"
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

        req = urllib.request.Request(self.api_url, data=data, headers=headers)

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
            yield f"无法连接到云端API服务 ({self.base_url})，请检查网络或API地址配置"
        except Exception as e:
            yield f"流式输出失败: {str(e)}"

    def generate(self, prompt: str, temperature: float = 0.7, timeout: int = None, **kwargs) -> str:
        """生成接口"""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, temperature, timeout, **kwargs)


# ========== 双模式客户端（自动切换） ==========
class DualModeLLMClient:
    """
    双模式LLM客户端
    优先使用本地Ollama，若不可用则自动尝试云端API
    """

    def __init__(
        self,
        ollama_base_url: str = OLLAMA_BASE_URL,
        ollama_model: str = OLLAMA_MODEL,
        cloud_api_base: str = None,
        cloud_model: str = None,
        cloud_api_key: str = None,
        timeout: int = OLLAMA_TIMEOUT
    ):
        self.local = OllamaClient(ollama_base_url, ollama_model, timeout)
        self.cloud = CloudClient(cloud_api_base, cloud_model, cloud_api_key, timeout)
        self._use_cloud = False

    @property
    def model(self) -> str:
        if self._use_cloud:
            return self.cloud.model
        return self.local.model

    @property
    def base_url(self) -> str:
        if self._use_cloud:
            return self.cloud.base_url
        return self.local.base_url

    def _get_error_info(self) -> tuple:
        """
        获取错误信息
        Returns: (is_ollama_available, is_cloud_available, error_message)
        """
        ollama_ok = self.local.check_available()

        # 单独检查云端密钥状态（不吞掉401/403异常）
        cloud_auth_error = None
        cloud_ok = False
        try:
            self.cloud._check_api_key()
            cloud_ok = True
        except ConnectionError as e:
            msg = str(e)
            if "API密钥无效" in msg or "已过期" in msg or "未配置" in msg:
                cloud_auth_error = msg
            # 否则是网络等其他问题，不算auth错误
        except Exception:
            pass

        if cloud_auth_error:
            # 云端密钥无效（401/403）优先处理
            return False, False, cloud_auth_error
        elif not ollama_ok and not cloud_ok:
            return False, False, LLMError.ALL_UNAVAILABLE
        elif not ollama_ok and cloud_ok:
            # 云端可用但ollama不可用时，检查key是否为空
            if not self.cloud.api_key:
                return False, False, LLMError.CLOUD_API_NOT_CONFIGURED
            # 云端可用，无错误（前端不需要弹出配置框）
            return False, True, None
        elif ollama_ok and not cloud_ok:
            return True, False, LLMError.CLOUD_API_NOT_CONFIGURED
        return True, True, None

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        timeout: int = None,
        tools: List[Dict] = None,
        **kwargs
    ) -> str:
        """
        聊天接口 - 双模式自动切换
        优先本地，不可用时自动切云端
        """
        # 先尝试本地
        if self.local.check_available():
            try:
                return self.local.chat(messages, temperature, timeout, tools, **kwargs)
            except ConnectionError as e:
                # 本地Ollama可用但调用失败，尝试云端
                if self.cloud.api_key:
                    try:
                        self._use_cloud = True
                        return self.cloud.chat(messages, temperature, timeout, tools, **kwargs)
                    except ConnectionError:
                        pass
                raise ConnectionError(LLMError.OLLAMA_CALL_FAILED.format(error=str(e)))
        else:
            # 本地不可用，检查云端
            if self.cloud.api_key:
                try:
                    self._use_cloud = True
                    return self.cloud.chat(messages, temperature, timeout, tools, **kwargs)
                except ConnectionError as e:
                    raise ConnectionError(LLMError.CLOUD_CALL_FAILED.format(error=str(e)))
            else:
                _, cloud_ok, _ = self._get_error_info()
                if not cloud_ok:
                    raise ConnectionError(LLMError.ALL_UNAVAILABLE)
                raise ConnectionError(LLMError.OLLAMA_NOT_RUNNING)

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict] = None,
        temperature: float = 0.7,
        **kwargs
    ) -> Generator[str, None, None]:
        """
        流式聊天接口 - 双模式自动切换
        """
        # 先尝试本地
        if self.local.check_available():
            try:
                self._use_cloud = False
                yield from self.local.chat_stream(messages, tools, temperature, **kwargs)
                return
            except ConnectionError:
                pass

        # 本地不可用，尝试云端
        if self.cloud.api_key:
            try:
                self._use_cloud = True
                yield from self.cloud.chat_stream(messages, tools, temperature, **kwargs)
                return
            except ConnectionError as e:
                err_msg = str(e)
                # 如果是云端API错误，改为返回包含"API密钥"的错误让前端能识别
                if "API请求失败" in err_msg or "无法连接" in err_msg or "API访问被拒绝" in err_msg:
                    yield f"API密钥无效或API服务不可用，请检查配置：{err_msg}"
                elif "API密钥" in err_msg:
                    yield err_msg
                else:
                    yield LLMError.CLOUD_CALL_FAILED.format(error=err_msg)
                return

        # 两种都不可用
        _, cloud_ok, _ = self._get_error_info()
        if not cloud_ok:
            yield LLMError.ALL_UNAVAILABLE
        else:
            yield LLMError.OLLAMA_NOT_RUNNING

    def generate(self, prompt: str, temperature: float = 0.7, timeout: int = None, **kwargs) -> str:
        """生成接口"""
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, temperature, timeout, **kwargs)

    def extract_json_from_response(self, text: str) -> Optional[Dict]:
        """从文本响应中提取JSON"""
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
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
                openai_tools.append(tool)
        return openai_tools


# ========== 全局单例 ==========
_llm_client = None


def get_llm_client() -> DualModeLLMClient:
    """获取LLM客户端单例（双模式）"""
    global _llm_client
    if _llm_client is None:
        _llm_client = DualModeLLMClient()
    return _llm_client


def reset_llm_client():
    """重置LLM客户端，重新加载配置"""
    global _llm_client
    _llm_client = None
    # 强制重新加载config模块以获取最新配置
    import importlib
    import config as config_module
    importlib.reload(config_module)
    # 同时重置Agent实例，确保使用新的LLM客户端
    import agent.agent as agent_module
    if hasattr(agent_module, '_agent'):
        agent_module._agent = None
