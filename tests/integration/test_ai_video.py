"""
测试 AI 生视频服务

验证 DashScope WanX 视频生成器的基本功能
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

from services.ai_video import (
    DashScopeVideoGenerator,
    VideoGenerationRequest,
    VideoProvider,
    VideoSize,
)


def test_video_generator_basic():
    """测试视频生成器基础功能"""
    print("\n" + "=" * 60)
    print("[视频生成器基础测试]")
    print("=" * 60)

    # 检查 API Key
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("[错误] 未配置 DASHSCOPE_API_KEY")
        assert False, "未配置 DASHSCOPE_API_KEY"

    print(f"\n[API Key] {api_key[:10]}...{api_key[-4:]}")

    # 创建生成器
    generator = DashScopeVideoGenerator(api_key=api_key)

    print("\n[支持的模型]")
    for model in generator.get_supported_models():
        config = generator.MODELS.get(model, {})
        print(f"  - {model}: {config.get('display_name', model)}")

    print("\n[成本估算]")
    test_request = VideoGenerationRequest(
        prompt="一只猫在玩毛线球",
        provider=VideoProvider.DASHSCOPE,
        model="wan2.7-t2v",
        size=VideoSize.PORTRAIT_9_16,
        duration=5,
    )
    cost = generator.estimate_cost(test_request)
    print(f"  5秒视频成本: {cost} 元")

    print("\n[完成] 基础测试通过")


def test_video_models_capability():
    """测试各模型能力配置"""
    print("\n" + "=" * 60)
    print("[模型能力配置测试]")
    print("=" * 60)

    generator = DashScopeVideoGenerator(api_key="dummy_key")

    print("\n[模型能力矩阵]")
    for model_id, config in generator.MODELS.items():
        print(f"\n  {model_id}:")
        print(f"    名称: {config.get('display_name')}")
        print(f"    类型: {config.get('type')}")
        print(f"    时长: {config.get('duration_range')} 秒")
        print(f"    分辨率: {config.get('resolutions')}")
        print(f"    画幅: {config.get('ratios')}")
        print(f"    帧率: {config.get('fps')} fps")

    print("\n[完成] 能力配置测试通过")


def main():
    """运行所有测试"""
    import sys
    import io
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    try:
        # 测试1: 基础功能
        test_video_generator_basic()

        # 测试2: 模型能力
        test_video_models_capability()

        print("\n" + "=" * 60)
        print("[测试结果]")
        print("=" * 60)
        print(f"  基础功能: [通过]")
        print(f"  模型能力: [通过]")
        print(f"\n[完成] 所有测试通过")

    except Exception as e:
        print(f"\n[异常] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

pytestmark = pytest.mark.integration
