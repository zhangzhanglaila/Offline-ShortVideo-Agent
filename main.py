# -*- coding: utf-8 -*-
"""
Offline-ShortVideo-Agent 主程序
本地一键完成 爆款选题→脚本分镜→自动剪辑→字幕烧录→多平台适配→数据复盘 的全链路短视频生产Agent

零API、零付费、零联网请求，完全离线运行
"""
import os
import sys
import time
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

import config
config.ensure_dirs()

from core.topics_module import TopicsModule
from core.script_module import ScriptModule
from core.video_module import VideoModule
from core.subtitle_module import SubtitleModule
from core.platform_module import PlatformModule
from core.analytics_module import AnalyticsModule
from core.db_init import init_topics_db, insert_sample_topics
from core.crawler_module import TrendingCrawler, run_crawler_and_expand


class ShortVideoAgent:
    """短视频全链路Agent"""

    def __init__(self):
        print("=" * 60)
        print("   Offline-ShortVideo-Agent 短视频AI生产系统")
        print("   零API · 零付费 · 100%离线 · 无封号风险")
        print("=" * 60)
        print()

        self._init_database()
        self.topics = TopicsModule(enable_cache=config.CACHE_CONFIG.get("enabled", True),
                                   preload_count=config.CACHE_CONFIG.get("preload_count", 500))
        self.scripts = ScriptModule()
        self.video = VideoModule()
        self.subtitle = SubtitleModule()
        self.platform = PlatformModule()
        self.analytics = AnalyticsModule()
        self.output_base = config.OUTPUT_DIR

    def _init_database(self):
        print("[初始化] 选题数据库...")
        conn = init_topics_db()
        insert_sample_topics(conn)
        conn.close()

        stats = self._get_topics_stats()
        print(f"      选题库: {stats['total']} 条 (缓存命中率: {stats.get('cache_hit_rate', 'N/A')})")
        print()

    def _get_topics_stats(self) -> Dict:
        try:
            return self.topics.get_statistics()
        except:
            return {"total": 0}

    def step1_browse_topics(self, category: Optional[str] = None,
                           keyword: Optional[str] = None,
                           limit: int = 10) -> List[Dict]:
        print("[步骤1] 浏览爆款选题")
        print("-" * 40)

        if keyword:
            topics = self.topics.search_topics(keyword, limit)
            print(f"  关键词 '{keyword}' 搜索结果: {len(topics)} 条")
        elif category:
            topics = self.topics.get_topics_by_category(category, limit)
            print(f"  赛道 '{category}' 选题: {len(topics)} 条")
        else:
            topics = self.topics.get_all_topics(limit)
            print(f"  全部分类选题: {len(topics)} 条")

        for i, topic in enumerate(topics[:10], 1):
            print(f"\n  [{i}] {topic['title']}")
            print(f"      赛道: {topic['category']} > {topic['sub_category']}")
            print(f"      钩子: {topic['hook']}")
            print(f"      热度: {topic['heat_score']} | 转化: {topic['transform_rate']*100:.0f}%")

        return topics

    def step2_recommend_topics(self, category: Optional[str] = None,
                               count: int = 5) -> List[Dict]:
        print("\n[步骤2] 智能推荐选题")
        print("-" * 40)

        recommendations = self.topics.recommend_topics(category=category, count=count)

        print(f"  基于热度+转化率+匹配度，推荐以下 {len(recommendations)} 个选题:\n")
        for i, topic in enumerate(recommendations, 1):
            print(f"  ★ 推荐 {i}: {topic['title']}")
            print(f"      钩子: {topic['hook']}")
            print(f"      热度: {topic['heat_score']} | 转化: {topic['transform_rate']*100:.0f}%")

        return recommendations

    def step3_generate_script(self, topic: Dict, platform: str = "抖音",
                              duration: int = 30) -> Dict:
        print(f"\n[步骤3] 生成{platform}口播脚本")
        print("-" * 40)
        print(f"  选题: {topic.get('title', '')}")
        print(f"  时长: {duration}秒 | 平台: {platform}")
        print("  正在调用本地Ollama推理...")

        script_result = self.scripts.generate_script(topic, platform, duration)

        print("\n  生成结果:")
        print(f"  ┌─ 黄金3秒钩子 ─")
        print(f"  │ {script_result.get('hook', '')}")
        print(f"  ├─ 主体内容 ─")
        body = script_result.get('body', '')
        if isinstance(body, list):
            body = ' '.join(body)
        for line in body.split('\n')[:3]:
            if line.strip():
                print(f"  │ {line.strip()}")
        print(f"  ├─ 行动号召 ─")
        print(f"  │ {script_result.get('cta', '')}")

        storyboard = script_result.get('storyboard', [])
        if storyboard:
            print(f"  └─ 分镜表 ({len(storyboard)}个镜头)")
            for shot in storyboard[:5]:
                print(f"      {shot.get('time', '')} | {shot.get('scene', '')[:20]}")

        script_id = self.scripts.save_script_to_db(script_result)
        script_result['script_id'] = script_id

        return script_result

    def step4_create_video(self, script_result: Dict,
                           images: Optional[List[str]] = None,
                           use_auto_material: bool = True,
                           add_bgm: bool = True) -> Optional[str]:
        print(f"\n[步骤4] 自动剪辑视频")
        print("-" * 40)

        if use_auto_material:
            if not images:
                images = self.video.auto_select_materials(count=5)
            if not images:
                print("  警告: 素材池为空，请手动放入图片到 assets/素材池_待剪辑/ 目录")
                return None

            print(f"  使用 {len(images)} 张图片生成视频")

        bgm_path = None
        if add_bgm:
            available_bgm = self.video.get_available_bgm()
            if available_bgm:
                bgm_path = available_bgm[0]
                print(f"  BGM: {Path(bgm_path).name}")
            else:
                print("  警告: 未找到BGM素材")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = config.OUTPUT_DIR / platform_name_to_folder(script_result.get('platform', '抖音'))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"video_{timestamp}.mp4")

        print("  正在生成视频...")
        print(f"  输出: {output_path}")

        success = self.video.create_video_from_images(
            images=images,
            output_path=output_path,
            duration_per_image=5,
            transition="fade",
            bgm_path=bgm_path
        )

        if success:
            print("  ✓ 视频生成成功!")
            return output_path
        else:
            print("  ✗ 视频生成失败")
            return None

    def step5_add_subtitles(self, video_path: str,
                            script_content: str,
                            use_whisper: bool = False) -> tuple:
        print(f"\n[步骤5] 添加字幕")
        print("-" * 40)

        duration = self.video._get_media_duration(video_path)
        if duration <= 0:
            duration = 30

        print(f"  视频时长: {duration:.1f}秒")
        print(f"  字幕方式: {'Whisper语音识别' if use_whisper else '脚本直接生成'}")

        output_path = video_path.replace('.mp4', '_subtitled.mp4')

        success, srt_path = self.subtitle.generate_subtitle_video(
            video_path=video_path,
            script=script_content,
            output_path=output_path,
            duration=duration,
            use_whisper=use_whisper
        )

        if success:
            print(f"  ✓ 字幕添加成功!")
            print(f"    视频: {output_path}")
            print(f"    字幕: {srt_path}")
            return True, output_path
        else:
            print("  ✗ 字幕添加失败")
            return False, video_path

    def step6_adapt_platform(self, video_path: str,
                            script_result: Dict,
                            platforms: List[str] = None) -> List[Dict]:
        print(f"\n[步骤6] 多平台适配")
        print("-" * 40)

        if platforms is None:
            platforms = ["抖音", "小红书", "B站"]

        results = []

        for p in platforms:
            print(f"\n  适配 {p}...")

            platform_content = self.platform.adapt_content(script_result, p)
            export_result = self.platform.export_package(video_path, platform_content)

            if export_result['success']:
                print(f"    ✓ 标题: {platform_content.get('platform_title', '')[:30]}...")
                print(f"    ✓ 已导出到: {export_result['output_dir']}")
            else:
                print(f"    ✗ 导出失败")

            results.append({
                "platform": p,
                "content": platform_content,
                "export": export_result
            })

        return results

    def step7_record_and_analyze(self, script_id: int,
                                 sample_metrics: Optional[Dict] = None) -> Dict:
        print(f"\n[步骤7] 数据记录与分析")
        print("-" * 40)

        if sample_metrics:
            record_id = self.analytics.record_metrics(script_id, sample_metrics)
            print(f"  已记录数据: 播放量={sample_metrics.get('views', 0)}")

        print("\n  生成数据报告...")
        report = self.analytics.get_weekly_report()

        print(f"\n  本周概览:")
        print(f"    视频数量: {report['summary']['video_count']}")
        print(f"    总播放: {report['summary']['total_views']}")
        print(f"    总点赞: {report['summary']['total_likes']}")
        print(f"    平均完播率: {report['summary']['avg_completion_rate']}%")

        recommendations = self.analytics.generate_recommended_topics(count=5)
        if recommendations:
            print(f"\n  基于数据分析，推荐以下选题:")
            for i, rec in enumerate(recommendations[:3], 1):
                print(f"    {i}. {rec['title']}")
                print(f"       原因: {rec.get('recommendation_reason', '')}")

        return report

    def run_full_workflow(self, topic_id: Optional[int] = None,
                         category: Optional[str] = None,
                         platform: str = "抖音",
                         duration: int = 30) -> Dict:
        print("\n" + "=" * 60)
        print("   开始执行完整短视频生产流程")
        print("=" * 60)

        result = {
            "start_time": datetime.now().isoformat(),
            "steps": {}
        }

        print("\n>>> 步骤1: 选择选题")
        if topic_id:
            topic = self.topics.get_topic_by_id(topic_id)
            if not topic:
                print(f"  错误: 选题 {topic_id} 不存在")
                return result
        else:
            topics = self.step2_recommend_topics(category=category, count=1)
            if not topics:
                print("  错误: 未找到合适的选题")
                return result
            topic = topics[0]

        print(f"  已选择: {topic['title']}")

        print("\n>>> 步骤2: 生成脚本")
        script_result = self.step3_generate_script(topic, platform, duration)
        result["steps"]["script"] = script_result
        script_id = script_result.get("script_id")

        print("\n>>> 步骤3: 检查素材")
        images = self.video.get_material_images()
        if not images:
            print("  警告: 素材池为空")
            print("  请将图片放入: assets/素材池_待剪辑/ 目录")

        print("\n>>> 步骤4: 生成视频")
        video_path = self.step4_create_video(
            script_result,
            images=images if images else None,
            add_bgm=True
        )

        if not video_path:
            print("  视频生成失败，跳过后续步骤")
            result["error"] = "视频生成失败"
            return result

        result["steps"]["video"] = {"path": video_path}

        print("\n>>> 步骤5: 添加字幕")
        script_content = script_result.get("full_script", "")
        success, final_video = self.step5_add_subtitles(
            video_path,
            script_content,
            use_whisper=False
        )
        result["steps"]["subtitle"] = {"success": success, "path": final_video}

        print("\n>>> 步骤6: 多平台适配")
        platforms = ["抖音", "小红书", "B站"]
        platform_results = self.step6_adapt_platform(final_video, script_result, platforms)
        result["steps"]["platforms"] = platform_results

        print("\n>>> 步骤7: 数据记录")
        self.step7_record_and_analyze(script_id)

        result["end_time"] = datetime.now().isoformat()
        result["success"] = True

        print("\n" + "=" * 60)
        print("   ✓ 短视频生产流程完成!")
        print("=" * 60)
        print(f"\n  输出目录: {config.OUTPUT_DIR}")
        print(f"  视频文件: {final_video}")

        return result

    def expand_topic_library(self, target_count: int = 1000):
        """扩充选题库到目标数量"""
        print("\n" + "=" * 50)
        print("   扩充选题库")
        print("=" * 50)

        current_stats = self.topics.get_statistics()
        current_count = current_stats['total']
        print(f"\n  当前选题: {current_count} 条")
        print(f"  目标数量: {target_count} 条")

        if current_count >= target_count:
            print("  选题库已满足要求，无需扩充")
            return current_stats

        print("\n  正在扩充选题库...")
        expand_result = self.topics.expand_library(target_count)

        self.topics.invalidate_cache()
        new_stats = self.topics.get_statistics()

        print(f"\n  扩充完成!")
        print(f"  原有: {expand_result.get('before', current_count)} 条")
        print(f"  新增: {expand_result.get('generated', 0)} 条")
        print(f"  当前: {new_stats['total']} 条")

        return new_stats

    async def _async_crawl_topics(self, platforms: List[str] = None, keywords: List[str] = None):
        """异步爬取选题"""
        crawler = TrendingCrawler()
        stats = await crawler.crawl_all_platforms(keywords=keywords, platforms=platforms)
        self.topics.invalidate_cache()
        return stats

    def crawl_topics(self, platforms: List[str] = None, keywords: List[str] = None):
        """爬取爆款选题 (联网)"""
        print("\n" + "=" * 50)
        print("   爆款选题爬虫")
        print("   ⚠ 将访问抖音/小红书/B站公开页面")
        print("   ⚠ 爬取完成后自动切换离线模式")
        print("=" * 50)

        confirm = input("\n确认开始爬取? (y/n): ").strip().lower()
        if confirm != 'y':
            print("已取消")
            return

        try:
            stats = asyncio.run(self._async_crawl_topics(platforms, keywords))
            print(f"\n爬取完成! 统计: {stats}")
        except ImportError:
            print("\n错误: 请先安装 Playwright")
            print("  pip install playwright")
            print("  playwright install chromium")
        except Exception as e:
            print(f"\n爬取出错: {e}")

    def get_cache_stats(self) -> Dict:
        """获取缓存统计"""
        return self.topics.get_statistics()

    def quick_demo(self):
        print("\n" + "=" * 60)
        print("   快速演示模式")
        print("=" * 60)

        topic = self.topics.get_random_topic()
        if not topic:
            print("错误: 无法获取选题")
            return

        print(f"\n使用随机选题: {topic['title']}")

        script_result = self.scripts.generate_script(topic, "抖音", 30)

        print("\n脚本预览:")
        print(f"  钩子: {script_result.get('hook', '')}")
        print(f"  脚本: {script_result.get('full_script', '')[:100]}...")

        images = self.video.get_material_images()
        if images:
            print(f"\n发现 {len(images)} 张素材图片")
            print("可执行完整流程生成视频")
        else:
            print("\n素材池为空，请先放入图片素材")

        return script_result


