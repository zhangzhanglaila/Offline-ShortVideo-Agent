"""
video_renderer.py — Minimal Python video renderer using PIL + ffmpeg.
Narrative OS → frames → video file.

Usage:
    from engine.renderer.video_renderer import render_frames, frames_to_video
    render_frames(segments, out_dir="frames")
    frames_to_video(out_dir="frames", output="output.mp4")
"""

import os
import math
import subprocess
import json
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

WIDTH  = 1280
HEIGHT = 720
FPS    = 30


# ─── Math helpers ─────────────────────────────────────────────────────────────

def lerp(a, b, t):
    return a + (b - a) * max(0.0, min(1.0, t))


# ─── Image transforms ───────────────────────────────────────────────────────────

def apply_zoom(img, zoom):
    """Zoom toward center (crop + resize)."""
    if abs(zoom - 1.0) < 0.001:
        return img
    w, h = img.size
    new_w = max(1, int(w / zoom))
    new_h = max(1, int(h / zoom))
    left = (w - new_w) // 2
    top  = (h - new_h) // 2
    cropped = img.crop((left, top, left + new_w, top + new_h))
    return cropped.resize((w, h), Image.LANCZOS)


def apply_shake(img, intensity=6):
    """Random small affine jitter — mimics camera shake."""
    dx = int((math.sin(os.urandom(1)[0] / 255 * 2 * math.pi) - 0.5) * intensity)
    dy = int((math.cos(os.urandom(1)[0] / 255 * 2 * math.pi) - 0.5) * intensity)
    return img.transform(img.size, Image.AFFINE, (1, 0, dx, 0, 1, dy))


def apply_glow(img, factor=1.4):
    """Boost brightness to simulate glow."""
    enhancer = ImageEnhance.Brightness(img)
    return enhancer.enhance(factor)


def apply_edge_glow(draw, img_w, img_h, edge_color, edge_width):
    """Draw glowing edge highlight on border."""
    r, g, b = int(edge_color[1:3], 16), int(edge_color[3:5], 16), int(edge_color[5:7], 16)
    for i in range(edge_width):
        alpha = max(30, 200 - i * 40)
        draw.rectangle([i, i, img_w - 1 - i, img_h - 1 - i],
                       outline=(r, g, b, alpha), width=1)


# ─── Caption renderer ─────────────────────────────────────────────────────────

