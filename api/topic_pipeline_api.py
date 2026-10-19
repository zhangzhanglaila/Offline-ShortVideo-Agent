# -*- coding: utf-8 -*-
"""
题材全自动生成 API 路由 — 步进式流水线（复刻 ecom_api 模式）
端点: POST generate → PUT script → POST tts → POST render → GET status
支持 SSE 流式输出: POST /api/topic/generate/stream
"""
import sys
import os
import json
import time
import queue
import threading
import asyncio
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Request
from fastapi.responses import JSONResponse, StreamingResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.pipeline_helpers import (
    video_path_to_url,
    normalize_script_result,
    normalize_storyboard,
    ensure_storyboard_placeholders,
    generate_manga_frames,
    run_render_pipeline,
)
from core.db_init import get_db_path

router = APIRouter()

VOICES = [
    {"value": "zh-CN-XiaoxiaoNeural", "label": "晓晓（女声-年轻）"},
    {"value": "zh-CN-YunxiNeural", "label": "云希（男声-年轻）"},
    {"value": "zh-CN-YunyangNeural", "label": "云扬（男声-新闻）"},
    {"value": "zh-CN-Xiaoyi", "label": "小艺（女声-温柔）"},
    {"value": "zh-CN-Zhiyu", "label": "志宇（男声-成熟）"},
    {"value": "zh-CN-Xiaomo", "label": "小墨（女声-活力）"},
]


def _generate_topic(topic_keyword: str = "", category: str = "", platform: str = "抖音", stream_callback=None) -> dict:
    """从关键词/赛道生成选题目录。"""
    from core.dual_mode_module import get_dual_mode_generator
    generator = get_dual_mode_generator()

    if topic_keyword and topic_keyword.strip():
        return generator._generate_topic_from_keyword(topic_keyword.strip(), platform, stream_callback=stream_callback)
    if category and category.strip():
        topics_mod = generator.topics
        topics = topics_mod.recommend_topics(category=category, count=1, platform=platform)
        if topics:
            t = topics[0]
            return {"title": t.get("title", ""), "hook": t.get("hook", ""), "category": t.get("category", category), "tags": t.get("tags", "")}
    # fallback: random recommendation
    topics_mod = generator.topics
    topics = topics_mod.recommend_topics(count=1, platform=platform)
    if topics:
        t = topics[0]
        return {"title": t.get("title", ""), "hook": t.get("hook", ""), "category": t.get("category", ""), "tags": t.get("tags", "")}
    return {"title": topic_keyword or "热门话题", "hook": "", "category": category or "通用", "tags": ""}


# ==================== Step 1: 生成脚本 ====================
#
# 同步入口 POST /api/topic/generate 由 api/topic_video_api.py 统一提供
# （U1 统一化：写 ecom_videos，product_id 为 NULL）。
# 本模块保留前端步进式流水线所需的两类端点：
#   1) SSE 流式生成  POST /api/topic/generate/stream
#   2) 步进式操作     /api/topic/videos/{id}/{script,tts,materials,render,status}
# 两条链路共用同一批辅助函数，但落库表不同（topic_videos / ecom_videos）。


# ==================== Step 1b: 流式生成脚本 (SSE) ====================

