# -*- coding: utf-8 -*-
"""
素材管理API路由
"""
import sys
import os
import json
import shutil
import subprocess
import threading
from pathlib import Path
from datetime import datetime
from typing import List
from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()

UPLOAD_TEMP_DIR = config.MATERIAL_DIR
THUMBNAILS_DIR = config.THUMBNAILS_DIR


def generate_video_thumbnail(video_path, output_path=None):
    """使用ffmpeg生成视频缩略图"""
    if output_path is None:
        output_path = THUMBNAILS_DIR / (Path(video_path).stem + '_thumb.jpg')
    else:
        output_path = Path(output_path)

    try:
        cmd = [
            'ffmpeg', '-y', '-i', str(video_path),
            '-ss', '00:00:01', '-vframes', '1',
            '-vf', 'scale=320:180',
            '-q:v', '2',
            str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode == 0 and output_path.exists():
            return str(output_path)
    except subprocess.TimeoutExpired:
        print(f"[缩略图] 超时: {video_path}")
    except Exception as e:
        print(f"[缩略图] 失败: {e}")
    return None


def transcode_video_for_web(video_path):
    """转码视频为浏览器兼容的H.264格式"""
    original = Path(video_path)
    if not original.exists():
        return video_path

    try:
        probe = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', str(original)],
            capture_output=True, text=True, timeout=15
        )
        streams = json.loads(probe.stdout).get('streams', [])
        for s in streams:
            if s.get('codec_type') == 'video':
                codec = s.get('codec_name', '')
                if codec in ('h264', 'libx264') and 'hevc' not in original.name.lower():
                    return video_path
    except Exception:
        pass

    temp_output = original.parent / (original.stem + '_web.mp4')
    if temp_output.exists():
        return str(temp_output)

    try:
        cmd = [
            'ffmpeg', '-y', '-i', str(original),
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            str(temp_output)
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode == 0 and temp_output.exists():
            return str(temp_output)
    except subprocess.TimeoutExpired:
        print(f"[转码] 超时: {original.name}")
    except Exception as e:
        print(f"[转码] 失败: {e}")

    return video_path


def format_size(size: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


@router.get("/api/materials")
async def api_materials():
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

                    thumb_name = f.stem + '_thumb.jpg'
                    thumb_path = THUMBNAILS_DIR / thumb_name
                    has_thumb = thumb_path.exists()

                    if mtype == 'video' and not has_thumb:
                        def gen(fname=f.name, fpath=str(f)):
                            print(f"[缩略图] 后台生成: {fname}")
                            thumb = generate_video_thumbnail(fpath)
                            if thumb:
                                print(f"[缩略图] 完成: {Path(thumb).name}")
                            else:
                                print(f"[缩略图] 失败: {fname}")
                        threading.Thread(target=gen, daemon=True).start()

                    materials.append({
                        'name': f.name,
                        'path': str(f),
                        'type': mtype,
                        'size': f.stat().st_size,
                        'size_str': format_size(f.stat().st_size),
                        'date': datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M'),
                        'has_thumb': has_thumb,
                        'thumb_name': thumb_name if has_thumb else None
                    })

        materials.sort(key=lambda x: x['date'], reverse=True)
        return JSONResponse({'materials': materials})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({'error': str(e)}, status_code=500)


@router.post("/api/materials/upload")
async def api_materials_upload(file: List[UploadFile] = File(...)):
    """上传素材文件"""
    try:
        uploaded = []
        skipped = []

        for f in file:
            if f.filename:
                save_path = Path(UPLOAD_TEMP_DIR) / f.filename
                if save_path.exists():
                    skipped.append(f.filename)
                    print(f"[上传] 文件已存在: {f.filename}")
                    continue

                contents = await f.read()
                save_path.write_bytes(contents)
                print(f"[上传] 保存成功: {f.filename}")

                ext = Path(f.filename).suffix.lower()
                if ext in ['.mp4', '.avi', '.mov', '.mkv']:
                    from .system_api import push_log
                    push_log(f"🎬 开始处理: {f.filename}", 'info')

                    fname = f.filename
                    fpath = str(save_path)

                    def process_video():
                        try:
                            print(f"[上传] 处理视频: {fname}")
                            thumb = generate_video_thumbnail(fpath)
                            if thumb:
                                from .system_api import push_log
                                push_log(f"🖼️ 缩略图完成: {fname}", 'success')
                            web_path = transcode_video_for_web(fpath)
                            if web_path != fpath:
                                try:
                                    shutil.move(web_path, fpath)
                                    print(f"[上传] 已转码: {fname}")
                                    push_log(f"✅ 转码完成: {fname}", 'success')
                                except Exception as e:
                                    print(f"[上传] 移动转码文件失败: {e}")
                            push_log(f"✅ 已上传: {fname}", 'success')
                        except Exception as e:
                            print(f"[上传] 处理异常: {e}")
                            push_log(f"❌ 处理异常: {e}", 'error')

                    threading.Thread(target=process_video, daemon=True).start()
                else:
                    from .system_api import push_log
                    push_log(f"✅ 已上传: {f.filename}", 'success')

                uploaded.append(f.filename)

        return JSONResponse({
            'success': True,
            'uploaded': uploaded,
            'skipped': skipped,
            'count': len(uploaded)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({'error': str(e)}, status_code=500)


@router.delete("/api/materials/{filename}")
async def api_materials_delete(filename: str):
    """删除单个素材"""
    try:
        file_path = Path(UPLOAD_TEMP_DIR) / filename
        if file_path.exists():
            file_path.unlink()
            thumb_name = Path(filename).stem + '_thumb.jpg'
            thumb_path = THUMBNAILS_DIR / thumb_name
            if thumb_path.exists():
                thumb_path.unlink()
            return JSONResponse({'success': True})
        return JSONResponse({'error': '文件不存在'}, status_code=404)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.post("/api/materials/clear")
async def api_materials_clear():
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
                        thumb_name = f.stem + '_thumb.jpg'
                        thumb_path = THUMBNAILS_DIR / thumb_name
                        if thumb_path.exists():
                            thumb_path.unlink()

        if THUMBNAILS_DIR.exists():
            for tf in THUMBNAILS_DIR.iterdir():
                if tf.is_file():
                    tf.unlink()

        return JSONResponse({'success': True, 'cleared': count})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)
