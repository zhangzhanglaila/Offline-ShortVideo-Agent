"""
测试 AI 生图集成到素材检索流程

验证 MaterialFetchAgent 能否使用 Bailian WanX 生成图片
"""
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

from core.agents.material_fetch_agent import MaterialFetchAgent
from core.models import ContentStructure, Scene, Message, SceneMaterialMap


def test_ai_image_integration():
    """测试 AI 生图集成"""
    print("\n" + "=" * 60)
    print("[AI 生图集成测试]")
    print("=" * 60)

    # 检查 API Key
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("[错误] 未配置 DASHSCOPE_API_KEY")
        assert False, "未配置 DASHSCOPE_API_KEY"

    print(f"\n[API Key] {api_key[:10]}...{api_key[-4:]}")

    # 创建简单的测试场景
    test_scene = Scene(
        scene_id=1,
        scene_type="content",
        text="一只可爱的小猫在玩毛线球",
        duration=5.0,
        keywords=["cat", "kitten", "playful"],
    )

    # 创建 MaterialFetchAgent（禁用素材库，只用 AI 生图）
    print("\n[创建 Agent] MaterialFetchAgent (AI 生图模式)")
    agent = MaterialFetchAgent(
        api_manager=False,  # 禁用素材库
        enable_download=True,
    )

    # 测试 AI 生图
    print(f"\n[测试场景]")
    print(f"  场景ID: {test_scene.scene_id}")
    print(f"  文本: {test_scene.text}")
    print(f"  关键词: {test_scene.keywords}")

    assets = agent._fetch_ai_image_for_scene(test_scene, ["cute kitten"])

    assert assets is not None, "AI 生图未返回资产"

    asset = assets  # 直接是单个 MaterialAsset，不是列表
    print(f"\n[成功] AI 生图成功")
    print(f"  资产ID: {asset.asset_id}")
    print(f"  来源: {asset.source}")
    print(f"  本地路径: {asset.local_path}")
    print(f"  URL: {asset.url}")
    print(f"  尺寸: {asset.width}x{asset.height}")

    # 检查文件是否存在
    assert asset.local_path, "资产本地路径为空"
    assert Path(asset.local_path).exists(), f"文件未找到: {asset.local_path}"
    file_size = Path(asset.local_path).stat().st_size / 1024
    print(f"  文件大小: {file_size:.1f} KB")
    print(f"\n[图片已保存] {asset.local_path}")


def test_full_pipeline():
    """测试完整素材检索流程（包含 AI 生图降级）"""
    print("\n" + "=" * 60)
    print("[完整流程测试]")
    print("=" * 60)

    # 创建测试内容结构
    content = ContentStructure(
        title="AI 生图测试",
        category="test",
        style="default",
        total_duration=10,
        scenes=[
            Scene(
                scene_id=1,
                scene_type="content",
                text="一只在海边奔跑的金毛犬",
                duration=5.0,
                keywords=["dog", "golden retriever", "beach", "running"],
            ),
            Scene(
                scene_id=2,
                scene_type="content",
                text="雪山日落的壮丽景色",
                duration=5.0,
                keywords=["mountain", "snow", "sunset", "landscape"],
            ),
        ],
    )

    # 创建 Agent（禁用素材库和视频，只用 AI 生图）
    agent = MaterialFetchAgent(
        api_manager=False,  # 禁用素材库
        prefer_video=False,  # 禁用视频素材
        enable_download=True,
    )

    # 创建消息
    message = Message(
        msg_id="test_ai_image",
        sender="test",
        receiver="material_fetch",
        msg_type="task",
        payload={"content": content.to_dict()},
    )

    print("\n[执行] 素材检索（AI 生图模式）")
    print(f"  场景数: {len(content.scenes)}")

    # 运行 Agent
    result = asyncio.run(agent.execute(message))

    assert result.status == "success", f"素材检索失败: {result.payload.get('error', 'Unknown error')}"

    # 结果在 result 字段，不是 payload
    result_data = result.result or {}
    material_map = SceneMaterialMap.from_dict(result_data)

    print(f"\n[成功] 素材检索完成")
    print(f"  场景素材数: {len(material_map.scene_materials)}")
    print(f"  匹配率: {material_map.match_rate(len(content.scenes)):.1%}")
    print(f"  真实素材: {material_map.real_count}")
    print(f"  占位符: {material_map.placeholder_count}")

    print(f"\n[素材详情]")
    for scene_id in material_map.scene_materials.keys():
        assets = material_map.get(scene_id)
        if assets:
            asset = assets[0]
            print(f"  场景 {scene_id}:")
            print(f"    来源: {asset.source}")
            print(f"    路径: {asset.local_path}")
            print(f"    占位符: {asset.is_placeholder}")

    assert material_map.real_count > 0, "未生成任何真实素材"


def main():
    """运行所有测试"""
    # 设置控制台编码
    import sys
    import io
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    try:
        # 测试1: AI 生图直接调用
        test_ai_image_integration()

        # 测试2: 完整流程
        test_full_pipeline()

        print("\n" + "=" * 60)
        print("[测试结果]")
        print("=" * 60)
        print(f"  AI 生图直接调用: [通过]")
        print(f"  完整流程测试: [通过]")
        print(f"\n[完成] 所有测试通过")

    except Exception as e:
        print(f"\n[异常] {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
