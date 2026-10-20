"""
测试阿里云百炼 AI 生图（OpenAI 兼容接口）

使用 .env 中配置的 DASHSCOPE_API_KEY 进行真实生图测试
"""

import pytest
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 加载 .env 文件
env_path = Path(__file__).resolve().parents[2] / ".env"
if env_path.exists():
    print(f"[加载配置] {env_path}")
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()
else:
    print(f"[警告] .env 文件不存在: {env_path}")

from services.ai_image.bailian_generator import BailianImageGenerator
from services.ai_image.base import ImageSize, AIImageRequest, AIProvider


async def test_real_generation():
    """测试真实图片生成"""
    print("\n" + "=" * 60)
    print("[阿里云百炼] 真实生图测试 (OpenAI兼容接口)")
    print("=" * 60)

    # 读取 API key
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("[错误] 未配置 DASHSCOPE_API_KEY")
        return

    print(f"\n[API Key] {api_key[:10]}...{api_key[-4:]}")

    # 创建生成器
    generator = BailianImageGenerator(api_key=api_key)

    # 显示支持的模型
    models = generator.get_supported_models()
    print(f"\n[支持模型] {models}")

    # 显示基础 URL
    print(f"[API 地址] {generator.base_url}")

    # 创建请求（简单提示词）
    request = AIImageRequest(
        prompt="一只可爱的小猫",
        provider="bailian",
        model="wan2.6-t2i",  # 使用正确的 WanX 模型
        size=ImageSize.SQUARE_1024,
    )

    # 估算成本
    cost = generator.estimate_cost(request)
    print(f"\n[预估成本] {cost:.2f} CNY")

    # 生成图片
    print(f"\n[开始生成]")
    print(f"  提示词: {request.prompt}")
    print(f"  模型: {request.model}")
    print(f"  尺寸: {request.size.value}")
    print(f"\n[提示] 百炼生图可能需要 20-40 秒...")

    result = await generator.generate(request)

    print("\n" + "=" * 60)
    print("[生成结果]")
    print("=" * 60)

    if result.success:
        print(f"\n[成功]")
        print(f"  图片路径: {result.image_path}")
        print(f"  图片 URL: {result.image_url}")
        print(f"  生成耗时: {result.generation_time:.2f}s")
        print(f"  实际成本: {result.cost:.2f} CNY")

        # 检查文件是否存在
        if result.image_path and Path(result.image_path).exists():
            file_size = Path(result.image_path).stat().st_size / 1024
            print(f"  文件大小: {file_size:.1f} KB")
            print(f"\n[图片已保存] {result.image_path}")
        else:
            print(f"\n[警告] 文件未找到: {result.image_path}")

        return result.image_path
    else:
        print(f"\n[失败]")
        print(f"  错误信息: {result.error}")
        return None


async def main():
    try:
        image_path = await test_real_generation()

        if image_path:
            print(f"\n[完成] 图片已生成: {image_path}")
        else:
            print(f"\n[未完成] 图片生成失败")

    except Exception as e:
        print(f"\n[异常] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())

pytestmark = pytest.mark.integration
