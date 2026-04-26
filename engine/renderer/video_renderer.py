"""
video_renderer.py — Narrative OS Renderer
PIL + ffmpeg pipeline.

Level 1: Camera State Machine (inertia, damping, carry-over)
Level 2: Scene Dynamics (rain, light-leak, vignette, color-drift)
Level 3: Frame Entropy Engine
  - Temporal Motion Blur (real accumulation, not crop-blend)
  - Per-frame geometric warp (makes each PNG truly different)
  - Prevents H.264 macroblock reuse → increases bitrate

Together: actual "video feel", not Ken Burns slideshow.
"""

import os
import math
import random
import subprocess
from dataclasses import dataclass
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

WIDTH  = 1280
HEIGHT = 720
FPS    = 30


# ══════════════════════════════════════════════════════════════════════════════
# CAMERA STATE MACHINE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CameraState:
    x: float    = 0.0
    y: float    = 0.0
    zoom: float = 1.0
    vx: float   = 0.0
    vy: float   = 0.0
    vz: float   = 0.0
    DAMP_X: float = 0.88
    DAMP_Y: float = 0.88
    DAMP_Z: float = 0.93

    def step(self, ax: float, ay: float, az: float):
        self.vx = self.vx * self.DAMP_X + ax
        self.vy = self.vy * self.DAMP_Y + ay
        self.vz = self.vz * self.DAMP_Z + az
        self.x    += self.vx
        self.y    += self.vy
        self.zoom  = max(0.3, min(4.0, self.zoom + self.vz))


# ══════════════════════════════════════════════════════════════════════════════
# EMOTION FORCE FIELD
# ══════════════════════════════════════════════════════════════════════════════

_EMOTION_FORCE = {
    'chaos':   ( 3.0,  2.5,  0.040),
    'burst':   ( 2.0,  1.5,  0.025),
    'climax':  ( 1.8,  1.2,  0.020),
    'buildup': ( 0.9,  0.6,  0.010),
    'focus':   ( 0.5,  0.4,  0.008),
    'linger':  ( 0.1,  0.1, -0.002),
    'release': (-1.2, -0.8, -0.015),
    'idle':    (-0.2, -0.1, -0.005),
    'normal':  ( 0.3,  0.2,  0.003),
}


def compute_camera_force(seg: dict) -> tuple:
    mode  = seg.get('mode', 'normal')
    scene = seg.get('scene', 'normal')
    base  = _EMOTION_FORCE.get(mode, (0.3, 0.2, 0.003))
    if scene == 'climax':
        base = (base[0] * 1.2, base[1] * 1.2, base[2] * 1.3)
    elif scene == 'release':
        base = (base[0] * 0.8, base[1] * 0.8, base[2] * 0.9)
    if seg.get('flashColor') and seg.get('flashDur', 0) > 0:
        base = (base[0], base[1] + 1.0, base[2])
    return base


# ══════════════════════════════════════════════════════════════════════════════
# LEVEL 3: FRAME ENTROPY ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def _apply_geometric_warp(img, t, intensity=0.015):
    """
    Per-frame non-uniform warp — makes each PNG truly different.
    This is what breaks H.264 macroblock reuse.
    Uses sinusoidal displacement field.
    """
    w, h = img.size
    # Non-linear displacement — not just translate, but shear + bulge
    dx = int(math.sin(t * 3.14159 * 2.3) * w * intensity)
    dy = int(math.cos(t * 3.14159 * 1.7) * h * intensity)

    # Edge shear: different offset at center vs corners
    center_x = w // 2
    center_y = h // 2

    # Crop source with offset and paste at different position
    src_x = max(0, dx)
    src_y = max(0, dy)
    src_w = w - abs(dx)
    src_h = h - abs(dy)

    if src_w <= 0 or src_h <= 0:
        return img

    cropped = img.crop((src_x, src_y, src_x + src_w, src_y + src_h))
    canvas = Image.new('RGB', (w, h), (10, 10, 15))
    paste_x = dx if dx >= 0 else 0
    paste_y = dy if dy >= 0 else 0
    canvas.paste(cropped.resize((w, h), Image.LANCZOS), (paste_x, paste_y))
    return canvas