def platform_name_to_folder(name: str) -> str:
    mapping = {
        "抖音": "抖音",
        "小红书": "小红书",
        "B站": "B站",
    }
    return mapping.get(name, name)


def print_menu():
    print("\n" + "=" * 60)
    print("        Offline-ShortVideo-Agent 主菜单")
    print("=" * 60)
    print("  1. 浏览爆款选题库")
    print("  2. 智能推荐选题")
    print("  3. 生成口播脚本")
    print("  4. 执行完整生产流程")
    print("  5. 交互式生产")
    print("  6. 快速演示")
    print("  7. 数据复盘分析")
    print("  ─────────────────")
    print("  8. 扩充选题库 (当前1000+条)")
    print("  9. 爬取爆款选题 (联网)")
    print("  0. 退出")
    print("=" * 60)


def main():
    print("检查环境...")
    try:
        import faster_whisper
        print("  ✓ faster-whisper")
    except:
        print("  ⚠ faster-whisper 未安装 (可选)")

    try:
        import subprocess
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
        print("  ✓ FFmpeg")
    except:
        print("  ⚠ FFmpeg 未安装 (必须)")

    try:
        import ollama
        print("  ✓ Ollama Python客户端")
    except:
        print("  ⚠ Ollama Python客户端未安装 (可选)")

    try:
        from core.crawler_module import PLAYWRIGHT_AVAILABLE
        if PLAYWRIGHT_AVAILABLE:
            print("  ✓ Playwright")
        else:
            print("  ⚠ Playwright 未安装 (可选，爬虫功能需要)")
    except:
        print("  ⚠ Playwright 未安装 (可选，爬虫功能需要)")

    print("\n启动程序...")
    agent = ShortVideoAgent()

    while True:
        print_menu()
        choice = input("\n请输入选项: ").strip()

        if choice == "1":
            print("\n--- 浏览选题库 ---")
            print("1. 全部选题  2. 按赛道筛选  3. 关键词搜索")
            sub = input("请选择: ").strip()
            if sub == "1":
                agent.step1_browse_topics(limit=20)
            elif sub == "2":
                cats = agent.topics.get_categories()
                for i, c in enumerate(cats, 1):
                    print(f"  {i}. {c}")
                c = input("选择赛道: ").strip()
                if c.isdigit() and 0 < int(c) <= len(cats):
                    agent.step1_browse_topics(category=cats[int(c)-1], limit=20)
            elif sub == "3":
                kw = input("输入关键词: ").strip()
                agent.step1_browse_topics(keyword=kw, limit=20)

        elif choice == "2":
            print("\n--- 智能推荐 ---")
            cats = agent.topics.get_categories()
            print("0. 不限赛道")
            for i, c in enumerate(cats, 1):
                print(f"  {i}. {c}")
            c = input("选择赛道(可选): ").strip()
            cat = cats[int(c)-1] if c.isdigit() and 0 < int(c) <= len(cats) else None
            agent.step2_recommend_topics(category=cat, count=10)

        elif choice == "3":
            print("\n--- 生成脚本 ---")
            topics = agent.step2_recommend_topics(count=5)
            if topics:
                print("\n选择选题编号生成脚本:")
                idx = input(": ").strip()
                if idx.isdigit() and 0 < int(idx) <= len(topics):
                    topic = topics[int(idx)-1]
                    platform = input("平台(默认抖音): ").strip() or "抖音"
                    script = agent.step3_generate_script(topic, platform, 30)
                    print("\n完整脚本:")
                    print(script.get("full_script", ""))

        elif choice == "4":
            print("\n--- 完整生产流程 ---")
            topic_id = input("选题ID(留空随机): ").strip()
            topic_id = int(topic_id) if topic_id.isdigit() else None
            platform = input("平台(默认抖音): ").strip() or "抖音"
            duration = input("时长秒(默认30): ").strip()
            duration = int(duration) if duration.isdigit() else 30

            agent.run_full_workflow(
                topic_id=topic_id,
                platform=platform,
                duration=duration
            )

        elif choice == "5":
            print("\n交互式模式需要更多交互代码，暂不支持")
            pass

        elif choice == "6":
            agent.quick_demo()

        elif choice == "7":
            print("\n--- 数据复盘 ---")
            print("1. 周报  2. 爆款分析  3. 选题推荐")
            sub = input("请选择: ").strip()
            analytics = agent.analytics
            if sub == "1":
                report = analytics.get_weekly_report()
                print(f"\n本周视频: {report['summary']['video_count']}")
                print(f"总播放: {report['summary']['total_views']}")
                print(f"总点赞: {report['summary']['total_likes']}")
            elif sub == "2":
                top = analytics.analyze_top_performing(limit=5)
                print("\n爆款视频TOP5:")
                for i, v in enumerate(top, 1):
                    print(f"  {i}. 播放{v['views']} 点赞{v['likes']} 完播{v['completion_rate']}%")
            elif sub == "3":
                recs = analytics.generate_recommended_topics(count=10)
                print("\n推荐选题:")
                for i, r in enumerate(recs, 1):
                    print(f"  {i}. {r['title']}")

        elif choice == "8":
            target = input("目标数量 (默认1000): ").strip()
            target = int(target) if target.isdigit() else 1000
            agent.expand_topic_library(target)

        elif choice == "9":
            print("\n--- 爆款选题爬虫 ---")
            print("支持的平台: 1.抖音  2.小红书  3.B站 (多选用空格分隔)")
            platform_input = input("选择平台 (默认全部): ").strip()
            if platform_input:
                platform_map = {"1": "抖音", "2": "小红书", "3": "B站"}
                platforms = [platform_map[p] for p in platform_input.split() if p in platform_map]
            else:
                platforms = None

            keywords_input = input("关键词 (逗号分隔，默认爆款选题,干货分享): ").strip()
            keywords = [k.strip() for k in keywords_input.split(",")] if keywords_input else None

            agent.crawl_topics(platforms=platforms, keywords=keywords)

        elif choice == "0":
            print("\n感谢使用 Offline-ShortVideo-Agent!")
            break

        else:
            print("无效选项")


if __name__ == "__main__":
    main()
