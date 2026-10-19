# -*- coding: utf-8 -*-
"""LLM → 单文件 HTML（内联 CSS/SVG，纯 @keyframes 动画）。"""
from __future__ import annotations

import re
from typing import Optional

from .prompts import build_html_prompt

MAX_HTML_CHARS = 200_000

_FENCE = re.compile(r"```(?:html)?\s*(.*?)```", re.S | re.I)
_DOC = re.compile(r"<!DOCTYPE html.*?</html>", re.S | re.I)
_HTML_TAG = re.compile(r"<html\b.*?</html>", re.S | re.I)


class HtmlGenerationError(RuntimeError):
    """模型没有产出可用的 HTML。"""


# ---------- 内置示例（不调用任何 API） ----------

DEMO_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; width: 1080px; height: 1920px; overflow: hidden; }
  body {
    background: radial-gradient(circle at 50% 30%, #1b2450 0%, #0a0e1f 60%, #05060d 100%);
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif;
    color: #f4f7ff;
  }
  .wrap { position: relative; width: 100%; height: 100%; }
  .glow {
    position: absolute; width: 720px; height: 720px; left: -60px; top: -160px;
    background: radial-gradient(circle, rgba(90,140,255,.45), rgba(90,140,255,0) 70%);
    filter: blur(10px);
    animation: glowMove 5s ease-in-out both;
  }
  @keyframes glowMove {
    from { transform: translate(0, 0) scale(.9); opacity: .2; }
    40%  { opacity: .9; }
    to   { transform: translate(420px, 260px) scale(1.2); opacity: .55; }
  }
  @keyframes fadeUp {
    from { opacity: 0; transform: translateY(40px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
  @keyframes draw { to { stroke-dashoffset: 260; } }

  .badge {
    position: absolute; top: 210px; left: 110px; font-size: 44px; letter-spacing: 8px;
    color: #7fb0ff; opacity: 0; animation: fadeUp .8s ease-out .2s both;
  }
  .title {
    position: absolute; top: 300px; left: 110px; right: 110px;
    font-size: 120px; line-height: 1.15; font-weight: 800;
    opacity: 0; animation: fadeUp 1s ease-out .6s both;
  }
  .title em { font-style: normal; color: #6ea8ff; }

  .ring {
    position: absolute; top: 760px; left: 50%; transform: translateX(-50%);
    width: 520px; height: 520px; opacity: 0;
    animation: fadeIn .6s ease-out 1.5s both;
  }
  .ring circle {
    fill: none; stroke: #6ea8ff; stroke-width: 6; stroke-linecap: round;
    stroke-dasharray: 1194; stroke-dashoffset: 1194;
    animation: draw 2s ease-in-out 1.8s both;
  }
  .ring text { fill: #f4f7ff; font-size: 64px; font-weight: 700; text-anchor: middle; }
  .ring .sub { fill: #9fb6dd; font-size: 34px; font-weight: 400; }

  .bullets { position: absolute; top: 1380px; left: 110px; right: 110px; }
  .bullet {
    display: flex; align-items: center; gap: 22px; margin-bottom: 34px;
    font-size: 52px; color: #dbe6ff; opacity: 0;
  }
  .bullet .dot { width: 18px; height: 18px; border-radius: 50%; background: #6ea8ff; flex: none; }
  .b1 { animation: fadeUp .7s ease-out 2.6s both; }
  .b2 { animation: fadeUp .7s ease-out 3.2s both; }
  .b3 { animation: fadeUp .7s ease-out 3.8s both; }

  .footer {
    position: absolute; bottom: 120px; left: 110px; font-size: 38px;
    color: #7f90b3; letter-spacing: 2px; opacity: 0;
    animation: fadeUp .7s ease-out 4.3s both;
  }
</style>
</head>
<body>
  <div class="wrap">
    <div class="glow"></div>
    <div class="badge">AI 动效演示</div>
    <div class="title">零图像 API<br>也能做出<em>动效</em></div>

    <svg class="ring" viewBox="0 0 520 520">
      <circle cx="260" cy="260" r="190"></circle>
      <text x="260" y="250">HTML</text>
      <text class="sub" x="260" y="310">+ CSS + SVG</text>
    </svg>

    <div class="bullets">
      <div class="bullet b1"><span class="dot"></span>一次文本补全生成页面</div>
      <div class="bullet b2"><span class="dot"></span>本地浏览器逐帧截图</div>
      <div class="bullet b3"><span class="dot"></span>ffmpeg 合成 MP4</div>
    </div>

    <div class="footer">0 元图像成本 · 全程本地渲染</div>
  </div>
</body>
</html>
"""


# ---------- 解析与清洗 ----------

def extract_html(text: str) -> str:
    """从模型输出里取出 HTML 文档。"""
    if not text:
        raise HtmlGenerationError("模型返回为空")

    match = _FENCE.search(text)
    body = match.group(1) if match else text

    doc = _DOC.search(body) or _HTML_TAG.search(body)
    if doc:
        return doc.group(0).strip()

    if "<body" in body.lower() and "</body>" in body.lower():
        return body.strip()

    raise HtmlGenerationError("模型没有返回完整的 HTML 文档")


def sanitize_html(html: str) -> str:
    """去掉脚本与外部资源，只保留可静态渲染的部分。

    这是纵深防御：提示词已经禁止这些内容，但模型不总是听话。
    """
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<script\b[^>]*/?>", "", html, flags=re.I)
    html = re.sub(r"<iframe\b[^>]*>.*?</iframe>", "", html, flags=re.S | re.I)
    html = re.sub(r"<(iframe|object|embed)\b[^>]*/?>", "", html, flags=re.I)
    html = re.sub(r"<link\b[^>]*>", "", html, flags=re.I)
    html = re.sub(r"@import\s+[^;]+;", "", html, flags=re.I)
    # 内联事件处理器（onclick 等）
    html = re.sub(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*')", "", html, flags=re.I)
    return html.strip()[:MAX_HTML_CHARS]


def generate_html(
    topic: str,
    style: str = "minimal",
    duration: float = 5,
    width: int = 1080,
    height: int = 1920,
    llm=None,
) -> dict:
    """调用文本 LLM 生成 HTML 动效页。

    Returns:
        {"html": str, "has_animation": bool, "raw_preview": str}
    """
    prompt = build_html_prompt(topic, style, duration, width, height)

    if llm is None:
        from agent.llm.ollama_client import get_llm_client
        llm = get_llm_client()

    try:
        # DualModeLLMClient 只提供 chat()；本地 OllamaClient / 测试桩可能是 generate()
        if hasattr(llm, "chat"):
            raw = llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.8,
                timeout=180,
            )
        else:
            raw = llm.generate(prompt, temperature=0.8, timeout=180)
    except Exception as exc:
        raise HtmlGenerationError(f"大模型调用失败: {exc}")

    html = sanitize_html(extract_html(raw))
    if "<style" not in html.lower():
        raise HtmlGenerationError("生成的 HTML 没有内联样式，无法做出动效")

    return {
        "html": html,
        "has_animation": "@keyframes" in html,
        "raw_preview": str(raw)[:2000],
    }
