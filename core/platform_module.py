# -*- coding: utf-8 -*-
"""
多平台适配模块
自动生成抖音/小红书/B站标题+简介+话题标签
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
            target_platform: 目标平台 (抖音/小红书/B站)

        返回:
            适配后的内容字典
        """
        config = self.platform_configs.get(target_platform)
        if not config:
            raise ValueError(f"不支持的平台: {target_platform}")

        title = script_result.get("topic_title", "")
        hook = script_result.get("hook", "")
        body = script_result.get("body", "")
        cta = script_result.get("cta", "")
        category = script_result.get("category", "")
        original_tags = script_result.get("suggested_tags", [])

        if target_platform == "抖音":
            adapted = self._adapt_for_douyin(title, hook, body, cta, category, original_tags, config)
        elif target_platform == "小红书":
            adapted = self._adapt_for_xiaohongshu(title, hook, body, cta, category, original_tags, config)
        elif target_platform == "B站":
            adapted = self._adapt_for_bilibili(title, hook, body, cta, category, original_tags, config)
        else:
            adapted = self._create_default_adaptation(title, hook, body, cta, original_tags, config)

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
        dy_title = self._generate_douyin_title(title, hook)
        dy_desc = self._generate_douyin_description(hook, body, cta)
        dy_hashtags = self._generate_douyin_hashtags(category, tags)
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
        import random
        templates = [
            f"必看！{title}",
            f"{hook}！{title[:15]}",
            f"绝了！{title}",
            f"救命！{title}",
            f"学会了！{title}",
            f"曝光！{title}",
            f"揭秘！{title}",
        ]
        template = random.choice(templates)
        if len(template) > 40:
            template = template[:37] + "..."
        return template

    def _generate_douyin_description(self, hook: str, body: str, cta: str) -> str:
        """生成抖音风格描述"""
        parts = []
        if hook:
            parts.append(hook)
        if body:
            body_summary = body[:100] if len(body) > 100 else body
            parts.append(body_summary)
        if cta:
            parts.append("")
            parts.append(cta)
        parts.append("")
        parts.append("关注我，更多干货持续更新~")
        return "\n".join(parts)

    def _generate_douyin_hashtags(self, category: str, tags: List[str]) -> List[str]:
        """生成抖音话题标签"""
        hashtags = []
        hashtags.extend(TRENDING_TAGS[:5])

        category_tags = {
            "知识付费": ["#知识分享", "#干货", "#自我提升", "#成长"],
            "美食探店": ["#美食", "#探店", "#吃货", "#美食推荐"],
            "生活方式": ["#生活", "#日常", "#vlog", "#记录生活"],
            "情感心理": ["#情感", "#心理", "#治愈", "#共鸣"],
            "科技数码": ["#科技", "#数码", "#测评", "#好物推荐"],
            "娱乐搞笑": ["#搞笑", "#娱乐", "#段子", "#解压"],
        }
        hashtags.extend(category_tags.get(category, ["#分享"])[:3])

        for tag in tags[:5]:
            clean_tag = tag.strip().replace(",", "").replace(" ", "")
            if clean_tag:
                if not clean_tag.startswith("#"):
                    clean_tag = "#" + clean_tag
                hashtags.append(clean_tag)

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
        xhs_title = self._generate_xiaohongshu_title(title, hook)
        xhs_desc = self._generate_xiaohongshu_description(title, hook, body)
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
        import random
        emojis = ["", "", "", "", ""]
        templates = [
            f"{emojis[0]}保姆级|{title}",
            f"{emojis[1]}建议收藏！{title}",
            f"{emojis[2]}吐血整理|{title}",
            f"{emojis[3]}真的绝了！{title}",
            f"{emojis[4]}{title}攻略",
        ]
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

        seen = set()
        unique = []
        for h in hashtags:
            if h not in seen:
                seen.add(h)
                unique.append(h)
                if len(unique) >= 15:
                    break

        return unique

    # ==================== B站(哔哩哔哩)适配 ====================

    def _adapt_for_bilibili(self, title: str, hook: str, body: str, cta: str,
                            category: str, tags: List[str], config: Dict) -> Dict:
        """B站适配 - UP主干货分享型"""
        bl_title = self._generate_bilibili_title(title, hook, category)
        bl_desc = self._generate_bilibili_description(title, hook, body, cta)
        bl_hashtags = self._generate_bilibili_hashtags(category, tags)
        duration_note = f"建议时长: {config['min_duration']}-{config['max_duration']}秒"

        return {
            "platform_title": bl_title,
            "platform_description": bl_desc,
            "platform_hashtags": bl_hashtags,
            "duration_note": duration_note,
            "tips": [
                "全文干货无废话，建议点赞投币收藏慢慢看",
                "有问题评论区留言，看到都会回复",
                "关注我，持续更新各类干货教程",
                "长视频可加片头片尾，B站用户习惯完整看完",
            ]
        }

    def _generate_bilibili_title(self, title: str, hook: str, category: str) -> str:
        """生成B站风格标题

        B站标题特点：
        - 【前缀】包裹常用：保姆级、新手必看、耗时整理、超详细
        - 干货/教程向明确
        - 可带数字和时效性词
        """
        import random

        prefixes = [
            "【保姆级教程】",
            "【新手必看】",
            "【超详细干货】",
            "【耗时整理】",
            "【零基础入门】",
            "【建议收藏】",
            "【附清单】",
            "【干货分享】",
            "【完整版】",
            "【持续更新】",
        ]

        suffixes = [
            "建议收藏！",
            "新手必看！",
            "超详细！",
            "附资源！",
            "完整版！",
            "收藏慢慢看！",
            "无废话版！",
            "更新版！",
        ]

        prefix = random.choice(prefixes)

        if category in ["知识付费", "科技数码"]:
            suffix = random.choice(["干货教程！", "新手入门！", "建议收藏！", "超详细！"])
        elif category in ["美食探店", "生活方式"]:
            suffix = random.choice(["分享！", "教程！", "必看！", "推荐！"])
        elif category in ["情感心理"]:
            suffix = random.choice(["分享！", "必看！", "干货！", "建议收藏！"])
        else:
            suffix = random.choice(suffixes)

        core = title if title else hook
        if len(core) > 20:
            core = core[:18]

        result = f"{prefix}{core}，{suffix}"

        if len(result) > 60:
            result = result[:57] + "..."

        return result

    def _generate_bilibili_description(self, title: str, hook: str, body: str, cta: str) -> str:
        """生成B站风格描述

        B站描述特点：
        - 分点说明，内容充实
        - 引导三连：点赞、投币、收藏
        - 评论区互动引导
        - 关注引导（语气平等，不低俗）
        """
        parts = []

        if hook:
            parts.append(f"▶ {hook}")

        if body:
            body_lines = body.split("\n")
            for line in body_lines[:5]:
                if line.strip():
                    parts.append(f"・ {line.strip()}")

        parts.append("")
        parts.append("——— 观看提示 ———")
        parts.append("全文干货无废话，建议点赞投币收藏慢慢看～")
        parts.append("")
        parts.append("有问题评论区留言，看到都会回复")
        parts.append("关注我，持续更新各类干货教程")

        if cta:
            parts.append("")
            parts.append(f"▶ {cta}")

        return "\n".join(parts)

    def _generate_bilibili_hashtags(self, category: str, tags: List[str]) -> List[str]:
        """生成B站话题标签

        B站标签特点：
        - 垂直实用：干货、教程、学习
        - 去掉正能量等泛标签
        - 带数字的标签受欢迎
        - 常用：#干货 #教程 #学习 #职场 #自律
        """
        import random

        base_tags = [
            "#干货", "#教程", "#学习", "#知识分享",
            "#技能get", "#自我提升", "#持续更新"
        ]

        category_tags = {
            "知识付费": [
                "#干货分享", "#知识分享", "#学习技巧", "#职场干货",
                "#自我提升", "#终身学习", "#知识变现", "#副业教程"
            ],
            "美食探店": [
                "#美食教程", "#做饭教程", "#家常菜", "#减脂餐",
                "#快手菜", "#美食分享", "#探店记录"
            ],
            "生活方式": [
                "#生活技巧", "#日常分享", "#自律打卡", "#极简生活",
                "#穿搭分享", "#健身记录", "#vlog日常"
            ],
            "情感心理": [
                "#情感分享", "#心理成长", "#自我认知", "#人际交往",
                "#情感治愈", "#女性成长", "#心理分析"
            ],
            "科技数码": [
                "#数码教程", "#科技干货", "#APP推荐", "#效率工具",
                "#科技数码", "#AI工具", "#黑科技", "#数码测评"
            ],
            "娱乐搞笑": [
                "#娱乐分享", "#搞笑合集", "#萌宠", "#短视频",
                "#游戏解说", "#影视推荐", "#休闲娱乐"
            ],
        }

        hashtags = random.sample(base_tags, k=min(4, len(base_tags)))

        cat_tags = category_tags.get(category, ["#分享", "#干货"])
        hashtags.extend(random.sample(cat_tags, k=min(4, len(cat_tags))))

        for tag in tags[:3]:
            clean_tag = tag.strip().replace(",", "").replace(" ", "")
            if clean_tag:
                if not clean_tag.startswith("#"):
                    clean_tag = "#" + clean_tag
                if len(clean_tag) <= 15:
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
        """导出平台发布包"""
        platform = platform_content.get("platform", "抖音")
        config = self.platform_configs.get(platform)
        output_dir = config["output_dir"]

        if output_subdir:
            output_dir = output_dir / output_subdir
        else:
            date_str = datetime.now().strftime("%Y%m%d")
            output_dir = output_dir / date_str

        output_dir.mkdir(parents=True, exist_ok=True)

        video_name = Path(video_path).name
        dest_video = output_dir / video_name

        if dest_video.exists():
            import time
            timestamp = int(time.time())
            stem = Path(video_path).stem
            ext = Path(video_path).suffix
            dest_video = output_dir / f"{stem}_{timestamp}{ext}"

        shutil.copy2(video_path, dest_video)

        package_info = {
            "video_file": dest_video.name,
            "title": platform_content.get("platform_title", ""),
            "description": platform_content.get("platform_description", ""),
            "hashtags": platform_content.get("platform_hashtags", []),
            "tips": platform_content.get("tips", []),
            "duration_note": platform_content.get("duration_note", ""),
            "export_time": datetime.now().isoformat(),
        }

        info_path = dest_video.with_suffix(".txt")
        with open(info_path, "w", encoding="utf-8") as f:
            f.write(f"平台: {platform}\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"【标题】\n{package_info['title']}\n\n")
            f.write(f"【描述】\n{package_info['description']}\n\n")
            f.write(f"【话题标签】\n" + "\n".join(package_info["hashtags"]) + "\n\n")
            if package_info["tips"]:
                f.write(f"【发布建议】\n" + "\n".join(f"- {t}" for t in package_info["tips"]) + "\n\n")
            f.write(f"【时长建议】\n{package_info['duration_note']}\n")

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
        """批量导出到多个平台"""
        if platforms is None:
            platforms = self.platforms

        results = []
        for platform in platforms:
            try:
                config = self.platform_configs.get(platform)
                if not config:
                    continue

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