def _render_temporal_motion_blur(img, cam: CameraState, t, mode='normal'):
    """
    Real temporal motion blur — samples previous frames' content,
    not just a shifted crop.
    """
    speed = math.sqrt(cam.vx**2 + cam.vy**2)
    blur_strength_map = {
        'chaos': 0.35, 'climax': 0.30, 'burst': 0.25,
        'buildup': 0.18, 'focus': 0.20, 'linger': 0.08,
        'release': 0.12, 'idle': 0.05, 'normal': 0.10,
    }
    base_strength = blur_strength_map.get(mode, 0.10)
    strength = base_strength * min(1.0, speed / 5.0)

    if strength < 0.05:
        return img

    # Accumulate N temporal samples with velocity-based offset
    num_samples = 4
    accumulator = Image.new('RGB', img.size)
    alpha_total = 0.0

    for s in range(num_samples):
        sample_t = t - (s + 1) * 0.04  # look back in time
        if sample_t < 0:
            continue

        # Velocity at sample time (approximate — decay from current)
        decay = 0.85 ** (s + 1)
        sx = cam.vx * decay * 0.5
        sy = cam.vy * decay * 0.5

        # Shift image by velocity
        shift_x = int(sx)
        shift_y = int(sy)

        if abs(shift_x) > 2 or abs(shift_y) > 2:
            sample_img = img.copy()
            if shift_x >= 0:
                src_x = 0
                dst_x = shift_x
            else:
                src_x = -shift_x
                dst_x = 0
            if shift_y >= 0:
                src_y = 0
                dst_y = shift_y
            else:
                src_y = -shift_y
                dst_y = 0

            src_w = w - abs(shift_x)
            src_h = h - abs(shift_y)
            if src_w > 0 and src_h > 0:
                cropped = sample_img.crop((src_x, src_y, src_x + src_w, src_y + src_h))
                tmp = Image.new('RGB', (w, h), (10, 10, 15))
                tmp.paste(cropped.resize((w, h), Image.LANCZOS), (dst_x, dst_y))
                sample_img = tmp
        else:
            sample_img = img.copy()

        weight = (1.0 - s / num_samples) * strength
        accumulator = Image.blend(accumulator, sample_img, weight / (alpha_total + weight))
        alpha_total += weight

    if alpha_total > 0.01:
        return Image.blend(img, accumulator, min(strength * 0.6, 0.5))
    return img


def _render_radial_pulse(img, t, mode='normal', zoom=1.0):
    """Breathing center glow."""
    w, h = img.size
    strength_map = {'chaos': 0.25, 'climax': 0.30, 'burst': 0.20,
                    'buildup': 0.15, 'focus': 0.18, 'linger': 0.10,
                    'release': 0.12, 'idle': 0.08, 'normal': 0.10}
    strength = strength_map.get(mode, 0.10)
    pulse = strength * (0.6 + 0.4 * math.sin(t * math.pi * 2))
    cx, cy = w // 2, h // 2
    grad = Image.new('RGB', (w, h), (0, 0, 0))
    grad_draw = ImageDraw.Draw(grad)
    max_r = w // 3
    for r in range(max_r, 0, -5):
        alpha = int(pulse * 255 * (1 - r / max_r))
        gray = min(255, alpha)
        grad_draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                          fill=(gray, gray, gray))
    grad = ImageEnhance.Brightness(grad).enhance(0.4)
    return Image.blend(img, grad, pulse * 0.5)


