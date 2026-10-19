"""端到端集成测试：用真实模板渲染"""
import asyncio
from pathlib import Path

from services.template.renderer import TemplateRenderer


async def _render_image_default_template():
    """测试用真实的 image_default 模板渲染（内部协程，由 test_render_e2e 驱动）。"""
    r = TemplateRenderer()

    # 输出路径
    output = Path("output/test_image_default_rendered.png")
    output.parent.mkdir(parents=True, exist_ok=True)

    context = {
        "title": "测试标题",
        "image": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='800'%3E%3Crect width='800' height='800' fill='%234A90E2'/%3E%3C/svg%3E",
        "text": "这是一段测试文本",
        "author": "测试作者",
        "describe": "测试描述",
        "brand": "测试品牌",
    }

    result = await r.render("image_default", context, output)

    assert result.exists()
    assert result.stat().st_size > 1000  # 至少 1KB

    # 关闭浏览器
    await r.close()


def test_render_e2e():
    """端到端渲染（真实浏览器）。

    原文件里还有一个同名 async 测试，但它没有 pytest-asyncio 标记、
    又被这里用 asyncio.run 驱动，导致重复收集 + "async def not natively supported"。
    统一收敛为单个同步用例，协程改名 _render_image_default_template。
    """
    asyncio.run(_render_image_default_template())