@router.post("/api/topic/generate/stream")
async def api_topic_generate_stream(request: Request):
    """
    Step 1 SSE: 从题材关键词流式生成脚本 + 结构化分镜。
    支持实时推送进度: 选题 → LLM生成 → 分镜规范化 → 入库
    事件类型: progress / done / error
    """
    import sqlite3

    data = await request.json()

    topic_keyword = (data.get("topic_keyword") or "").strip()
    category = (data.get("category") or "").strip()
    platform = data.get("platform", "抖音")
    duration = data.get("duration", 30)
    orientation = data.get("orientation", "portrait")
    visual_style = data.get("visual_style", "manga")
    voice = data.get("voice", "zh-CN-XiaoxiaoNeural")

    log_queue = queue.Queue()
    result_queue = queue.Queue()

    def log_send(event_type, msg, extra=None):
        entry = {"type": event_type, "time": time.strftime("%H:%M:%S"), "msg": msg}
        if extra:
            entry.update(extra)
        log_queue.put_nowait(entry)

    def run_generation():
        try:
            if not topic_keyword and not category:
                log_send("error", "请输入题材关键词或选择赛道")
                return

            video_width, video_height = config.get_output_dimensions(orientation)

            # Step 1: 选题
            log_send("progress", "正在分析题材关键词...")
            def on_topic_stream(chunk):
                log_send("thinking", "", {"content": chunk})
            topic = _generate_topic(topic_keyword, category, platform, stream_callback=on_topic_stream)
            log_send("progress", f"选题完成: {topic.get('title', '')}")

            # Step 2: LLM 脚本生成
            log_send("progress", "正在调用 AI 生成脚本...")
            try:
                from core.script_module import generate_script
                # 流式回调：实时推送LLM输出到前端深度思考面板
                def on_stream_chunk(chunk):
                    log_send("thinking", "", {"content": chunk})
                script_result = generate_script(topic, platform, duration, stream_callback=on_stream_chunk)
            except Exception as e:
                log_send("error", f"脚本生成失败: {str(e)}")
                return

            if "error" in script_result:
                log_send("error", f"LLM 生成失败: {script_result['error']}")
                return

            script_result = normalize_script_result(script_result)

            if not script_result.get("full_script"):
                log_send("error", "LLM 返回空内容，请检查 API Key 是否有效或稍后重试")
                return

            log_send("progress", f"脚本生成完成 ({len(script_result.get('full_script', ''))} 字)")

            # Step 3: 分镜规范化
            log_send("progress", "正在结构化分镜头...")
            normalized_storyboard = normalize_storyboard(script_result, int(duration))
            log_send("progress", f"分镜完成，共 {len(normalized_storyboard)} 个场景")

            # Step 4: 入库
            log_send("progress", "正在保存到数据库...")
            chart_data = script_result.get("chart_data", [])
            diagram_layout = script_result.get("diagram_layout", "")
            conn = sqlite3.connect(get_db_path())
            try:
                cursor = conn.cursor()
                cursor.execute("BEGIN")
                cursor.execute("""
                    INSERT INTO topic_videos (topic_keyword, category, platform, style, script_content, storyboard,
                    status, pipeline_step, prompt_snapshot, llm_model, duration, animation_style, orientation,
                    video_width, video_height, visual_style, voice, chart_data, diagram_layout)
                    VALUES (?, ?, ?, ?, ?, ?, 'script_ready', 'script_ready', ?, ?, ?, 'manga_frame', ?, ?, ?, ?, ?, ?, ?)
                """, (
                    topic_keyword, category, platform, "manga",
                    script_result.get("full_script", ""),
                    json.dumps(normalized_storyboard, ensure_ascii=False),
                    json.dumps({"topic": topic, "platform": platform}, ensure_ascii=False),
                    config.OPENAI_MODEL,
                    duration,
                    orientation,
                    video_width,
                    video_height,
                    visual_style if visual_style in config.VISUAL_STYLES else "manga",
                    voice,
                    json.dumps(chart_data, ensure_ascii=False) if chart_data else "[]",
                    diagram_layout or "",
                ))
                video_id = cursor.lastrowid
                normalized_storyboard = ensure_storyboard_placeholders(video_id, normalized_storyboard, script_result.get("full_script", ""), table_name="topic_videos")
                cursor.execute("UPDATE topic_videos SET storyboard=? WHERE id=?", (json.dumps(normalized_storyboard, ensure_ascii=False), video_id))
                conn.commit()
            except Exception as e:
                conn.rollback()
                log_send("error", f"数据库写入失败: {str(e)}")
                return
            finally:
                conn.close()

            log_send("done", "脚本生成完毕", {
                "video_id": video_id,
                "script": {**script_result, "storyboard": normalized_storyboard, "chart_data": chart_data, "diagram_layout": diagram_layout},
                "topic": topic,
            })

        except Exception as e:
            log_send("error", f"生成过程异常: {str(e)}")

    thread = threading.Thread(target=run_generation)
    thread.start()

    async def event_generator():
        while True:
            try:
                entry = log_queue.get(timeout=0.15)
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            except queue.Empty:
                pass

            if not thread.is_alive():
                # Drain remaining
                while True:
                    try:
                        entry = log_queue.get_nowait()
                        yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        break
                break

            yield f"data: {json.dumps({'type': 'ping'})}\n\n"
            await asyncio.sleep(0.1)

        thread.join()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


# ==================== Step 2: 保存脚本 ====================

