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
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

# 导入配置
import config
config.ensure_dirs()

# 导入核心模块
from core.topics_module import TopicsModule
from core.script_module import ScriptModule
from core.video_module import VideoModule
from core.subtitle_module import SubtitleModule
from core.platform_module import PlatformModule
from core.analytics_module import AnalyticsModule
from core.db_init import init_topics_db, insert_sample_topics


class ShortVideoAgent:
    """短视频全链路Agent"""

    def __init__(self):
        """初始化Agent"""
        print("=" * 60)
        print("   Offline-ShortVideo-Agent 短视频AI生产系统")
        print("   零API · 零付费 · 100%离线 · 无封号风险")
        print("=" * 60)
        print()

        # 初始化数据库
        self._init_database()

        # 初始化各模块
        self.topics = TopicsModule()
        self.scripts = ScriptModule()
        self.video = VideoModule()
        self.subtitle = SubtitleModule()
        self.platform = PlatformModule()
        self.analytics = AnalyticsModule()

        # 输出目录
        self.output_base = config.OUTPUT_DIR

    def _init_database(self):
        """初始化数据库"""
        print("[1/6] 初始化选题数据库...")
        conn = init_topics_db()
        insert_sample_topics(conn)
        conn.close()
        print("      数据库初始化完成")
        print()

    def step1_browse_topics(self, category: Optional[str] = None,
                           keyword: Optional[str] = None,
                           limit: int = 10) -> List[Dict]:
        """
        步骤1: 浏览爆款选题

        参数:
            category: 赛道筛选 (知识付费/美食探店/生活方式/情感心理/科技数码/娱乐搞笑)
            keyword: 关键词搜索
            limit: 返回数量

        返回:
            选题列表
        """
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

        # 显示选题列表
        for i, topic in enumerate(topics[:10], 1):
            print(f"\n  [{i}] {topic['title']}")
            print(f"      赛道: {topic['category']} > {topic['sub_category']}")
            print(f"      钩子: {topic['hook']}")
            print(f"      热度: {topic['heat_score']} | 转化: {topic['transform_rate']*100:.0f}%")

        return topics

    def step2_recommend_topics(self, category: Optional[str] = None,
                               count: int = 5) -> List[Dict]:
        """
        步骤2: 智能推荐选题

        参数:
            category: 赛道筛选
            count: 推荐数量

        返回:
            推荐的选题列表
        """
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
        """
        步骤3: 生成口播脚本和分镜

        参数:
            topic: 选题字典
            platform: 目标平台
            duration: 视频时长(秒)

        返回:
            生成的脚本数据
        """
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

        # 分镜信息
        storyboard = script_result.get('storyboard', [])
        if storyboard:
            print(f"  └─ 分镜表 ({len(storyboard)}个镜头)")
            for shot in storyboard[:5]:
                print(f"      {shot.get('time', '')} | {shot.get('scene', '')[:20]}")

        # 保存到数据库
        script_id = self.scripts.save_script_to_db(script_result)
        script_result['script_id'] = script_id

        return script_result

    def step4_create_video(self, script_result: Dict,
                           images: Optional[List[str]] = None,
                           use_auto_material: bool = True,
                           add_bgm: bool = True) -> Optional[str]:
        """
        步骤4: 自动剪辑视频

        参数:
            script_result: 脚本数据
            images: 图片素材列表(可选)
            use_auto_material: 自动从素材池选择
            add_bgm: 是否添加BGM

        返回:
            生成视频的路径
        """
        print(f"\n[步骤4] 自动剪辑视频")
        print("-" * 40)

        # 获取或选择素材
        if use_auto_material:
            if not images:
                images = self.video.auto_select_materials(count=5)
            if not images:
                print("  警告: 素材池为空，请手动放入图片到 assets/素材池_待剪辑/ 目录")
                return None

            print(f"  使用 {len(images)} 张图片生成视频")

        # 获取BGM
        bgm_path = None
        if add_bgm:
            available_bgm = self.video.get_available_bgm()
            if available_bgm:
                bgm_path = available_bgm[0]
                print(f"  BGM: {Path(bgm_path).name}")
            else:
                print("  警告: 未找到BGM素材")

        # 生成输出路径
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = config.OUTPUT_DIR / platform_name_to_folder(script_result.get('platform', '抖音'))
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"video_{timestamp}.mp4")

        print("  正在生成视频...")
        print(f"  输出: {output_path}")

        # 创建视频
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
        """
        步骤5: 添加字幕

        参数:
            video_path: 视频路径
            script_content: 脚本内容
            use_whisper: 是否使用Whisper识别

        返回:
            (是否成功, 视频路径)
        """
        print(f"\n[步骤5] 添加字幕")
        print("-" * 40)

        # 获取视频时长
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
        """
        步骤6: 多平台适配

        参数:
            video_path: 视频路径
            script_result: 脚本数据
            platforms: 目标平台列表

        返回:
            各平台的适配结果
        """
        print(f"\n[步骤6] 多平台适配")
        print("-" * 40)

        if platforms is None:
            platforms = ["抖音", "小红书", "视频号"]

        results = []

        for p in platforms:
            print(f"\n  适配 {p}...")

            # 生成平台适配内容
            platform_content = self.platform.adapt_content(script_result, p)

            # 导出发布包
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
        """
        步骤7: 数据记录与分析

        参数:
            script_id: 脚本ID
            sample_metrics: 示例数据(用于演示)

        返回:
            分析报告
        """
        print(f"\n[步骤7] 数据记录与分析")
        print("-" * 40)

        # 记录示例数据(如果有)
        if sample_metrics:
            record_id = self.analytics.record_metrics(script_id, sample_metrics)
            print(f"  已记录数据: 播放量={sample_metrics.get('views', 0)}")

        # 生成周报
        print("\n  生成数据报告...")
        report = self.analytics.get_weekly_report()

        print(f"\n  本周概览:")
        print(f"    视频数量: {report['summary']['video_count']}")
        print(f"    总播放: {report['summary']['total_views']}")
        print(f"    总点赞: {report['summary']['total_likes']}")
        print(f"    平均完播率: {report['summary']['avg_completion_rate']}%")

        # 推荐选题
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
        """
        运行完整工作流

        参数:
            topic_id: 指定选题ID
            category: 指定赛道
            platform: 目标平台
            duration: 视频时长

        返回:
            完整流程结果
        """
        print("\n" + "=" * 60)
        print("   开始执行完整短视频生产流程")
        print("=" * 60)

        result = {
            "start_time": datetime.now().isoformat(),
            "steps": {}
        }

        # 步骤1: 选择选题
        print("\n>>> 步骤1: 选择选题")
        if topic_id:
            topic = self.topics.get_topic_by_id(topic_id)
            if not topic:
                print(f"  错误: 选题 {topic_id} 不存在")
                return result
        else:
            # 智能推荐
            topics = self.step2_recommend_topics(category=category, count=1)
            if not topics:
                print("  错误: 未找到合适的选题")
                return result
            topic = topics[0]

        print(f"  已选择: {topic['title']}")

        # 步骤2: 生成脚本
        print("\n>>> 步骤2: 生成脚本")
        script_result = self.step3_generate_script(topic, platform, duration)
        result["steps"]["script"] = script_result
        script_id = script_result.get("script_id")

        # 步骤3: 检查素材
        print("\n>>> 步骤3: 检查素材")
        images = self.video.get_material_images()
        if not images:
            print("  警告: 素材池为空")
            print("  请将图片放入: assets/素材池_待剪辑/ 目录")
            print("  然后重新运行或在步骤4手动指定图片")

        # 步骤4: 生成视频
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

        # 步骤5: 添加字幕
        print("\n>>> 步骤5: 添加字幕")
        script_content = script_result.get("full_script", "")
        success, final_video = self.step5_add_subtitles(
            video_path,
            script_content,
            use_whisper=False
        )
        result["steps"]["subtitle"] = {"success": success, "path": final_video}

        # 步骤6: 多平台适配
        print("\n>>> 步骤6: 多平台适配")
        platforms = ["抖音", "小红书", "视频号"]
        platform_results = self.step6_adapt_platform(final_video, script_result, platforms)
        result["steps"]["platforms"] = platform_results

        # 步骤7: 数据记录
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

    def interactive_mode(self):
        """交互式模式"""
        print("\n" + "=" * 60)
        print("   交互式短视频生产")
        print("=" * 60)

        # 1. 选择赛道
        print("\n请选择赛道 (输入数字):")
        categories = self.topics.get_categories()
        for i, cat in enumerate(categories, 1):
            print(f"  {i}. {cat}")
        print(f"  0. 不限赛道")

        choice = input("\n你的选择: ").strip()
        category = categories[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(categories) else None

        # 2. 推荐选题
        topics = self.step2_recommend_topics(category=category, count=5)

        if not topics:
            print("未找到选题，退出")
            return

        # 3. 选择选题
        print("\n请选择选题编号 (输入数字):")
        choice = input("你的选择: ").strip()
        idx = int(choice) - 1 if choice.isdigit() and 0 <= idx < len(topics) else 0
        topic = topics[idx]

        # 4. 选择平台
        print("\n请选择目标平台:")
        platforms = ["抖音", "小红书", "视频号"]
        for i, p in enumerate(platforms, 1):
            print(f"  {i}. {p}")

        choice = input("\n你的选择: ").strip()
        platform = platforms[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(platforms) else "抖音"

        # 5. 选择时长
        print("\n请选择视频时长:")
        durations = [15, 30, 45, 60]
        for i, d in enumerate(durations, 1):
            print(f"  {i}. {d}秒")

        choice = input("\n你的选择: ").strip()
        duration = durations[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(durations) else 30

        # 6. 执行流程
        self.run_full_workflow(
            topic_id=topic.get("id"),
            platform=platform,
            duration=duration
        )

    def quick_demo(self):
        """快速演示模式"""
        print("\n" + "=" * 60)
        print("   快速演示模式")
        print("=" * 60)

        # 使用随机选题
        topic = self.topics.get_random_topic()
        if not topic:
            print("错误: 无法获取选题")
            return

        print(f"\n使用随机选题: {topic['title']}")

        # 生成脚本
        script_result = self.scripts.generate_script(topic, "抖音", 30)

        print("\n脚本预览:")
        print(f"  钩子: {script_result.get('hook', '')}")
        print(f"  脚本: {script_result.get('full_script', '')[:100]}...")

        # 检查素材
        images = self.video.get_material_images()
        if images:
            print(f"\n发现 {len(images)} 张素材图片")
            print("可执行完整流程生成视频")
        else:
            print("\n素材池为空，请先放入图片素材")

        return script_result


def platform_name_to_folder(name: str) -> str:
    """平台名转文件夹名"""
    mapping = {
        "抖音": "抖音",
        "小红书": "小红书",
        "视频号": "视频号",
    }
    return mapping.get(name, name)


def print_menu():
    """打印主菜单"""
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
    print("  0. 退出")
    print("=" * 60)


def main():
    """主函数"""
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
            agent.interactive_mode()

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

        elif choice == "0":
            print("\n感谢使用 Offline-ShortVideo-Agent!")
            break

        else:
            print("无效选项")


if __name__ == "__main__":
    # 检查依赖
    print("检查环境...")
    try:
        import faster_whisper
        print("  ✓ faster-whisper")
    except:
        print("  ⚠ faster-whisper 未安装 (可选)")

    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True)
        print("  ✓ FFmpeg")
    except:
        print("  ⚠ FFmpeg 未安装 (必须)")

    try:
        import ollama
        print("  ✓ Ollama Python客户端")
    except:
        print("  ⚠ Ollama Python客户端未安装 (可选)")

    print("\n启动程序...")
    main()
