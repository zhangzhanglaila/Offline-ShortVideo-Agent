# -*- coding: utf-8 -*-
"""
Offline-ShortVideo-Agent Web 前端
Flask极简Web服务，本地127.0.0.1:5000

启动方式: python app.py
"""
import os
import sys
import json
import time
import webbrowser
import threading
import shutil
from pathlib import Path
from datetime import datetime
from functools import wraps

from flask import Flask, send_from_directory, jsonify, request, send_file

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

# 创建Flask应用
app = Flask(__name__, static_folder='web', static_url_path='')
app.config['JSON_AS_ASCII'] = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max

# 全局模块实例
_topics_module = None
_script_module = None
_video_module = None
_subtitle_module = None
_platform_module = None
_analytics_module = None

# 素材暂存目录
UPLOAD_TEMP_DIR = config.MATERIAL_DIR


def get_topics_module():
    """获取选题模块单例"""
    global _topics_module
    if _topics_module is None:
        _topics_module = TopicsModule(
            enable_cache=config.CACHE_CONFIG.get("enabled", True),
            preload_count=config.CACHE_CONFIG.get("preload_count", 500)
        )
    return _topics_module


def get_script_module():
    """获取脚本模块单例"""
    global _script_module
    if _script_module is None:
        _script_module = ScriptModule()
    return _script_module


def get_video_module():
    """获取视频模块单例"""
    global _video_module
    if _video_module is None:
        _video_module = VideoModule()
    return _video_module


def get_subtitle_module():
    """获取字幕模块单例"""
    global _subtitle_module
    if _subtitle_module is None:
        _subtitle_module = SubtitleModule()
    return _subtitle_module


def get_platform_module():
    """获取平台模块单例"""
    global _platform_module
    if _platform_module is None:
        _platform_module = PlatformModule()
    return _platform_module


def get_analytics_module():
    """获取分析模块单例"""
    global _analytics_module
    if _analytics_module is None:
        _analytics_module = AnalyticsModule()
    return _analytics_module


def init_database():
    """初始化数据库"""
    conn = init_topics_db()
    insert_sample_topics(conn)
    conn.close()


# ==================== 路由 ====================

@app.route('/')
def index():
    """首页"""
    return send_file('web/index.html')