def hex_to_rgb(hex_color):
    """Convert #rrggbb → (r, g, b)."""
    if not hex_color or hex_color == 'None':
        return (200, 220, 255)
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def draw_caption(draw, text, style, x=WIDTH // 2, y=HEIGHT - 100):
    """Render centered caption text with optional glow."""
    try:
        font = ImageFont.truetype("arial.ttf", 42)
    except Exception:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 42)
        except Exception:
            font = ImageFont.load_default()

    text = text or ''
    # Use font.getbbox for Pillow 10+, fallback to getsize
    try:
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except Exception:
        tw, th = font.getsize(text)
    px = max(20, (WIDTH - tw) // 2)
    py = max(20, y - th)

    # Glow behind text
    if style.get('glow'):
        for blur_r in [6, 4, 2]:
            alpha = max(30, 120 - blur_r * 20)
            fill = hex_to_rgb(style['accent']) + (alpha,)
            for dx in (-blur_r, 0, blur_r):
                for dy in (-blur_r, 0, blur_r):
                    draw.text((px + dx, py + dy), text, font=font, fill=fill)

    draw.text((px, py), text, fill=hex_to_rgb(style.get('text', '#c8dcff')), font=font)


def draw_scene_label(draw, scene, scene_transition):
    """Top-left scene badge."""
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font = ImageFont.load_default()

    label = f"[{scene.upper()}]"
    if scene_transition and scene_transition != 'cut':
        label += f" → {scene_transition}"
    draw.text((16, 16), label, fill=(80, 100, 140), font=font)


# ─── Image cache ───────────────────────────────────────────────────────────────

_image_cache = {}
_seed_index   = [0]

# Deterministic seeds per segment type — ensures consistent imagery
_SCENE_SEEDS = {
    'intro':      10,
    'buildup':    20,
    'climax':     30,
    'release':    40,
    'idle':       50,
    'focus-arc':  60,
    'normal':     70,
}

_MODE_SEEDS = {
    'chaos':  80,
    'burst':  90,
    'focus': 100,
    'linger': 110,
    'normal': 120,
}

# Fallback built-in gradient backgrounds (no network needed)
_GRADIENT_BG = {
    'intro':    [(15, 5, 30), (5, 15, 40)],
    'buildup':  [(20, 10, 40), (40, 15, 60)],
    'climax':   [(30, 5, 50), (60, 20, 80)],
    'release':  [(10, 20, 40), (20, 40, 60)],
    'idle':     [(8, 8, 20), (15, 15, 30)],
    'normal':   [(10, 10, 25), (20, 20, 35)],
}


def _scene_seed(seg):
    s = seg.get('scene', 'normal')
    m = seg.get('mode', 'normal')
    base = _SCENE_SEEDS.get(s, 70) + _MODE_SEEDS.get(m, 120)
    # Mix in job index for variety
    idx = seg.get('jobIndex', 0) if isinstance(seg.get('jobIndex'), int) else hash(str(seg.get('jobId', ''))) % 200
    return base + (idx % 50)


def _load_segment_image(seg, width=WIDTH, height=HEIGHT):
    """
    Load a contextual image for this segment.
    Tries network source first, falls back to procedural gradient.
    """
    cache_key = (width, height, _scene_seed(seg))
    if cache_key in _image_cache:
        return _image_cache[cache_key].copy()

    scene = seg.get('scene', 'normal')
    mode  = seg.get('mode', 'normal')
    emphasis = seg.get('emphasis', 'none')

    # Pick a "mood" based on scene/mode
    try:
        # Try to fetch a real image from picsum (fast placeholder)
        seed = _scene_seed(seg)
        url  = f"https://picsum.photos/seed/{seed}/{width}"
        import urllib.request
        req  = urllib.request.Request(url, headers={'User-Agent': 'NarrativeOS/1.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            img = Image.open(resp).convert('RGB')
            img = img.resize((width, height), Image.LANCZOS)
            _image_cache[cache_key] = img
            return img.copy()
    except Exception:
        pass

    # Fallback: procedural gradient background
    colors = _GRADIENT_BG.get(scene, _GRADIENT_BG['normal'])
    img = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(img)
    c1, c2 = colors
    for y in range(height):
        t = y / height
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    _image_cache[cache_key] = img
    return img.copy()


# ─── Frame composition ─────────────────────────────────────────────────────────

def create_base_frame(style):
    """Create a base frame with background color."""
    bg = style.get('bg', '#0a0a0f')
    if bg.startswith('#'):
        bg = hex_to_rgb(bg)
    else:
        bg = (10, 10, 15)
    return Image.new('RGB', (WIDTH, HEIGHT), bg)


def render_segment_frame(seg, local_t):
    """
    Render a single frame for seg at normalized local_t (0-1).
    Returns PIL Image with real imagery + captions + effects.
    """
    cb   = seg.get('contentBinding', {})
    style = cb.get('style', {'bg': '#0a0a0f', 'text': '#c8dcff', 'accent': '#0088cc', 'glow': False})
    caption = cb.get('caption', seg.get('jobId') or seg.get('job_name') or '·')
    scene   = seg.get('scene', 'normal')
    scene_t = seg.get('sceneTransition')

    # Load contextual image (network or procedural fallback)
    img = _load_segment_image(seg, WIDTH, HEIGHT)

    # Shot curve evaluation
    curve = seg.get('shotCurve')
    zoom  = 1.0
    glow  = 0.0
    shake = False

    if curve:
        from engine.renderer import evaluate_shot_curve
        result = evaluate_shot_curve(curve, local_t)
        zoom  = result.get('zoom', 1.0)
        glow  = result.get('glow', 0.0)
        shake = result.get('shake', False)

    draw = ImageDraw.Draw(img)

    # Caption
    draw_caption(draw, caption, style)

    # Scene label
    draw_scene_label(draw, scene, scene_t)

    # Flash effect at start of segment
    if local_t < 0.05 and seg.get('flashColor') and seg.get('flashDur', 0) > 0:
        flash_alpha = max(30, int(180 * (1.0 - local_t / 0.05)))
        fl = hex_to_rgb(seg['flashColor']) + (flash_alpha,)
        draw.rectangle([0, 0, WIDTH - 1, HEIGHT - 1], fill=fl)

    # Edge glow
    if seg.get('edgeColor') and seg.get('edgeWidth'):
        apply_edge_glow(draw, WIDTH, HEIGHT, seg['edgeColor'], int(seg['edgeWidth']))

    # Apply transforms
    img = apply_zoom(img, zoom)

    if glow > 0.1:
        img = apply_glow(img, 1.0 + glow * 0.6)

    if shake:
        img = apply_shake(img, intensity=5 + glow * 8)

    return img


# ─── Batch frame rendering ─────────────────────────────────────────────────────

def render_frames(segments, out_dir="frames", total_ms=12000):
    """
    Render all segments to PNG frames.
    Each segment occupies its normalized [start, end] of the timeline.
    """
    os.makedirs(out_dir, exist_ok=True)

    # Collect all frame timestamps
    frames = []  # list of (seg, local_t, abs_frame_idx)
    for seg in segments:
        start = seg.get('start', 0.0)
        end   = seg.get('end', start + seg.get('duration', 0.01))
        dur   = max(end - start, 0.001)
        seg_frames = max(1, int(dur * total_ms / 1000 * FPS))

        for i in range(seg_frames):
            local_t = i / seg_frames if seg_frames > 1 else 0.0
            abs_idx = int(start * total_ms / 1000 * FPS) + i
            frames.append((seg, local_t, abs_idx))

    # Sort by absolute frame index to ensure correct order
    frames.sort(key=lambda x: x[2])

    print(f"  Rendering {len(frames)} frames...")

    for seg, local_t, abs_idx in frames:
        img = render_segment_frame(seg, local_t)
        img.save(f"{out_dir}/frame_{abs_idx:06d}.png", optimize=True)

    return len(frames)


# ─── Video encoding ────────────────────────────────────────────────────────────

def frames_to_video(frames_dir="frames", output="output.mp4", fps=FPS):
    """Encode frames directory → MP4 via ffmpeg."""
    if not os.path.exists(frames_dir):
        print(f"[ERROR] Frames directory '{frames_dir}' not found.")
        return

    count = len([f for f in os.listdir(frames_dir) if f.endswith('.png')])
    if count == 0:
        print(f"[ERROR] No frames found in '{frames_dir}'.")
        return

    print(f"  Encoding {count} frames → {output} ...")

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", f"{frames_dir}/frame_%06d.png",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        output,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] ffmpeg failed:\n{result.stderr[-500:]}")
        return

    print(f"  [OK] Video: {output}")


# ─── High-level API ───────────────────────────────────────────────────────────

def render_video(segments, output="output.mp4", total_ms=12000, frames_dir="frames"):
    """
    Full render pipeline: segments → frames → video file.
    """
    print(f"[Renderer] Rendering {len(segments)} segments...")
    n = render_frames(segments, out_dir=frames_dir, total_ms=total_ms)
    print(f"[Renderer] {n} frames rendered.")
    print(f"[Renderer] Encoding video...")
    frames_to_video(frames_dir=frames_dir, output=output)
    print(f"[OK] Output: {output}")
    return output