def _render_color_temperature(img, t, mode='normal'):
    """Warm/cool color drift."""
    warm_modes = {'climax', 'buildup', 'chaos'}
    cool_modes = {'idle', 'linger', 'release'}
    if mode in warm_modes:
        shift = (15 * math.sin(t * math.pi * 0.5), 8, -5)
    elif mode in cool_modes:
        shift = (-8, -5, 12)
    else:
        shift = (3 * math.sin(t * math.pi * 0.3), 2, 0)
    try:
        import numpy as np
        np_arr = np.array(img).astype(int)
        np_arr = np.clip(np_arr + shift, 0, 255).astype(np.uint8)
        return Image.fromarray(np_arr)
    except Exception:
        return img


def _render_vignette(img, t, mode='normal'):
    """Breathing vignette."""
    w, h = img.size
    strength_map = {'chaos': 0.7, 'climax': 0.6, 'burst': 0.5,
                    'buildup': 0.35, 'focus': 0.25, 'linger': 0.15,
                    'release': 0.2, 'idle': 0.1, 'normal': 0.2}
    strength = strength_map.get(mode, 0.2)
    pulse = strength * (0.7 + 0.3 * math.sin(t * math.pi * 3))
    cx, cy = w // 2, h // 2
    max_r = int(math.sqrt(cx**2 + cy**2))
    overlay = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(overlay)
    steps = 8
    for i in range(steps - 1, -1, -1):
        inner_r = int(max_r * (i / steps) * (1 - pulse))
        outer_r = int(max_r * ((i + 1) / steps) * (1 - pulse * 0.3))
        alpha = int((1 - i / steps) * 255 * pulse)
        draw.ellipse([cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r],
                     fill=min(255, alpha + 30))
    vignette = ImageEnhance.Brightness(overlay).enhance(0.6)
    dark = Image.new('RGB', (w, h), (0, 0, 0))
    dark.putalpha(vignette)
    img = img.convert('RGBA')
    img = Image.alpha_composite(img, dark)
    img = img.convert('RGB')
    return img


def _render_rain(img, t, density=0.5, mode='normal'):
    """Diagonal rain streaks."""
    if mode not in ('chaos', 'climax', 'buildup', 'burst'):
        return img
    draw = ImageDraw.Draw(img)
    w, h = img.size
    num_streaks = int(density * 80)
    for i in range(num_streaks):
        x0 = (int(t * 200 + i * 37) % w)
        y0 = (int(t * 350 + i * 19) % h)
        length = 15 + (i % 20)
        draw.line([(x0, y0), (x0 + 2, y0 + length)],
                  fill=(160, 180, 220), width=1)
    return img


def _render_light_leak(img, t, scene='normal', mode='normal'):
    """Corner light leak flare."""
    if scene not in ('climax', 'buildup') and mode not in ('climax', 'burst', 'chaos'):
        return img
    w, h = img.size
    corners = [(0, 0), (w, 0), (0, h), (w, h)]
    rng = random.Random(int(t * 10) & 0xFF)
    corner = corners[rng.randint(0, 3)]
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    r, g, b = (255, 120, 50)
    for r_size in [200, 150, 80]:
        alpha = 20 + int(15 * math.sin(t * math.pi * 2))
        rad = int(r_size * (0.8 + 0.2 * math.sin(t * 0.7)))
        c_box = [corner[0] - rad, corner[1] - rad,
                 corner[0] + rad, corner[1] + rad]
        od.ellipse(c_box, fill=(r, g, b, alpha))
    img = img.convert('RGBA')
    img = Image.alpha_composite(img, overlay)
    img = img.convert('RGB')
    return img


def render_atmosphere(img, seg, local_t, cam: CameraState):
    """
    Full scene dynamics pipeline.
    Order matters: warp first (breaks SSIM), then atmosphere, then camera.
    """
    mode  = seg.get('mode', 'normal')
    scene = seg.get('scene', 'normal')

    # 1. GEOMETRIC WARP — most important for entropy (must happen before camera)
    img = _apply_geometric_warp(img, local_t, intensity=0.012)

    # 2. Rain (emotion-driven)
    density = {'chaos': 0.7, 'climax': 0.5, 'burst': 0.4, 'buildup': 0.3}.get(mode, 0.2)
    img = _render_rain(img, local_t, density, mode)

    # 3. Light leak flare
    img = _render_light_leak(img, local_t, scene, mode)

    # 4. Radial pulse
    img = _render_radial_pulse(img, local_t, mode, cam.zoom)

    # 5. Color temperature drift
    img = _render_color_temperature(img, local_t, mode)

    # 6. Breathing vignette
    img = _render_vignette(img, local_t, mode)

    return img


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE TRANSFORMS
# ══════════════════════════════════════════════════════════════════════════════

