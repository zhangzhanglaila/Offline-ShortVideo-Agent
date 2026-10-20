# -*- coding: utf-8 -*-
"""
独立 MCP Server — STDIO + Streamable HTTP 双传输模式
暴露短视频生成系统的所有能力为 MCP Tools / Resources / Prompts

覆盖面试模块：MCP协议 / Tool暴露 / 跨平台兼容 / 并发治理

启动方式:
  python mcp/server.py                  # STDIO模式（默认）
  python mcp/server.py --transport http --port 9020  # HTTP模式
  python mcp/server.py --transport both --http-port 9020
"""
import json
import sys
import os
import asyncio
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class MCPToolCategory(str, Enum):
    SCRIPT = "script"
    TTS = "tts"
    RENDER = "render"
    TOPIC = "topic"
    UTILITY = "utility"


@dataclass
class MCPToolParam:
    name: str
    type: str
    description: str
    required: bool = False
    enum: Optional[List[str]] = None


@dataclass
class MCPToolDef:
    name: str
    category: MCPToolCategory
    description: str
    parameters: List[MCPToolParam] = field(default_factory=list)

    def to_openai_format(self) -> dict:
        props = {}
        required = []
        for p in self.parameters:
            prop = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            props[p.name] = prop
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": props, "required": required},
            }
        }

    def to_mcp_schema(self) -> dict:
        props = {}
        required = []
        for p in self.parameters:
            prop = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            props[p.name] = prop
            if p.required:
                required.append(p.name)
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": props, "required": required},
        }


# ═══════════════════════════════════════════════════════════════
# Tool Registry — 注册所有暴露的工具
# ═══════════════════════════════════════════════════════════════

def _build_tool_registry() -> Dict[str, MCPToolDef]:
    return {
        # ── 脚本生成 ──
        "generate_script": MCPToolDef(
            name="generate_script",
            category=MCPToolCategory.SCRIPT,
            description="生成短视频口播脚本。输入选题标题和赛道，返回包含黄金3秒钩子、主体内容、CTA、分镜表的完整脚本JSON。",
            parameters=[
                MCPToolParam("topic_title", "string", "选题标题，如'武汉科技大学介绍'", required=True),
                MCPToolParam("category", "string", "赛道分类", required=True, enum=[
                    "知识付费", "美食探店", "生活方式", "情感心理", "科技数码", "娱乐搞笑"
                ]),
                MCPToolParam("duration", "integer", "视频时长(秒)，默认30", required=False),
                MCPToolParam("platform", "string", "目标平台", required=False, enum=["抖音", "小红书", "B站"]),
                MCPToolParam("style", "string", "语言风格", required=False, enum=["爆款", "温和", "专业"]),
                MCPToolParam("use_rag", "boolean", "是否启用RAG知识增强搜索", required=False),
            ],
        ),
        # ── TTS配音 ──
        "generate_tts": MCPToolDef(
            name="generate_tts",
            category=MCPToolCategory.TTS,
            description="将文本转为语音配音(WAV格式)。支持多种中文发音人。",
            parameters=[
                MCPToolParam("text", "string", "要配音的文本", required=True),
                MCPToolParam("voice", "string", "发音人", required=False),
                MCPToolParam("rate", "string", "语速，如'+20%'或'-10%'", required=False),
            ],
        ),
        "list_voices": MCPToolDef(
            name="list_voices",
            category=MCPToolCategory.TTS,
            description="列出所有可用的TTS发音人及其语言和性别属性。",
            parameters=[],
        ),
        # ── 视频渲染 ──
        "render_video": MCPToolDef(
            name="render_video",
            category=MCPToolCategory.RENDER,
            description="触发视频渲染管道：生成图表→漫画帧→动画→多轨道合成→音效混合。支持流式进度推送。",
            parameters=[
                MCPToolParam("video_id", "integer", "视频数据库ID", required=True),
                MCPToolParam("table_name", "string", "数据库表名", required=False, enum=["topic_videos", "ecom_videos"]),
                MCPToolParam("use_graph", "boolean", "是否使用LangGraph Agent管道(支持断点恢复)", required=False),
            ],
        ),
        "get_video_status": MCPToolDef(
            name="get_video_status",
            category=MCPToolCategory.RENDER,
            description="查询视频渲染进度状态。",
            parameters=[
                MCPToolParam("video_id", "integer", "视频数据库ID", required=True),
                MCPToolParam("table_name", "string", "数据库表名", required=False),
            ],
        ),
        # ── 选题管理 ──
        "recommend_topics": MCPToolDef(
            name="recommend_topics",
            category=MCPToolCategory.TOPIC,
            description="根据赛道和关键词推荐热门选题，返回选题列表含标题、钩子、热度分。",
            parameters=[
                MCPToolParam("keyword", "string", "选题关键词", required=True),
                MCPToolParam("category", "string", "赛道分类", required=False),
                MCPToolParam("count", "integer", "推荐数量", required=False),
            ],
        ),
        # ── RAG研究 ──
        "research_topic": MCPToolDef(
            name="research_topic",
            category=MCPToolCategory.UTILITY,
            description="对选题进行互联网研究，检索相关事实和数据，返回可作为脚本素材的背景知识片段。",
            parameters=[
                MCPToolParam("query", "string", "研究查询，如'武汉科技大学 历史 专业特色'", required=True),
                MCPToolParam("category", "string", "赛道分类（辅助搜索精度）", required=False),
                MCPToolParam("top_k", "integer", "返回片段数，默认5", required=False),
            ],
        ),
        # ── 图表 ──
        "generate_chart": MCPToolDef(
            name="generate_chart",
            category=MCPToolCategory.UTILITY,
            description="用Pillow生成数据图表PNG。支持柱状图(bar)、饼图(pie)、折线图(line)、流程图(flowchart)。",
            parameters=[
                MCPToolParam("chart_type", "string", "图表类型", required=True, enum=["bar", "pie", "line", "flowchart"]),
                MCPToolParam("title", "string", "图表标题", required=True),
                MCPToolParam("labels", "array", "数据标签数组", required=True),
                MCPToolParam("values", "array", "数据值数组", required=True),
                MCPToolParam("value_suffix", "string", "数值后缀，如'%'、'亿'", required=False),
                MCPToolParam("visual_style", "string", "视觉风格", required=False, enum=["manga", "minimal", "neon", "magazine", "vibrant"]),
            ],
        ),
    }


