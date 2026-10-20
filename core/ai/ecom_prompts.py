# -*- coding: utf-8 -*-
"""
电商专用 Prompt 模板库
"""
import json


ECOM_STYLES = {
    "hard_sell": "强促销风格：限时优惠+价格锚点+紧迫感CTA",
    "soft_sell": "软种草风格：场景代入+使用体验+自然推荐",
    "unboxing": "开箱风格：拆箱惊喜+细节展示+真实感受",
    "tutorial": "教程风格：痛点引入+解决方案+产品展示",
}


def build_script_prompt(product: dict, style: str, platform: str, duration: int = 30) -> str:
    """构建带货脚本生成 prompt。"""
    name = product.get('name', '')
    price = product.get('price', 0)
    currency = product.get('currency', 'USD')
    description = product.get('description', '')
    selling_points = product.get('selling_points', [])
    if isinstance(selling_points, str):
        try:
            selling_points = json.loads(selling_points)
        except json.JSONDecodeError:
            selling_points = [selling_points]

    style_desc = ECOM_STYLES.get(style, style)
    sp_lines = '\n'.join(f"  - {sp}" for sp in selling_points[:8]) if selling_points else f"  - {name}"

    return f"""你是一位顶级电商带货短视频文案专家。请为以下商品生成一段{duration}秒的带货口播脚本。

【商品信息】
- 商品名称: {name}
- 价格: {currency} {price}
- 商品描述: {description}
- 核心卖点:
{sp_lines}

【风格要求】
{style_desc}

【平台】
{platform}

【脚本要求】
1. 黄金3秒开头：抓住观众注意力
2. 中间部分：逐个展示核心卖点，每个卖点配一个使用场景
3. 结尾CTA：引导购买、关注
4. 口语化、有感染力、节奏紧凑
5. 总字数控制在{duration * 3}字以内

【输出格式 - JSON】
{{
  "hook": "黄金3秒开头文案",
  "body": "主体内容",
  "cta": "行动号召",
  "full_script": "完整口播文案",
  "storyboard": [
    {{"time": "0-3秒", "scene": "画面描述", "subtitle": "字幕", "duration": 3}}
  ]
}}

直接输出JSON:"""


def build_insight_prompt(analytics_data: list, product_name: str = '') -> str:
    """构建数据分析洞察 prompt。"""
    data_lines = []
    for item in analytics_data[:10]:
        data_lines.append(
            f"- 视频#{item.get('video_id', '?')}: 展示{item.get('impressions', 0)}, "
            f"点击{item.get('clicks', 0)}, CTR{item.get('ctr', 0):.1%}, "
            f"转化{item.get('conversions', 0)}, 完播率{item.get('completion_rate', 0):.0%}"
        )
    data_text = '\n'.join(data_lines) if data_lines else '暂无数据'

    return f"""你是一位电商短视频数据分析师。请根据以下数据给出优化建议。

【商品】{product_name or '未知商品'}

【视频表现数据】
{data_text}

请从以下维度分析并给出3-5条可执行的优化建议：
1. 标题/开头hook优化
2. 视频节奏/时长调整
3. 卖点展示顺序
4. CTA话术优化
5. 发布时间/平台策略

输出格式：直接给出建议，每条建议包含【问题】和【优化方案】。"""


def build_platform_content_prompt(script: str, product_name: str, platform: str) -> str:
    """为指定平台生成适配的标题/描述/标签。"""
    return f"""基于以下带货视频脚本，为{platform}平台生成发布内容：

【脚本】
{script}

【商品】{product_name}

请生成:
1. 吸引人的标题(30字以内)
2. 引导互动的描述(200字以内)
3. 10个相关话题标签

输出JSON:
{{
  "title": "标题",
  "description": "描述",
  "hashtags": ["#标签1", "#标签2"]
}}

直接输出JSON:"""
