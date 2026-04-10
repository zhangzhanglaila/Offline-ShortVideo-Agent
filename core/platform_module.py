# -*- coding: utf-8 -*-
"""
多平台适配模块
自动生成抖音/小红书/视频号标题+简介+话题标签
自动裁剪对应时长，分文件夹导出发布包
"""
import os
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from config import PLATFORM_CONFIGS, OUTPUT_DIR, TRENDING_TAGS

class PlatformModule:
    """多平台适配模块"""

    def __init__(self):
        """初始化平台模块"""
        self.platforms = list(PLATFORM_CONFIGS.keys())
        self.platform_configs = PLATFORM_CONFIGS

    def adapt_content(self, script_result: Dict, target_platform: str) -> Dict:
        """
        为目标平台适配内容

        参数:
            script_result: 脚本生成结果
            target_platform: 目标平台 (抖音/小红书/视频号)

        返回:
            适配后的内容字典
        """
        config = self.platform_configs.get(target_platform)
        if not config:
            raise ValueError(f"不支持的平台: {target_platform}")

        # 提取原始数据
        title = script_result.get("topic_title", "")
        hook = script_result.get("hook", "")
        body = script_result.get("body", "")
        cta = script_result.get("cta", "")
        category = script_result.get("category", "")
        original_tags = script_result.get("suggested_tags", [])

        # 根据平台特性调整内容
        if target_platform == "抖音":
            adapted = self._adapt_for_douyin(title, hook, body, cta, category, original_tags, config)
        elif target_platform == "小红书":
            adapted = self._adapt_for_xiaohongshu(title, hook, body, cta, category, original_tags, config)
        elif target_platform == "视频号":
            adapted = self._adapt_for_videoaccount(title, hook, body, cta, category, original_tags, config)
        else:
            adapted = self._create_default_adaptation(title, hook, body, cta, original_tags, config)

        # 添加元数据
        adapted["platform"] = target_platform
        adapted["adapted_at"] = datetime.now().isoformat()
        adapted["video_settings"] = {
            "aspect_ratio": config["aspect_ratio"],
            "max_duration": config["max_duration"],
        }

        return adapted

    def _adapt_for_douyin(self, title: str, hook: str, body: str, cta: str,
                          category: str, tags: List[str], config: Dict) -> Dict:
        """抖音适配 - 悬念型、爆款风格"""
        # 标题: 悬念/震惊型
        dy_title = self._generate_douyin_title(title, hook)

        # 描述: 引导互动
        dy_desc = self._generate_douyin_description(hook, body, cta)

        # 话题: 热门挑战+垂直标签
        dy_hashtags = self._generate_douyin_hashtags(category, tags)

        # 时长控制
        duration_note = f"建议时长: {config['min_duration']}-{config['max_duration']}秒"

        return {
            "platform_title": dy_title,
            "platform_description": dy_desc,
            "platform_hashtags": dy_hashtags,
            "duration_note": duration_note,
            "tips": [
                "视频前3秒必须有爆点/悬念",
                "在第5秒左右出现第一个转折",
                "结尾引导评论互动",
                "配文引导: '评论区见'/'说说你的看法'",
            ]
        }

    def _generate_douyin_title(self, title: str, hook: str) -> str:
        """生成抖音风格标题"""
        templates = [
            f"必看！{title}",
            f"{hook}！{title[:15]}",
            f"绝了！{title}",
            f"救命！{title}",
            f"学会了！{title}",
            f"曝光！{title}",
            f"揭秘！{title}",
        ]
        # 随机选择一个模板
        import random
        template = random.choice(templates)
        # 确保不超过长度限制
        if len(template) > 40:
            template = template[:37] + "..."
        return template

    def _generate_douyin_description(self, hook: str, body: str, cta: str) -> str:
        """生成抖音风格描述"""
        parts = []

        # 开头爆点
        if hook:
            parts.append(hook)

        # 主体内容摘要
        if body:
            # 取前100字
            body_summary = body[:100] if len(body) > 100 else body
            parts.append(body_summary)

        # CTA
        if cta:
            parts.append("")
            parts.append(cta)

        # 固定引导
        parts.append("")
        parts.append("关注我，更多干货持续更新~")

        return "\n".join(parts)

    def _generate_douyin_hashtags(self, category: str, tags: List[str]) -> List[str]:
        """生成抖音话题标签"""
        hashtags = []

        # 添加爆款通用标签
        hashtags.extend(TRENDING_TAGS[:5])

        # 添加赛道标签
        if category:
            category_tags = {
                "知识付费": ["#知识分享", "#干货", "#自我提升", "#成长"],
                "美食探店": ["#美食", "#探店", "#吃货", "#美食推荐"],
                "生活方式": ["#生活", "#日常", "#vlog", "#记录生活"],
                "情感心理": ["#情感", "#心理", "#治愈", "#共鸣"],
                "科技数码": ["#科技", "#数码", "#测评", "#好物推荐"],
                "娱乐搞笑": ["#搞笑", "#娱乐", "#段子", "#解压"],
            }
            hashtags.extend(category_tags.get(category, ["#分享"])[:3])

        # 添加原始标签
        for tag in tags[:5]:
            clean_tag = tag.strip().replace(",", "").replace(" ", "")
            if clean_tag:
                if not clean_tag.startswith("#"):
                    clean_tag = "#" + clean_tag
                hashtags.append(clean_tag)

        # 去重并限制数量
        seen = set()
        unique_hashtags = []
        for h in hashtags:
            if h not in seen:
                seen.add(h)
                unique_hashtags.append(h)
                if len(unique_hashtags) >= 20:
                    break

        return unique_hashtags

    def _adapt_for_xiaohongshu(self, title: str, hook: str, body: str, cta: str,
                               category: str, tags: List[str], config: Dict) -> Dict:
        """小红书适配 - 种草分享型"""
        # 标题: 种草分享型
        xhs_title = self._generate_xiaohongshu_title(title, hook)

        # 描述: 经验分享型
        xhs_desc = self._generate_xiaohongshu_description(title, hook, body)

        # 话题: 生活方式型
        xhs_hashtags = self._generate_xiaohongshu_hashtags(category, tags)

        return {
            "platform_title": xhs_title,
            "platform_description": xhs_desc,
            "platform_hashtags": xhs_hashtags,
            "duration_note": f"建议时长: {config['min_duration']}-{config['max_duration']}秒",
            "tips": [
                "封面图要精致美观",
                "标题多用数字和emoji",
                "内容要有实用价值",
                "结尾引导收藏关注",
            ]
        }

    def _generate_xiaohongshu_title(self, title: str, hook: str) -> str:
        """生成小红书风格标题"""
        emojis = ["", "", "", "", ""]
        templates = [
            f"{emojis[0]}保姆级|{title}",
            f"{emojis[1]}建议收藏！{title}",
            f"{emojis[2]}吐血整理|{title}",
            f"{emojis[3]}真的绝了！{title}",
            f"{emojis[4]}{title}攻略",
        ]
        import random
        template = random.choice(templates)
        if len(template) > 20:
            template = template[:17] + "..."
        return template

    def _generate_xiaohongshu_description(self, title: str, hook: str, body: str) -> str:
        """生成小红书风格描述"""
        parts = []

        parts.append(f"今天给大家分享{title}")
        parts.append("")
        if hook:
            parts.append(hook)
        parts.append("")
        if body:
            parts.append(body[:300])
        parts.append("")
        parts.append("—————")
        parts.append("喜欢的话记得收藏关注哦")
        parts.append("更多干货，点击主页")

        return "\n".join(parts)

    def _generate_xiaohongshu_hashtags(self, category: str, tags: List[str]) -> List[str]:
        """生成小红书话题标签"""
        hashtags = ["#小红书", "#笔记灵感", "#种草"]

        category_tags = {
            "知识付费": ["#知识博主", "#学习", "#干货分享"],
            "美食探店": ["#美食博主", "#吃货日记", "#探店打卡"],
            "生活方式": ["#生活博主", "#日常碎片", "#vlog日常"],
            "情感心理": ["#情感博主", "#女性成长", "#治愈系"],
            "科技数码": ["#数码博主", "#科技改变生活", "#测评"],
            "娱乐搞笑": ["#搞笑博主", "#沙雕日常", "#放松一下"],
        }
        hashtags.extend(category_tags.get(category, ["#分享"])[:3])

        for tag in tags[:5]:
            clean_tag = tag.strip().replace(",", "").replace(" ", "")
            if clean_tag:
                if not clean_tag.startswith("#"):
                    clean_tag = "#" + clean_tag
                hashtags.append(clean_tag)

        # 去重
        seen = set()
        unique = []
        for h in hashtags:
            if h not in seen:
                seen.add(h)
                unique.append(h)
                if len(unique) >= 15:
                    break

        return unique

    def _adapt_for_videoaccount(self, title: str, hook: str, body: str, cta: str,
                                 category: str, tags: List[str], config: Dict) -> Dict:
        """视频号适配 - 新闻资讯型"""
        # 标题: 新闻资讯型
        wx_title = self._generate_videoaccount_title(title, hook)

        # 描述: 朋友圈风格
        wx_desc = self._generate_videoaccount_description(hook, body)

        # 话题: 正能量型
        wx_hashtags = self._generate_videoaccount_hashtags(category, tags)

        return {
            "platform_title": wx_title,
            "platform_description": wx_desc,
            "platform_hashtags": wx_hashtags,
            "duration_note": f"建议时长: {config['min_duration']}-{config['max_duration']}秒",
            "tips": [
                "内容积极正面",
                "适合中老年群体",
                "避免敏感话题",
                "结尾引导转发朋友圈",
            ]
        }

    def _generate_videoaccount_title(self, title: str, hook: str) -> str:
        """生成视频号风格标题"""
        templates = [
            f"【今日头条】{title}",
            f"{title}（建议转发）",
            f"速看！{title}",
            f"朋友圈都在传！{title}",
        ]
        import random
        template = random.choice(templates)
        if len(template) > 30:
            template = template[:27] + "..."
        return template

    def _generate_videoaccount_description(self, hook: str, body: str) -> str:
        """生成视频号风格描述"""
        parts = []

        if hook:
            parts.append(hook)
        parts.append("")
        if body:
            parts.append(body[:200])
        parts.append("")
        parts.append("看完觉得有收获的，转发给朋友们看看！")

        return "\n".join(parts)

    def _generate_videoaccount_hashtags(self, category: str, tags: List[str]) -> List[str]:
        """生成视频号话题标签"""
        hashtags = ["#正能量", "#生活记录", "#每日分享"]

        category_tags = {
            "知识付费": ["#知识分享", "#终身学习"],
            "美食探店": ["#美食探店", "#家乡美食"],
            "生活方式": ["#美好生活", "#生活窍门"],
            "情感心理": ["#情感故事", "#心灵鸡汤"],
            "科技数码": ["#科技资讯", "#实用技巧"],
            "娱乐搞笑": ["#轻松一刻", "#趣味生活"],
        }
        hashtags.extend(category_tags.get(category, ["#分享"])[:2])

        for tag in tags[:5]:
            clean_tag = tag.strip().replace(",", "").replace(" ", "")
            if clean_tag:
                if not clean_tag.startswith("#"):
                    clean_tag = "#" + clean_tag
                hashtags.append(clean_tag)

        seen = set()
        unique = []
        for h in hashtags:
            if h not in seen:
                seen.add(h)
                unique.append(h)
                if len(unique) >= 10:
                    break

        return unique

    def _create_default_adaptation(self, title: str, hook: str, body: str,
                                    cta: str, tags: List[str], config: Dict) -> Dict:
        """默认适配方案"""
        return {
            "platform_title": title[:config["title_max_len"]],
            "platform_description": f"{hook}\n\n{body}\n\n{cta}",
            "platform_hashtags": [f"#{t.strip()}" for t in tags[:config["hashtags_max"]]],
            "duration_note": f"建议时长: {config['min_duration']}-{config['max_duration']}秒",
            "tips": []
        }

    def export_package(self, video_path: str, platform_content: Dict,
                       output_subdir: Optional[str] = None) -> Dict:
        """
        导出平台发布包

        参数:
            video_path: 视频文件路径
            platform_content: 平台适配内容
            output_subdir: 输出子目录

        返回:
            发布包信息
        """
        platform = platform_content.get("platform", "抖音")
        config = self.platform_configs.get(platform)
        output_dir = config["output_dir"]

        if output_subdir:
            output_dir = output_dir / output_subdir
        else:
            # 按日期创建子目录
            date_str = datetime.now().strftime("%Y%m%d")
            output_dir = output_dir / date_str

        output_dir.mkdir(parents=True, exist_ok=True)

        # 复制视频
        video_name = Path(video_path).name
        dest_video = output_dir / video_name

        # 如果目标文件已存在，添加时间戳
        if dest_video.exists():
            import time
            timestamp = int(time.time())
            stem = Path(video_path).stem
            ext = Path(video_path).suffix
            dest_video = output_dir / f"{stem}_{timestamp}{ext}"

        shutil.copy2(video_path, dest_video)

        # 生成发布说明文件
        package_info = {
            "video_file": dest_video.name,
            "title": platform_content.get("platform_title", ""),
            "description": platform_content.get("platform_description", ""),
            "hashtags": platform_content.get("platform_hashtags", []),
            "tips": platform_content.get("tips", []),
            "duration_note": platform_content.get("duration_note", ""),
            "export_time": datetime.now().isoformat(),
        }

        # 保存发布说明
        info_path = dest_video.with_suffix(".txt")
        with open(info_path, "w", encoding="utf-8") as f:
            f.write(f"平台: {platform}\n")
            f.write(f"=" * 50 + "\n\n")
            f.write(f"【标题】\n{package_info['title']}\n\n")
            f.write(f"【描述】\n{package_info['description']}\n\n")
            f.write(f"【话题标签】\n" + "\n".join(package_info["hashtags"]) + "\n\n")
            if package_info["tips"]:
                f.write(f"【发布建议】\n" + "\n".join(f"- {t}" for t in package_info["tips"]) + "\n\n")
            f.write(f"【时长建议】\n{package_info['duration_note']}\n")

        # 保存JSON格式的完整信息
        json_path = dest_video.with_suffix(".json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(package_info, f, ensure_ascii=False, indent=2)

        return {
            "success": True,
            "output_dir": str(output_dir),
            "video_path": str(dest_video),
            "info_path": str(info_path),
            "package_info": package_info,
        }

    def batch_export(self, video_path: str, platforms: List[str] = None) -> List[Dict]:
        """
        批量导出到多个平台

        参数:
            video_path: 视频文件路径
            platforms: 目标平台列表，默认全部

        返回:
            各平台的导出结果
        """
        if platforms is None:
            platforms = self.platforms

        results = []
        for platform in platforms:
            try:
                # 获取平台配置
                config = self.platform_configs.get(platform)
                if not config:
                    continue

                # 获取脚本内容（需要外部传入，这里用占位）
                # 实际使用时需要先生成适配内容
                content = {
                    "platform": platform,
                    "platform_title": Path(video_path).stem,
                    "platform_description": "",
                    "platform_hashtags": [],
                    "tips": [],
                    "duration_note": "",
                }

                result = self.export_package(video_path, content)
                results.append(result)
            except Exception as e:
                results.append({
                    "success": False,
                    "platform": platform,
                    "error": str(e)
                })

        return results


# ==================== 便捷函数 ====================
_module_instance = None

def get_platform_module() -> PlatformModule:
    """获取平台模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = PlatformModule()
    return _module_instance

def adapt_for_platform(script_result: Dict, platform: str) -> Dict:
    """快速为平台适配内容"""
    return get_platform_module().adapt_content(script_result, platform)

def export_release_package(video_path: str, platform_content: Dict) -> Dict:
    """导出发布包"""
    return get_platform_module().export_package(video_path, platform_content)