TOOL_REGISTRY = _build_tool_registry()


# ═══════════════════════════════════════════════════════════════
# Tool Executor — 实际调用 core 模块执行工具
# ═══════════════════════════════════════════════════════════════

class MCPToolExecutor:
    """执行MCP工具调用，桥接到项目的core模块。"""

    def execute(self, tool_name: str, arguments: dict) -> dict:
        try:
            if tool_name == "generate_script":
                return self._exec_generate_script(arguments)
            elif tool_name == "generate_tts":
                return self._exec_generate_tts(arguments)
            elif tool_name == "list_voices":
                return self._exec_list_voices()
            elif tool_name == "render_video":
                return self._exec_render_video(arguments)
            elif tool_name == "get_video_status":
                return self._exec_get_video_status(arguments)
            elif tool_name == "recommend_topics":
                return self._exec_recommend_topics(arguments)
            elif tool_name == "research_topic":
                return self._exec_research_topic(arguments)
            elif tool_name == "generate_chart":
                return self._exec_generate_chart(arguments)
            else:
                return {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True}
        except Exception as e:
            import traceback
            return {"content": [{"type": "text", "text": f"Tool execution error: {e}\n{traceback.format_exc()}"}], "isError": True}

    def _exec_generate_script(self, args: dict) -> dict:
        from core.script_module import get_script_module
        topic = {
            "title": args["topic_title"],
            "category": args.get("category", "通用"),
            "hook": "",
            "tags": [],
        }
        result = get_script_module().generate_script(
            topic=topic,
            platform=args.get("platform", "抖音"),
            video_duration=int(args.get("duration", 30)),
            style=args.get("style", "爆款"),
            use_rag=args.get("use_rag", True),
        )
        return {"content": [{"type": "text", "text": json.dumps({
            "hook": result.get("hook", ""),
            "body": result.get("body", ""),
            "cta": result.get("cta", ""),
            "full_script": result.get("full_script", ""),
            "storyboard": result.get("storyboard", []),
            "chart_data": result.get("chart_data", []),
        }, ensure_ascii=False, indent=2)}]}

    def _exec_generate_tts(self, args: dict) -> dict:
        from core.tts_module import get_tts_module
        tts = get_tts_module()
        text = args["text"]
        voice = args.get("voice", "zh-CN-XiaoxiaoNeural")
        rate = args.get("rate", "+0%")
        if rate:
            rate_str = str(rate).replace('%', '').replace('+', '')
            try:
                tts.set_rate(int(rate_str))
            except Exception:
                pass
        output_path = str(config.OUTPUT_DIR / "临时" / f"mcp_tts_{int(time.time())}.wav")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        tts.voice = voice
        ok = tts.generate_audio(text, output_path)
        if ok:
            duration = tts.get_audio_duration(output_path)
            return {"content": [{"type": "text", "text": json.dumps({"success": True, "path": output_path, "duration": duration, "voice": voice})}]}
        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": "TTS生成失败"})}], "isError": True}

    def _exec_list_voices(self) -> dict:
        from core.tts_module import get_tts_module
        tts = get_tts_module()
        voices = tts.get_available_voices()
        return {"content": [{"type": "text", "text": json.dumps({"voices": voices, "default": tts.DEFAULT_VOICE}, ensure_ascii=False)}]}

    def _exec_render_video(self, args: dict) -> dict:
        video_id = int(args["video_id"])
        table_name = args.get("table_name", "topic_videos")
        use_graph = args.get("use_graph", False)
        if use_graph:
            from core.langgraph_pipeline import run_graph_pipeline
            result = run_graph_pipeline(video_id, table_name)
        else:
            from core.pipeline_helpers import run_render_pipeline
            result = {"success": True}  # 旧管道无返回值
            run_render_pipeline(video_id, table_name)
        return {"content": [{"type": "text", "text": json.dumps({"started": True, "video_id": video_id, "use_graph": use_graph})}]}

    def _exec_get_video_status(self, args: dict) -> dict:
        video_id = int(args["video_id"])
        table_name = args.get("table_name", "topic_videos")
        import sqlite3
        conn = sqlite3.connect(config.TOPICS_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute(f"SELECT pipeline_step, status, video_path FROM {table_name} WHERE id=?", (video_id,)).fetchone()
        conn.close()
        if row:
            return {"content": [{"type": "text", "text": json.dumps({"step": row["pipeline_step"], "status": row["status"], "video_path": row["video_path"]})}]}
        return {"content": [{"type": "text", "text": json.dumps({"error": "Video not found"})}], "isError": True}

    def _exec_recommend_topics(self, args: dict) -> dict:
        from core.topics_module import get_topics_module
        topics_mod = get_topics_module()
        keyword = args["keyword"]
        category = args.get("category", "")
        count = int(args.get("count", 10))
        topics = topics_mod.recommend_topics(keyword=keyword, category=category, count=count)
        return {"content": [{"type": "text", "text": json.dumps(topics, ensure_ascii=False, indent=2)}]}

    def _exec_research_topic(self, args: dict) -> dict:
        from core.rag_engine import get_rag_engine
        rag = get_rag_engine()
        docs = rag.research_topic(args["query"], args.get("category", ""), int(args.get("top_k", 5)))
        context = rag.format_context(docs)
        return {"content": [{"type": "text", "text": context if context else "未检索到相关资料"}]}

    def _exec_generate_chart(self, args: dict) -> dict:
        from core.chart_renderer import render_chart
        chart_type = args["chart_type"]
        title = args["title"]
        labels = json.loads(args["labels"]) if isinstance(args["labels"], str) else args["labels"]
        values = json.loads(args["values"]) if isinstance(args["values"], str) else args["values"]
        suffix = args.get("value_suffix", "")
        visual_style = args.get("visual_style", "manga")
        spec = {"chart_type": chart_type, "title": title, "labels": labels, "values": values, "value_suffix": suffix}
        output_path = str(config.OUTPUT_DIR / "临时" / f"mcp_chart_{int(time.time())}.png")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        result = render_chart(spec, output_path, visual_style)
        return {"content": [{"type": "text", "text": json.dumps({"success": True, "path": result or output_path})}]}


# ═══════════════════════════════════════════════════════════════
# JSON-RPC 2.0 Protocol Handler
# ═══════════════════════════════════════════════════════════════

class MCPJSONRPCHandler:
    """MCP JSON-RPC 2.0 处理器 — 独立于Agent系统。"""

    def __init__(self, executor: MCPToolExecutor = None):
        self.executor = executor or MCPToolExecutor()

    def handle_request(self, rpc: dict) -> dict:
        method = rpc.get("method", "")
        params = rpc.get("params", {})
        rid = rpc.get("id")

        try:
            if method == "tools/list":
                result = {"tools": [t.to_mcp_schema() for t in TOOL_REGISTRY.values()]}
            elif method == "tools/call":
                result = self.executor.execute(params.get("name", ""), params.get("arguments", {}))
            elif method == "resources/list":
                result = self._list_resources()
            elif method == "resources/read":
                result = self._read_resource(params.get("uri", ""))
            elif method == "prompts/list":
                result = self._list_prompts()
            elif method == "prompts/get":
                result = self._get_prompt(params.get("name", ""))
            elif method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                    "serverInfo": {"name": "ShortVideo-Agent-MCP", "version": "1.0.0"},
                }
            elif method == "ping":
                result = {}
            else:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(e)}}

    def _list_resources(self) -> dict:
        return {"resources": [
            {"uri": "topic://list", "name": "选题列表", "mimeType": "application/json", "description": "获取所有已保存的选题"},
            {"uri": "video://list", "name": "视频列表", "mimeType": "application/json", "description": "获取所有已生成的视频"},
            {"uri": "script://{video_id}", "name": "脚本详情", "mimeType": "application/json", "description": "获取指定视频的脚本和分镜"},
        ]}

    def _read_resource(self, uri: str) -> dict:
        import sqlite3
        conn = sqlite3.connect(config.TOPICS_DB)
        conn.row_factory = sqlite3.Row
        try:
            if uri.startswith("topic://list"):
                rows = conn.execute("SELECT id, category, title, hook, heat_score FROM topics ORDER BY heat_score DESC LIMIT 20").fetchall()
                data = [dict(r) for r in rows]
            elif uri.startswith("video://list"):
                rows = conn.execute("SELECT id, topic_keyword, category, status, pipeline_step, duration FROM topic_videos ORDER BY id DESC LIMIT 20").fetchall()
                data = [dict(r) for r in rows]
            elif uri.startswith("script://"):
                video_id = int(uri.split("//")[1])
                row = conn.execute("SELECT script_content, storyboard, chart_data FROM topic_videos WHERE id=?", (video_id,)).fetchone()
                data = dict(row) if row else {}
            else:
                data = {}
        finally:
            conn.close()
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False)}]}

    def _list_prompts(self) -> dict:
        return {"prompts": [
            {"name": "script-generation", "description": "生成短视频口播脚本的标准提示模板"},
            {"name": "topic-research", "description": "对选题进行深度研究分析的提示模板"},
        ]}

    def _get_prompt(self, name: str) -> dict:
        if name == "script-generation":
            text = "你是顶级短视频口播文案专家。根据选题信息写一段{duration}秒的口播稿，包含hook/body/cta/storyboard，输出JSON格式。"
        elif name == "topic-research":
            text = "请对以下选题进行深度分析：{topic}。从背景、现状、亮点、数据四个维度展开，每个维度2-3个要点。"
        else:
            text = ""
        return {"messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


# ═══════════════════════════════════════════════════════════════
# Transports
# ═══════════════════════════════════════════════════════════════

def run_stdio_server():
    """STDIO传输模式 — 标准MCP通信方式，适合Claude Desktop等客户端。"""
    import sys
    handler = MCPJSONRPCHandler()
    # 去掉启动时的banner输出
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)
            response = handler.handle_request(request)
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            continue
        except KeyboardInterrupt:
            break
        except EOFError:
            break


