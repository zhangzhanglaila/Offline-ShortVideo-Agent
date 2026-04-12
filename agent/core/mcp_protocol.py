# -*- coding: utf-8 -*-
"""
MCP (Model Context Protocol) 协议实现
"""
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum


class MCPError(Exception):
    """MCP协议错误"""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class MCPMethod(Enum):
    """MCP方法"""
    # 工具相关
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"

    # 资源相关
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    RESOURCES_SUBSCRIBE = "resources/subscribe"

    # 提示相关
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"

    # 采样
    SAMPLING_CREATE = "sampling/create"

    # 根目录
    ROOTS_LIST = "roots/list"

    # 追踪
    TRACE = "trace/event"


class MCPProtocol:
    """
    MCP (Model Context Protocol) 协议实现

    支持标准MCP方法：
    - tools/list: 列出可用工具
    - tools/call: 调用工具
    - resources/list: 列出资源
    - resources/read: 读取资源
    """

    def __init__(self, agent):
        self.agent = agent
        self._tools = {t.definition.name: t for t in agent.tools}

    def handle_request(self, method: str, params: Dict = None) -> Dict:
        """
        处理MCP JSON-RPC 2.0请求

        Args:
            method: MCP方法名
            params: 参数对象

        Returns:
            JSON-RPC 2.0响应
        """
        try:
            # 解析方法
            if method == MCPMethod.TOOLS_LIST.value:
                result = self.tools_list()
            elif method == MCPMethod.TOOLS_CALL.value:
                result = self.tools_call(
                    params.get("name"),
                    params.get("arguments", {})
                )
            elif method == MCPMethod.RESOURCES_LIST.value:
                result = self.resources_list()
            elif method == MCPMethod.RESOURCES_READ.value:
                result = self.resources_read(params.get("uri"))
            else:
                raise MCPError(-32601, f"Method not found: {method}")

            return {
                "jsonrpc": "2.0",
                "id": params.get("_id") or 1,
                "result": result
            }

        except MCPError as e:
            return {
                "jsonrpc": "2.0",
                "id": params.get("_id") or 1,
                "error": {
                    "code": e.code,
                    "message": e.message
                }
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": params.get("_id") or 1,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}"
                }
            }

    def handle_notification(self, method: str, params: Dict = None) -> None:
        """处理MCP通知（无响应）"""
        if method == MCPMethod.TRACE.value:
            self.trace_event(params)
        elif method.startswith("notifications/"):
            pass  # 忽略其他通知

    # ========== 工具方法 ==========

    def tools_list(self) -> Dict:
        """列出所有可用工具（MCP格式）"""
        tools = []
        for tool in self._tools.values():
            tools.append({
                "name": tool.definition.name,
                "description": tool.definition.description,
                "inputSchema": self._tool_to_schema(tool)
            })
        return {"tools": tools}

    def tools_call(self, name: str, arguments: Dict) -> Dict:
        """调用工具"""
        if name not in self._tools:
            raise MCPError(-32602, f"Tool not found: {name}")

        tool = self._tools[name]
        result = tool.execute(**arguments)

        if result.success:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result.result, ensure_ascii=False, indent=2)
                    }
                ],
                "isError": False
            }
        else:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: {result.error}"
                    }
                ],
                "isError": True
            }

    def _tool_to_schema(self, tool) -> Dict:
        """将工具转换为MCP input schema"""
        properties = {}
        required = []

        for p in tool.definition.parameters:
            properties[p.name] = {
                "type": p.type,
                "description": p.description
            }
            if p.required:
                required.append(p.name)
            if p.enum_values:
                properties[p.name]["enum"] = p.enum_values

        return {
            "type": "object",
            "properties": properties,
            "required": required
        }

    # ========== 资源方法 ==========

    def resources_list(self) -> Dict:
        """列出所有资源"""
        resources = []

        # 素材资源
        resources.append({
            "uri": "agent://materials/images",
            "name": "素材池图片",
            "description": "素材池中的所有图片",
            "mimeType": "application/json"
        })

        resources.append({
            "uri": "agent://materials/videos",
            "name": "素材池视频",
            "description": "素材池中的所有视频",
            "mimeType": "application/json"
        })

        # 选题资源
        resources.append({
            "uri": "agent://topics/hot",
            "name": "热门选题",
            "description": "当前热门选题列表",
            "mimeType": "application/json"
        })

        # 任务资源
        resources.append({
            "uri": "agent://tasks/status",
            "name": "任务状态",
            "description": "当前异步任务状态",
            "mimeType": "application/json"
        })

        return {"resources": resources}

    def resources_read(self, uri: str) -> Dict:
        """读取资源"""
        if uri == "agent://materials/images":
            from agent.tools.material_tool import MaterialReadingTool
            tool = MaterialReadingTool()
            result = tool.execute(material_type="image", limit=20)
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(result.result, ensure_ascii=False)
                }]
            }

        elif uri == "agent://topics/hot":
            from agent.tools.topic_tool import TopicRecommendTool
            tool = TopicRecommendTool()
            result = tool.execute(count=10)
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(result.result, ensure_ascii=False)
                }]
            }

        elif uri == "agent://tasks/status":
            from agent.core.task_queue import get_task_queue
            queue = get_task_queue()
            tasks = queue.list_tasks()
            return {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(tasks, ensure_ascii=False)
                }]
            }

        else:
            raise MCPError(-32602, f"Resource not found: {uri}")

    def resources_subscribe(self, uri: str) -> None:
        """订阅资源变化（暂不支持）"""
        pass

    # ========== 追踪方法 ==========

    def trace_event(self, params: Dict) -> None:
        """记录追踪事件"""
        # 可用于调试和监控
        pass


def create_mcp_handler(agent) -> MCPProtocol:
    """创建MCP协议处理器"""
    return MCPProtocol(agent)