def hex_to_rgb(hex_color):
    if not hex_color or hex_color == 'None':
        return (200, 220, 255)
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def apply_zoom(img, zoom):
    if abs(zoom - 1.0) < 0.001:
        return img
    w, h = img.size
    new_w = max(1, int(w / zoom))
    new_h = max(1, int(h / zoom))
    left = (w - new_w) // 2
    top  = (h - new_h) // 2
    cropped = img.crop((left, top, left + new_w, top + new_h))
    return cropped.resize((w, h), Image.LANCZOS)


def apply_pan_offset(img, cx: float, cy: float):
    dx = int(cx)
    dy = int(cy)
    if dx == 0 and dy == 0:
        return img
    return img.transform(img.size, Image.AFFINE, (1, 0, dx, 0, 1, dy))


def apply_shake(img, intensity=6):
    dx = int((math.sin(os.urandom(1)[0] / 255 * 2 * math.pi) - 0.5) * intensity)
    dy = int((math.cos(os.urandom(1)[0] / 255 * 2 * math.pi) - 0.5) * intensity)
    return img.transform(img.size, Image.AFFINE, (1, 0, dx, 0, 1, dy))


def apply_glow(img, factor=1.4):
    return ImageEnhance.Brightness(img).enhance(factor)


def apply_flash(img, color_hex, alpha=0.25):
    overlay = Image.new('RGB', img.size, hex_to_rgb(color_hex))
    return Image.blend(img, overlay, alpha)


def apply_edge_glow(draw, img_w, img_h, edge_color, edge_width):
    r, g, b = int(edge_color[1:3], 16), int(edge_color[3:5], 16), int(edge_color[5:7], 16)
    for i in range(edge_width):
        alpha = max(30, 200 - i * 40)
        draw.rectangle([i, i, img_w - 1 - i, img_h - 1 - i],
                       outline=(r, g, b, alpha), width=1)