def create_http_app():
    """创建FastAPI HTTP传输应用 — 用于Streamable HTTP模式。"""
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse, StreamingResponse
    except ImportError:
        raise ImportError("HTTP模式需要 fastapi 和 uvicorn")

    app = FastAPI(title="ShortVideo-Agent MCP Server", version="1.0.0")
    handler = MCPJSONRPCHandler()

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        body = await request.json()
        # 支持批量请求（JSON-RPC 2.0 batch）
        if isinstance(body, list):
            responses = [handler.handle_request(r) for r in body]
        else:
            responses = handler.handle_request(body)
        return JSONResponse(responses)

    @app.get("/mcp/sse")
    async def mcp_sse(request: Request):
        """SSE流式端点 — 用于服务器推送通知。"""
        async def event_stream():
            yield f"data: {json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'})}\n\n"
            # 保持连接开放，等待服务端事件
            while True:
                await asyncio.sleep(30)
                yield f"data: {json.dumps({'jsonrpc': '2.0', 'method': 'ping'})}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/health")
    async def health():
        return {"status": "ok", "tools_count": len(TOOL_REGISTRY)}

    return app


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ShortVideo-Agent MCP Server")
    parser.add_argument("--transport", choices=["stdio", "http", "both"], default="stdio",
                        help="传输模式 (default: stdio)")
    parser.add_argument("--port", type=int, default=9020, help="HTTP端口 (default: 9020)")
    args = parser.parse_args()

    print(f"[MCP Server] Starting in {args.transport} mode...", file=sys.stderr)

    if args.transport == "stdio":
        print("[MCP Server] Listening on STDIO. Ready for MCP client connections.", file=sys.stderr)
        run_stdio_server()
    elif args.transport == "http":
        import uvicorn
        app = create_http_app()
        print(f"[MCP Server] Listening on http://localhost:{args.port}", file=sys.stderr)
        uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")
    elif args.transport == "both":
        import threading
        import uvicorn
        # HTTP in background thread, STDIO in main
        app = create_http_app()
        t = threading.Thread(target=uvicorn.run, args=(app,), kwargs={"host": "0.0.0.0", "port": args.port, "log_level": "info"}, daemon=True)
        t.start()
        print(f"[MCP Server] HTTP on http://localhost:{args.port}", file=sys.stderr)
        run_stdio_server()