@app.route('/api/stats')
def api_stats():
    """获取系统统计"""
    try:
        topics = get_topics_module()
        stats = topics.get_statistics()

        # 统计作品数量
        works_count = 0
        output_dir = Path(config.OUTPUT_DIR)
        if output_dir.exists():
            for platform_dir in output_dir.iterdir():
                if platform_dir.is_dir():
                    works_count += len(list(platform_dir.rglob('*.mp4')))

        cache_stats = stats.get('cache_stats', {})
        hit_rate = cache_stats.get('hit_rate', '0%')

        return jsonify({
            'topics_count': stats.get('total', 0),
            'cache_hit_rate': hit_rate,
            'cache_size': cache_stats.get('size', 0),
            'works_count': works_count,
            'category_stats': stats.get('by_category', {}),
            'cache_stats': cache_stats,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/categories')
def api_categories():
    """获取赛道分类"""
    categories = config.CATEGORIES
    icons = {
        "知识付费": "💡",
        "美食探店": "🍜",
        "生活方式": "🌿",
        "情感心理": "💝",
        "科技数码": "💻",
        "娱乐搞笑": "🎮",
    }
    return jsonify([
        {'name': name, 'icon': icons.get(name, '📁')}
        for name in categories.keys()
    ])


@app.route('/api/topics')
def api_topics():
    """获取选题列表"""
    try:
        limit = int(request.args.get('limit', 20))
        offset = int(request.args.get('offset', 0))
        category = request.args.get('category', '')
        keyword = request.args.get('keyword', '')

        topics = get_topics_module()

        if keyword:
            topic_list = topics.search_topics(keyword, limit + offset)
            topic_list = topic_list[offset:offset + limit]
        elif category and category != 'all':
            topic_list = topics.get_topics_by_category(category, limit + offset)
            topic_list = topic_list[offset:offset + limit]
        else:
            topic_list = topics.get_all_topics(limit + offset)
            topic_list = topic_list[offset:offset + limit]

        return jsonify({'topics': topic_list})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/topics/recommend')
def api_recommend():
    """智能推荐选题"""
    try:
        category = request.args.get('category', '')
        count = int(request.args.get('count', 5))

        topics = get_topics_module()
        result = topics.recommend_topics(
            category=category if category and category != 'all' else None,
            count=count
        )

        return jsonify({'topics': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/materials')
def api_materials():
    """获取素材列表"""
    try:
        materials = []
        material_dir = Path(UPLOAD_TEMP_DIR)

        if material_dir.exists():
            for f in material_dir.iterdir():
                if f.is_file():
                    ext = f.suffix.lower()
                    if ext in ['.jpg', '.jpeg', '.png', '.webp']:
                        mtype = 'image'
                    elif ext in ['.mp4', '.avi', '.mov', '.mkv']:
                        mtype = 'video'
                    elif ext in ['.mp3', '.wav', '.aac', '.m4a']:
                        mtype = 'audio'
                    else:
                        continue

                    materials.append({
                        'name': f.name,
                        'path': str(f),
                        'type': mtype,
                        'size': f.stat().st_size,
                        'size_str': format_size(f.stat().st_size),
                        'date': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
                    })

        materials.sort(key=lambda x: x['date'], reverse=True)
        return jsonify({'materials': materials})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/materials/upload', methods=['POST'])
def api_materials_upload():
    """上传素材文件"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': '没有文件'}), 400

        files = request.files.getlist('file')
        uploaded = []

        for f in files:
            if f.filename:
                # 保存到素材目录
                save_path = Path(UPLOAD_TEMP_DIR) / f.filename
                f.save(str(save_path))
                uploaded.append(f.filename)

        return jsonify({
            'success': True,
            'uploaded': uploaded,
            'count': len(uploaded)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/materials/<filename>', methods=['DELETE'])
def api_materials_delete(filename):
    """删除单个素材"""
    try:
        file_path = Path(UPLOAD_TEMP_DIR) / filename
        if file_path.exists():
            file_path.unlink()
            return jsonify({'success': True})
        return jsonify({'error': '文件不存在'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/materials/clear', methods=['POST'])
def api_materials_clear():
    """清空所有素材"""
    try:
        count = 0
        material_dir = Path(UPLOAD_TEMP_DIR)
        if material_dir.exists():
            for f in material_dir.iterdir():
                if f.is_file():
                    ext = f.suffix.lower()
                    if ext in ['.jpg', '.jpeg', '.png', '.webp', '.mp4', '.avi', '.mov', '.mkv', '.mp3', '.wav', '.aac', '.m4a']:
                        f.unlink()
                        count += 1
        return jsonify({'success': True, 'cleared': count})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/works')
def api_works():
    """获取已生成作品"""
    try:
        works = []
        output_dir = Path(config.OUTPUT_DIR)

        if output_dir.exists():
            for platform_dir in output_dir.iterdir():
                if platform_dir.is_dir():
                    for video_file in platform_dir.rglob('*.mp4'):
                        # 查找对应的信息文件
                        info_file = video_file.with_suffix('.txt')
                        title = video_file.stem

                        if info_file.exists():
                            try:
                                content = info_file.read_text(encoding='utf-8')
                                for line in content.split('\n'):
                                    if line.startswith('【标题】'):
                                        title = line.replace('【标题】', '').strip()
                                        break
                            except:
                                pass

                        works.append({
                            'name': video_file.name,
                            'path': str(video_file),
                            'platform': platform_dir.name,
                            'title': title,
                            'date': datetime.fromtimestamp(video_file.stat().st_mtime).strftime('%Y-%m-%d')
                        })

        # 按时间倒序
        works.sort(key=lambda x: x['date'], reverse=True)
        return jsonify({'works': works})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/generate/with-materials', methods=['POST'])
def api_generate_with_materials():
    """使用用户素材生成视频"""
    try:
        data = request.get_json()
        category = data.get('category', '')
        platforms = data.get('platforms', ['抖音', '小红书', 'B站'])
        material_paths = data.get('materials', [])

        # 初始化模块
        topics = get_topics_module()
        scripts = get_script_module()
        video = get_video_module()
        subtitle = get_subtitle_module()
        platform_mod = get_platform_module()

        logs = []

        # 1. 推荐选题
        topic_list = topics.recommend_topics(
            category=category if category else None,
            count=1
        )

        if not topic_list:
            return jsonify({'error': '未找到合适的选题', 'logs': logs}), 400

        topic = topic_list[0]
        logs.append({'step': '选题', 'status': 'success', 'msg': f'已选择: {topic.get("title", "")}'})

        # 2. 生成脚本
        script_result = scripts.generate_script(topic, platforms[0] if platforms else '抖音', 30)
        logs.append({'step': '脚本', 'status': 'success', 'msg': '脚本生成完成'})

        # 3. 处理素材
        images = []
        audio = None

        for m in material_paths:
            p = Path(m)
            if p.exists():
                ext = p.suffix.lower()
                if ext in ['.jpg', '.jpeg', '.png', '.webp']:
                    images.append(str(p))
                elif ext in ['.mp3', '.wav', '.aac', '.m4a']:
                    audio = str(p)

        # 如果没有用户素材，使用自动选择
        if not images:
            images = video.auto_select_materials(count=5)

        if not images:
            return jsonify({'error': '素材池为空，请先上传素材', 'logs': logs}), 400

        logs.append({'step': '素材', 'status': 'success', 'msg': f'已加载 {len(images)} 个素材'})

        # 4. 生成视频
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(config.OUTPUT_DIR / "临时" / f"video_{timestamp}.mp4")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 计算时长
        duration_per_image = 5
        total_duration = len(images) * duration_per_image

        success = video.create_video_from_images(
            images=images,
            output_path=output_path,
            duration_per_image=duration_per_image,
            transition="fade",
            bgm_path=audio
        )

        if not success:
            return jsonify({'error': '视频生成失败', 'logs': logs}), 500

        logs.append({'step': '剪辑', 'status': 'success', 'msg': '视频剪辑完成'})

        # 5. 添加字幕
        script_content = script_result.get('full_script', '')
        final_video = output_path.replace('.mp4', '_subtitled.mp4')

        sub_success, srt_path = subtitle.generate_subtitle_video(
            video_path=output_path,
            script=script_content,
            output_path=final_video,
            duration=total_duration,
            use_whisper=False
        )

        if not sub_success:
            final_video = output_path

        logs.append({'step': '字幕', 'status': 'success', 'msg': '字幕烧录完成'})

        # 6. 多平台导出
        works = []
        for p in platforms:
            platform_content = platform_mod.adapt_content(script_result, p)
            export_result = platform_mod.export_package(final_video, platform_content)

            if export_result['success']:
                works.append({
                    'platform': p,
                    'path': export_result['video_path'],
                    'output_dir': export_result['output_dir']
                })
                logs.append({'step': p, 'status': 'success', 'msg': f'{p} 投稿包已生成'})

        # 清理临时文件
        try:
            if Path(output_path).exists() and output_path != final_video:
                Path(output_path).unlink()
        except:
            pass

        return jsonify({
            'success': True,
            'topic': topic,
            'works': works,
            'logs': logs,
            'message': f'成功生成 {len(works)} 个平台的作品'
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/generate', methods=['POST'])
def api_generate():
    """一键生成视频（原有接口，保持兼容）"""
    try:
        data = request.get_json()
        category = data.get('category', '')
        platforms = data.get('platforms', ['抖音', '小红书', 'B站'])

        # 初始化模块
        topics = get_topics_module()
        scripts = get_script_module()
        video = get_video_module()
        subtitle = get_subtitle_module()
        platform_mod = get_platform_module()

        # 1. 推荐选题
        topic_list = topics.recommend_topics(
            category=category if category else None,
            count=1
        )

        if not topic_list:
            return jsonify({'error': '未找到合适的选题'}), 400

        topic = topic_list[0]

        # 2. 生成脚本
        script_result = scripts.generate_script(topic, platforms[0] if platforms else '抖音', 30)

        # 3. 获取素材
        images = video.auto_select_materials(count=5)
        if not images:
            return jsonify({'error': '素材池为空，请先放入素材到 assets/素材池_待剪辑/'}), 400

        # 4. 生成视频
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(config.OUTPUT_DIR / "临时" / f"video_{timestamp}.mp4")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        success = video.create_video_from_images(
            images=images,
            output_path=output_path,
            duration_per_image=5,
            transition="fade",
            bgm_path=None
        )

        if not success:
            return jsonify({'error': '视频生成失败'}), 500

        # 5. 添加字幕
        script_content = script_result.get('full_script', '')
        final_video = output_path.replace('.mp4', '_subtitled.mp4')

        sub_success, srt_path = subtitle.generate_subtitle_video(
            video_path=output_path,
            script=script_content,
            output_path=final_video,
            duration=30,
            use_whisper=False
        )

        if not sub_success:
            final_video = output_path

        # 6. 多平台导出
        works = []
        for p in platforms:
            platform_content = platform_mod.adapt_content(script_result, p)
            export_result = platform_mod.export_package(final_video, platform_content)

            if export_result['success']:
                works.append({
                    'platform': p,
                    'path': export_result['video_path'],
                    'output_dir': export_result['output_dir']
                })

        # 清理临时文件
        try:
            if Path(output_path).exists():
                Path(output_path).unlink()
            if Path(final_video).exists() and final_video != output_path:
                pass  # 保留最终视频
        except:
            pass

        return jsonify({
            'success': True,
            'topic': topic,
            'works': works,
            'message': f'成功生成 {len(works)} 个平台的作品'
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/cache/clear', methods=['POST'])
def api_cache_clear():
    """清空缓存"""
    try:
        topics = get_topics_module()
        topics.invalidate_cache()
        return jsonify({'success': True, 'message': '缓存已清空'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/library/expand', methods=['POST'])
def api_library_expand():
    """扩充选题库"""
    try:
        data = request.get_json()
        target = data.get('target', 1000)

        topics = get_topics_module()
        result = topics.expand_library(target)

        return jsonify({
            'success': True,
            'before': result.get('before', 0),
            'after': result.get('after', 0),
            'generated': result.get('generated', 0)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/file/open')
def api_file_open():
    """打开文件位置"""
    try:
        filepath = request.args.get('path', '')
        if filepath:
            path = Path(filepath)
            if path.exists():
                if sys.platform == 'win32':
                    os.startfile(str(path.parent))
                else:
                    os.system(f'open "{path.parent}"')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== 辅助函数 ====================

def format_size(size):
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


# ==================== 启动 ====================

def open_browser():
    """延迟打开浏览器"""
    def _open():
        time.sleep(1.5)
        webbrowser.open('http://127.0.0.1:5000')
    threading.Thread(target=_open, daemon=True).start()


def main():
    """主函数"""
    print("=" * 60)
    print("   Offline-ShortVideo-Agent Web 前端")
    print("   访问地址: http://127.0.0.1:5000")
    print("=" * 60)

    # 初始化数据库
    print("\n[初始化] 选题数据库...")
    init_database()

    # 检查选题库数量
    topics = get_topics_module()
    stats = topics.get_statistics()
    print(f"      选题库: {stats['total']} 条")

    # 确保素材目录存在
    Path(UPLOAD_TEMP_DIR).mkdir(parents=True, exist_ok=True)

    # 打开浏览器
    open_browser()

    # 启动Flask
    print("\n[启动] Web服务已启动，请访问 http://127.0.0.1:5000")
    print("按 Ctrl+C 停止服务\n")

    app.run(
        host='127.0.0.1',
        port=5000,
        debug=False,
        use_reloader=False
    )


if __name__ == '__main__':
    main()