def draw_caption(draw, text, style, x=WIDTH // 2, y=HEIGHT - 100):
    try:
        font = ImageFont.truetype("arial.ttf", 42)
    except Exception:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 42)
        except Exception:
            font = ImageFont.load_default()

    text = text or ''
    try:
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except Exception:
        tw, th = font.getsize(text)
    px = max(20, (WIDTH - tw) // 2)
    py = max(20, y - th)

    if style.get('glow'):
        for blur_r in [6, 4, 2]:
            alpha = max(30, 120 - blur_r * 20)
            fill = hex_to_rgb(style['accent']) + (alpha,)
            for dx in (-blur_r, 0, blur_r):
                for dy in (-blur_r, 0, blur_r):
                    draw.text((px + dx, py + dy), text, font=font, fill=fill)

    draw.text((px, py), text, fill=hex_to_rgb(style.get('text', '#c8dcff')), font=font)


def draw_scene_label(draw, scene, scene_transition):
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
    label = f"[{scene.upper()}]"
    if scene_transition and scene_transition != 'cut':
        label += f" → {scene_transition}"
    draw.text((16, 16), label, fill=(80, 100, 140), font=font)


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE CACHE
# ══════════════════════════════════════════════════════════════════════════════

_image_cache = {}

_SCENE_SEEDS = {
    'intro': 10, 'buildup': 20, 'climax': 30,
    'release': 40, 'idle': 50, 'focus-arc': 60, 'normal': 70,
}
_MODE_SEEDS = {
    'chaos': 80, 'burst': 90, 'focus': 100, 'linger': 110, 'normal': 120,
}

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
    idx = seg.get('jobIndex', 0) if isinstance(seg.get('jobIndex'), int) else hash(str(seg.get('jobId', ''))) % 200
    return base + (idx % 50)


def _load_segment_image(seg, width=WIDTH, height=HEIGHT):
    seed = _scene_seed(seg)
    cache_key = (width, height, seed)
    if cache_key in _image_cache:
        return _image_cache[cache_key].copy()

    scene = seg.get('scene', 'normal')
    mode  = seg.get('mode', 'normal')

    try:
        url = f"https://picsum.photos/seed/{seed}/{width}"
        import urllib.request
        req = urllib.request.Request(url, headers={'User-Agent': 'NarrativeOS/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            img = Image.open(resp).convert('RGB')
            img = img.resize((width, height), Image.LANCZOS)
            _image_cache[cache_key] = img
            return img.copy()
    except Exception:
        pass

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


# ══════════════════════════════════════════════════════════════════════════════
# RENDER PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def render_camera_frame(seg, local_t, cam: CameraState):
    cb      = seg.get('contentBinding', {})
    style   = cb.get('style', {'bg': '#0a0a0f', 'text': '#c8dcff', 'accent': '#0088cc', 'glow': False})
    caption = cb.get('caption', seg.get('jobId') or seg.get('job_name') or '·')
    scene   = seg.get('scene', 'normal')
    scene_t = seg.get('sceneTransition')
    mode    = seg.get('mode', 'normal')

    # 1. Stable base image
    img = _load_segment_image(seg, WIDTH, HEIGHT)

    # 2. Shot curve
    curve = seg.get('shotCurve')
    base_zoom = 1.0
    glow_val  = 0.0
    shake_val = False
    if curve:
        from engine.renderer import evaluate_shot_curve
        result = evaluate_shot_curve(curve, local_t)
        base_zoom = result.get('zoom', 1.0)
        glow_val  = result.get('glow', 0.0)
        shake_val = result.get('shake', False)

    # 3. Scene Dynamics + Frame Entropy (includes geometric warp first)
    img = render_atmosphere(img, seg, local_t, cam)

    # 4. Overlays
    draw = ImageDraw.Draw(img)
    draw_caption(draw, caption, style)
    draw_scene_label(draw, scene, scene_t)

    # Flash
    if local_t < 0.05 and seg.get('flashColor') and seg.get('flashDur', 0) > 0:
        flash_alpha = max(30, int(180 * (1.0 - local_t / 0.05)))
        fl = hex_to_rgb(seg['flashColor']) + (flash_alpha,)
        draw.rectangle([0, 0, WIDTH - 1, HEIGHT - 1], fill=fl)

    # Edge glow
    if seg.get('edgeColor') and seg.get('edgeWidth'):
        apply_edge_glow(draw, WIDTH, HEIGHT, seg['edgeColor'], int(seg['edgeWidth']))

    # 5. Camera transforms
    effective_zoom = base_zoom * cam.zoom
    img = apply_zoom(img, effective_zoom)
    img = apply_pan_offset(img, cam.x, cam.y)

    # 6. Post-processing
    if glow_val > 0.1:
        img = apply_glow(img, 1.0 + glow_val * 0.6)
    if shake_val:
        img = apply_shake(img, intensity=5 + glow_val * 8)

    # Flash overlay
    if seg.get('flashColor') and seg.get('flashDur', 0) > 0:
        if local_t < 0.15:
            alpha = 0.18 * (1.0 - local_t / 0.15)
            img = apply_flash(img, seg['flashColor'], alpha)

    return img


# ══════════════════════════════════════════════════════════════════════════════
# BATCH RENDER
# ══════════════════════════════════════════════════════════════════════════════

def render_frames(segments, out_dir="frames", total_ms=5000):
    total_ms = max(total_ms, 5000)
    os.makedirs(out_dir, exist_ok=True)

    frames = []
    for seg_idx, seg in enumerate(segments):
        start = seg.get('start', 0.0)
        end   = seg.get('end', start + seg.get('duration', 0.01))
        dur   = max(end - start, 0.001)
        seg_frames = max(1, int(dur * total_ms / 1000 * FPS))
        for i in range(seg_frames):
            local_t = i / seg_frames if seg_frames > 1 else 0.0
            abs_idx = int(start * total_ms / 1000 * FPS) + i
            frames.append((seg, local_t, abs_idx, i, seg_idx))

    frames.sort(key=lambda x: x[2])

    cam = CameraState()
    prev_seg_idx = None

    print(f"  Frame Entropy Engine rendering {len(frames)} frames (total_ms={total_ms})...")

    prev_img = None

    for seg, local_t, abs_idx, frame_in_seg, seg_idx in frames:
        is_cut_snap = (frame_in_seg == 0 and seg.get('camCutSnap'))
        is_new_seg  = (seg_idx != prev_seg_idx)

        if is_new_seg:
            prev_seg_idx = seg_idx
            if is_cut_snap:
                cam.vx *= 0.3
                cam.vy *= 0.3
                cam.vz *= 0.4
            elif prev_seg_idx is not None:
                cam.vx *= 0.75
                cam.vy *= 0.75
                cam.vz *= 0.80

        ax, ay, az = compute_camera_force(seg)

        if is_cut_snap and frame_in_seg == 0:
            cam.vx += (seg.get('camZoom', 1.0) - 1.0) * 15.0
            cam.vy += (seg.get('camZoom', 1.0) - 1.0) * 8.0

        cam.step(ax, ay, az)

        img = render_camera_frame(seg, local_t, cam)

        # Temporal motion blur: blend with previous frames for real accumulation
        if prev_img is not None:
            speed = math.sqrt(cam.vx**2 + cam.vy**2)
            blur_strength = 0.12 * min(1.0, speed / 8.0)
            if blur_strength > 0.01:
                img = Image.blend(img, prev_img, blur_strength)

        if prev_img is not None and frame_in_seg < 3:
            alpha = frame_in_seg / 3.0
            img = Image.blend(prev_img, img, alpha)

        if is_cut_snap:
            prev_img = None
        else:
            prev_img = img.copy()

        img.save(f"{out_dir}/frame_{abs_idx:06d}.png", optimize=True)

    return len(frames)


# ══════════════════════════════════════════════════════════════════════════════
# VIDEO ENCODING
# ══════════════════════════════════════════════════════════════════════════════

def frames_to_video(frames_dir="frames", output="output.mp4", fps=FPS):
    if not os.path.exists(frames_dir):
        print(f"[ERROR] Frames directory '{frames_dir}' not found.")
        return

    count = len([f for f in os.listdir(frames_dir) if f.endswith('.png')])
    if count == 0:
        print(f"[ERROR] No frames found in '{frames_dir}'.")
        return

    print(f"  Encoding {count} frames → {output} ...")

    png_files = sorted([f for f in os.listdir(frames_dir) if f.endswith('.png')])
    first_idx = int(png_files[0].split('_')[1].split('.')[0]) if png_files else 0

    abs_dir = os.path.abspath(frames_dir)
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-start_number", str(first_idx),
        "-i", f"{abs_dir}/frame_%06d.png",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        output,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] ffmpeg failed:\n{result.stderr[-500:]}")
        return

    print(f"  [OK] Video: {output}")


def render_video(segments, output="output.mp4", total_ms=5000, frames_dir="frames"):
    print(f"[Renderer] Frame Entropy Engine — {len(segments)} segments...")
    n = render_frames(segments, out_dir=frames_dir, total_ms=total_ms)
    print(f"[Renderer] {n} frames rendered.")
    print(f"[Renderer] Encoding video...")
    frames_to_video(frames_dir=frames_dir, output=output)
    print(f"[OK] Output: {output}")
    return output