@router.put("/api/topic/videos/{video_id}/script")
async def api_topic_update_script(video_id: int, data: dict):
    """Step 2: 保存用户编辑后的脚本。"""
    import sqlite3

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT pipeline_step, duration FROM topic_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"error": "视频不存在"}, status_code=404)
        if row["pipeline_step"] not in ("script_ready", "script_edited"):
            return JSONResponse({"error": f"当前状态不允许编辑脚本: {row['pipeline_step']}"}, status_code=400)

        duration = row["duration"] or 30
        script_content = data.get("full_script", "")
        normalized_storyboard = normalize_storyboard(
            {"full_script": script_content, "storyboard": data.get("storyboard", [])},
            duration,
        )
        normalized_storyboard = ensure_storyboard_placeholders(video_id, normalized_storyboard, script_content, table_name="topic_videos")
        cursor.execute("""
            UPDATE topic_videos SET script_content=?, storyboard=?, pipeline_step='script_edited' WHERE id=?
        """, (
            script_content,
            json.dumps(normalized_storyboard, ensure_ascii=False),
            video_id,
        ))
        conn.commit()
        return JSONResponse({"success": True, "storyboard": normalized_storyboard})
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()


# ==================== Step 3: 生成 TTS ====================

@router.post("/api/topic/videos/{video_id}/tts")
async def api_topic_generate_tts(video_id: int, data: dict = None):
    """Step 3: 基于已保存脚本生成 TTS 配音。"""
    import sqlite3

    if data is None:
        data = {}

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT pipeline_step, script_content, duration, voice FROM topic_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"error": "视频不存在"}, status_code=404)
        if row["pipeline_step"] not in ("script_ready", "script_edited"):
            return JSONResponse({"error": f"当前状态不允许生成 TTS: {row['pipeline_step']}"}, status_code=400)

        script_content = row["script_content"]
        duration = row["duration"] or 30
        voice = data.get("voice") or row["voice"] or "zh-CN-XiaoxiaoNeural"
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)

    output_dir = config.OUTPUT_DIR / "topic"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = str(output_dir / f"tts_{video_id}.wav")

    scene_audio_urls = []
    try:
        from core.tts_module import generate_tts_from_script, TTSModule
        cursor = conn.cursor()
        cursor.execute("SELECT storyboard FROM topic_videos WHERE id = ?", (video_id,))
        sb_row = cursor.fetchone()
        try:
            storyboard = json.loads(sb_row["storyboard"]) if (sb_row and sb_row["storyboard"]) else []
        except Exception:
            storyboard = []
        scene_segments = [{"text": (s.get("subtitle") or s.get("title") or "").strip()} for s in storyboard if (s.get("subtitle") or s.get("title"))]
        if scene_segments:
            scene_dir = output_dir / f"tts_{video_id}_scenes"
            tts = TTSModule(voice)
            ok, scene_files = tts.generate_from_segments(scene_segments, str(scene_dir), voice=voice)
            if ok:
                scene_audio_urls = [f"/api/tts/audio/{Path(p).name}" for p in scene_files]
        success, audio_path = generate_tts_from_script(script_content, output_path, duration, voice)
    except Exception as e:
        return JSONResponse({"error": f"TTS 生成失败: {str(e)}"}, status_code=500)

    if not success or not audio_path:
        return JSONResponse({"error": "TTS 生成失败，请检查 TTS 配置"}, status_code=500)

    try:
        audio_duration = TTSModule.get_audio_duration(audio_path)
    except Exception:
        audio_duration = 0

    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE topic_videos SET tts_audio_path=?, pipeline_step='tts_ready', status='tts_ready', voice=? WHERE id=?
        """, (audio_path, voice, video_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": f"数据库更新失败: {str(e)}"}, status_code=500)
    finally:
        conn.close()

    audio_filename = Path(audio_path).name
    return JSONResponse({
        "success": True,
        "audio_url": f"/api/tts/audio/{audio_filename}",
        "audio_path": audio_path,
        "duration": audio_duration,
        "scene_audio_urls": scene_audio_urls,
    })


# ==================== Step 3.5: 上传素材 ====================

@router.post("/api/topic/videos/{video_id}/materials")
async def api_topic_upload_material(
    video_id: int,
    scene_index: int = 0,
    file: UploadFile = File(...),
):
    """上传分镜素材（multipart/form-data）。"""
    import sqlite3

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT pipeline_step, materials_json FROM topic_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"error": "视频不存在"}, status_code=404)
        if row["pipeline_step"] not in ("script_ready", "script_edited", "tts_ready"):
            return JSONResponse({"error": f"当前状态不允许上传素材: {row['pipeline_step']}"}, status_code=400)
    except Exception as e:
        conn.close()
        return JSONResponse({"error": str(e)}, status_code=500)

    save_dir = config.OUTPUT_DIR / "topic" / f"video_{video_id}"
    save_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"scene_{scene_index}_{file.filename}"
    save_path = save_dir / safe_name

    contents = await file.read()
    save_path.write_bytes(contents)

    try:
        materials = {}
        if row["materials_json"]:
            try:
                materials = json.loads(row["materials_json"])
            except json.JSONDecodeError:
                pass
        materials[str(scene_index)] = str(save_path)

        cursor = conn.cursor()
        cursor.execute("UPDATE topic_videos SET materials_json=? WHERE id=?",
                       (json.dumps(materials, ensure_ascii=False), video_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": f"数据库更新失败: {str(e)}"}, status_code=500)
    finally:
        conn.close()

    return JSONResponse({
        "success": True,
        "path": str(save_path),
        "scene_index": scene_index,
        "url": video_path_to_url(str(save_path)),
    })


# ==================== Step 4: 渲染 ====================

@router.post("/api/topic/videos/{video_id}/render")
async def api_topic_render_video(video_id: int, data: dict = None):
    """Step 4: 启动视频渲染管线（后台线程）。"""
    import threading
    import sqlite3

    if data is None:
        data = {}

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT pipeline_step FROM topic_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"error": "视频不存在"}, status_code=404)
        if row["pipeline_step"] != "tts_ready":
            return JSONResponse({"error": f"当前状态不允许渲染: {row['pipeline_step']}"}, status_code=400)

        cursor.execute("UPDATE topic_videos SET pipeline_step='rendering', status='generating' WHERE id=?", (video_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()

    orientation = (data or {}).get("orientation")
    visual_style = (data or {}).get("visual_style")
    if orientation in ("portrait", "landscape") or (visual_style and visual_style in config.VISUAL_STYLES):
        try:
            conn = sqlite3.connect(get_db_path())
            sets, params = [], []
            if visual_style in config.VISUAL_STYLES:
                sets.append("visual_style=?")
                params.append(visual_style)
            if orientation in ("portrait", "landscape"):
                w, h = config.get_output_dimensions(orientation)
                sets.append("orientation=?")
                params.append(orientation)
                sets.append("video_width=?")
                params.append(w)
                sets.append("video_height=?")
                params.append(h)
            params.append(video_id)
            conn.execute(f"UPDATE topic_videos SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
            conn.close()
        except Exception:
            pass

    thread = threading.Thread(target=run_render_pipeline, args=(video_id, "topic_videos"), daemon=True)
    thread.start()

    return JSONResponse({"success": True, "video_id": video_id})


@router.post("/api/topic/videos/{video_id}/retry-render")
async def api_topic_retry_render(video_id: int, data: dict = None):
    """Step 4 重试: 从失败状态回退到 tts_ready 后重新触发渲染，保留脚本和配音。"""
    import threading
    import sqlite3

    if data is None:
        data = {}

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT pipeline_step, tts_audio_path FROM topic_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        if not row:
            return JSONResponse({"error": "视频不存在"}, status_code=404)
        if row["pipeline_step"] != "failed":
            return JSONResponse({"error": f"当前状态不允许重试: {row['pipeline_step']}"}, status_code=400)
        if not row["tts_audio_path"]:
            return JSONResponse({"error": "配音文件丢失，请重新生成脚本和配音"}, status_code=400)

        cursor.execute("UPDATE topic_videos SET pipeline_step='rendering', status='generating', notes='' WHERE id=?", (video_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()

    orientation = (data or {}).get("orientation")
    visual_style = (data or {}).get("visual_style")
    if orientation in ("portrait", "landscape") or (visual_style and visual_style in config.VISUAL_STYLES):
        try:
            conn = sqlite3.connect(get_db_path())
            sets, params = [], []
            if visual_style in config.VISUAL_STYLES:
                sets.append("visual_style=?")
                params.append(visual_style)
            if orientation in ("portrait", "landscape"):
                w, h = config.get_output_dimensions(orientation)
                sets.append("orientation=?")
                params.append(orientation)
                sets.append("video_width=?")
                params.append(w)
                sets.append("video_height=?")
                params.append(h)
            params.append(video_id)
            conn.execute(f"UPDATE topic_videos SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
            conn.close()
        except Exception:
            pass

    thread = threading.Thread(target=run_render_pipeline, args=(video_id, "topic_videos"), daemon=True)
    thread.start()

    return JSONResponse({"success": True, "video_id": video_id})


# ==================== 状态轮询 ====================

@router.get("/api/topic/videos/{video_id}/status")
async def api_topic_video_status(video_id: int):
    """状态轮询端点（含 pipeline_step）。"""
    try:
        import sqlite3
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT status, video_path, notes, pipeline_step, tts_audio_path FROM topic_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return JSONResponse({"error": "视频不存在"}, status_code=404)

        d = dict(row)
        resp = {
            "status": d["status"],
            "pipeline_step": d.get("pipeline_step") or d["status"],
        }
        if d.get("video_path"):
            resp["video_url"] = video_path_to_url(d["video_path"])
            resp["video_path"] = d["video_path"]
        if d.get("tts_audio_path"):
            resp["audio_url"] = f"/api/tts/audio/{Path(d['tts_audio_path']).name}"
        if d.get("notes") and d["status"] == "failed":
            resp["error"] = d["notes"]
        return JSONResponse(resp)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ==================== 视频详情 / 列表 / 删除 ====================

@router.get("/api/topic/videos/{video_id}")
async def api_topic_video_detail(video_id: int):
    """视频详情。"""
    try:
        import sqlite3
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM topic_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return JSONResponse({"error": "视频不存在"}, status_code=404)

        d = dict(row)
        for key in ("storyboard", "chart_data"):
            if d.get(key) and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except json.JSONDecodeError:
                    d[key] = []
        d["video_url"] = video_path_to_url(d.get("video_path", ""))
        if d.get("tts_audio_path"):
            d["audio_url"] = f"/api/tts/audio/{Path(d['tts_audio_path']).name}"
        return JSONResponse(d)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/topic/videos")
async def api_topic_videos(
    page: int = 1,
    page_size: int = 20,
):
    """题材视频列表。"""
    try:
        import sqlite3
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM topic_videos")
        total = cursor.fetchone()[0]

        offset = (page - 1) * page_size
        cursor.execute("SELECT * FROM topic_videos ORDER BY created_at DESC LIMIT ? OFFSET ?", (page_size, offset))

        items = []
        for row in cursor.fetchall():
            d = dict(row)
            for key in ("storyboard", "chart_data"):
                if d.get(key) and isinstance(d[key], str):
                    try:
                        d[key] = json.loads(d[key])
                    except json.JSONDecodeError:
                        d[key] = [] if key == "chart_data" else d.get(key, [])
            d["video_url"] = video_path_to_url(d.get("video_path", ""))
            items.append(d)

        conn.close()
        return JSONResponse({"items": items, "total": total, "page": page, "page_size": page_size})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/topic/videos/{video_id}")
async def api_topic_delete_video(video_id: int):
    """删除单个视频。"""
    try:
        import sqlite3
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT video_path, thumbnail_path FROM topic_videos WHERE id = ?", (video_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return JSONResponse({"error": "视频不存在"}, status_code=404)

        video_path = row["video_path"] or ""
        thumb_path = row["thumbnail_path"] or ""

        cursor.execute("DELETE FROM topic_videos WHERE id = ?", (video_id,))
        conn.commit()
        conn.close()

        for fpath in (video_path, thumb_path):
            if fpath and Path(fpath).exists():
                try:
                    Path(fpath).unlink()
                except OSError:
                    pass

        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/topic/videos/all")
async def api_topic_delete_all_videos():
    """删除全部题材视频（数据库记录 + 文件系统文件）。"""
    try:
        import sqlite3
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT video_path, thumbnail_path, tts_audio_path FROM topic_videos")
        rows = cursor.fetchall()

        cursor.execute("DELETE FROM topic_videos")
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()

        for row in rows:
            for fpath in (row["video_path"], row["thumbnail_path"], row["tts_audio_path"]):
                if fpath and Path(fpath).exists():
                    try:
                        Path(fpath).unlink()
                    except OSError:
                        pass

        return JSONResponse({"success": True, "deleted": deleted_count})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ==================== 元数据 ====================

@router.get("/api/topic/meta")
async def api_topic_meta():
    """返回题材模块元数据。"""
    return JSONResponse({
        "visual_styles": {k: {"name_cn": v["name_cn"], "paper_color": v["paper_color"], "accent_red": v["accent_red"], "text_c": v["text_c"]} for k, v in config.VISUAL_STYLES.items()},
        "categories": list(config.CATEGORIES.keys()),
        "platforms": ["抖音", "小红书", "B站"],
        "voices": VOICES,
    })
