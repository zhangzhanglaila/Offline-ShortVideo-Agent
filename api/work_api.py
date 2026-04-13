# -*- coding: utf-8 -*-
"""
作品API路由
"""
import sys
import os
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()


@router.get("/api/works")
async def api_works():
    """获取已生成作品"""
    try:
        works = []
        output_dir = Path(config.OUTPUT_DIR)

        if output_dir.exists():
            for platform_dir in output_dir.iterdir():
                if platform_dir.is_dir():
                    for video_file in platform_dir.rglob('*.mp4'):
                        info_file = video_file.with_suffix('.txt')
                        title = video_file.stem

                        if info_file.exists():
                            try:
                                content = info_file.read_text(encoding='utf-8')
                                for line in content.split('\n'):
                                    if line.startswith('【标题】'):
                                        title = line.replace('【标题】', '').strip()
                                        break
                            except Exception:
                                pass

                        works.append({
                            'name': video_file.name,
                            'path': str(video_file),
                            'platform': platform_dir.name,
                            'title': title,
                            'date': datetime.fromtimestamp(video_file.stat().st_mtime).strftime('%Y-%m-%d')
                        })

        works.sort(key=lambda x: x['date'], reverse=True)
        return JSONResponse({'works': works})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)
