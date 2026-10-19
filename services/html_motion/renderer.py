# -*- coding: utf-8 -*-
"""HTML → 逐帧截图 → ffmpeg 编码。

确定性关键：把页面里所有 Web Animations 暂停，然后按帧设置 `currentTime`。
这样 CSS `@keyframes`（含 delay / 缓动）可以被精确寻址，截图不会抖动。
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from core.utils.ffmpeg_runner import run_ffmpeg
from services.template.browser import BrowserManager

MAX_FRAMES = 320


class HtmlRenderError(RuntimeError):
    """HTML 渲染为视频失败。"""


_PAUSE_ALL = """() => {
  const anims = document.getAnimations ? document.getAnimations() : [];
  anims.forEach(a => { try { a.pause(); a.currentTime = 0; } catch (e) {} });
  return anims.length;
}"""

_SEEK = """(t) => {
  const anims = document.getAnimations ? document.getAnimations() : [];
  anims.forEach(a => { try { a.currentTime = t; } catch (e) {} });
}"""


async def render_html_to_video(
    html: str,
    output_dir: Path | str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
    duration: float = 5,
    on_progress: Optional[Callable[[str, float], None]] = None,
    keep_frames: bool = False,
) -> dict:
    """把一段 HTML 渲染成 MP4。

    Args:
        html: 自包含的 HTML 文档（内联 CSS/SVG，建议纯 CSS 动画）
        output_dir: 产物目录（frames/ 与 video.mp4 写在这里）
        on_progress: (消息, 0~1 进度) 回调

    Returns:
        {"video_path", "frames", "animations", "fps", "duration", "elapsed"}
    """
    started = time.time()
    output_dir = Path(output_dir)
    frames_dir = output_dir / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    total_frames = max(1, min(int(round(fps * duration)), MAX_FRAMES))

    def notify(msg: str, pct: float):
        if on_progress:
            try:
                on_progress(msg, max(0.0, min(1.0, pct)))
            except Exception:
                pass

    browser = await BrowserManager().get_browser()
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1,
    )
    animations = 0
    try:
        # 阻断一切外部请求：即使模型写了外链，也不会污染画面或拖慢渲染
        await context.route("**/*", lambda route: route.abort())

        page = await context.new_page()
        await page.set_content(html, wait_until="load")
        await page.wait_for_timeout(300)

        animations = await page.evaluate(_PAUSE_ALL)
        notify(f"检测到 {animations} 个动画，开始逐帧渲染", 0.15)

        step_report = max(1, total_frames // 10)
        for index in range(total_frames):
            timestamp_ms = round(index * 1000 / fps)
            await page.evaluate(_SEEK, timestamp_ms)
            # 用 JPEG 采集：1080×1920 的 PNG 编码约 440ms/帧，JPEG 只需 ~56ms
            await page.screenshot(
                path=str(frames_dir / f"frame_{index:05d}.jpg"),
                type="jpeg",
                quality=92,
            )
            if index % step_report == 0:
                notify(
                    f"渲染帧 {index + 1}/{total_frames}",
                    0.15 + 0.7 * (index / total_frames),
                )
    except Exception as exc:
        raise HtmlRenderError(f"逐帧截图失败: {exc}")
    finally:
        await context.close()

    video_path = output_dir / "video.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%05d.jpg"),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(video_path),
    ]
    notify("正在编码视频...", 0.9)
    try:
        await asyncio.to_thread(run_ffmpeg, cmd, 300)
    except Exception as exc:
        raise HtmlRenderError(f"ffmpeg 编码失败: {exc}")

    if not video_path.exists() or video_path.stat().st_size < 1024:
        raise HtmlRenderError("编码后的视频为空")

    if not keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)

    notify("完成", 1.0)
    return {
        "video_path": str(video_path),
        "frames": total_frames,
        "animations": animations,
        "fps": fps,
        "duration": duration,
        "elapsed": round(time.time() - started, 2),
    }
