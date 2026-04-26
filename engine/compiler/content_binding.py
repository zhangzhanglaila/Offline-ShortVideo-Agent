"""
Content Binding Layer — maps Narrative IR to actual visual/audio content.
This is the bridge from "directing instructions" to "actual media assets".
"""

from dataclasses import dataclass
from typing import Optional


CONTENT_STYLE_PRESETS = {
    'dark-tech':   {'bg': '#0a0a0f', 'text': '#44aaff', 'accent': '#0088cc', 'font': 'monospace', 'glow': True},
    'light':       {'bg': '#f5f5f5', 'text': '#333',    'accent': '#888',    'font': 'sans-serif', 'glow': False},
    'neon':        {'bg': '#0a0a0f', 'text': '#ff4466', 'accent': '#ff0033', 'font': 'monospace', 'glow': True},
    'warm':        {'bg': '#1a1410', 'text': '#f0c040', 'accent': '#6b5000', 'font': 'serif', 'glow': False},
}


@dataclass
class ContentBinding:
    assetSource: str      # 'generated' | 'image' | 'video' | 'icon'
    contentType: str      # semantic category
    caption: str          # on-screen label
    style: dict          # color/style preset
    genPrompt: str       # text-to-image prompt
    audioHint: str
    duration: float
    intensity: float


def compile_content_binding(ir: dict, seg: dict) -> ContentBinding:
    """
    Compile IR + segment metadata → ContentBinding.
    ir: RenderIR (as dict from narrative_compiler)
    seg: raw segment dict (needs scene, tags, jobId)
    """
    scene = seg.get('scene', 'normal')
    tags  = seg.get('tags', [])
    job_id = seg.get('jobId') or seg.get('job_id') or 'SYSTEM'

    style = CONTENT_STYLE_PRESETS['dark-tech']

    # Content type derivation
    if scene == 'climax' and ir.get('shot') == 'jitter-cut':
        content_type = 'peak-chaos'
    elif scene == 'buildup':
        content_type = 'rising-pattern'
    elif scene == 'release':
        content_type = 'falling-pattern'
    elif ir.get('shot') == 'wide-push':
        content_type = 'overview'
    elif ir.get('shot') == 'tighten':
        content_type = 'focus-detail'
    elif ir.get('shot') == 'static-hold':
        content_type = 'still-frame'
    else:
        content_type = 'steady'

    explicit_text = str(seg.get('text') or '').strip()
    content_binding = seg.get('contentBinding', {}) if isinstance(seg.get('contentBinding'), dict) else {}
    explicit_caption = str(content_binding.get('caption') or '').strip()

    # Caption
    caption_map = {
        'peak-chaos':      '⚠ OVERLOAD',
        'rising-pattern':  '↗ BUILDING',
        'falling-pattern': '↘ RESOLVING',
        'overview':        job_id,
        'focus-detail':    '✗ FAILURE' if 'failed' in tags else '▶ ACTIVE',
        'still-frame':     job_id,
        'steady':          job_id,
    }
    caption = explicit_caption or explicit_text or caption_map.get(content_type, job_id)

    # Gen prompt
    gen_prompt_map = {
        'peak-chaos':       'abstract red network overflow, dark background, glitch art, intense',
        'rising-pattern':   'ascending data visualization, blue tones, dark grid, rising energy',
        'falling-pattern': 'descending waveform, calming blue, fade to dark, resolution',
        'overview':         'tech system diagram, dark mode, nodes and connections, minimal',
        'focus-detail':     'close-up data node, detailed, sharp focus, dark background',
        'still-frame':      'system component, static view, dark background, clean UI',
        'steady':           'system activity, neutral state, dark mode, clean visualization',
    }
    gen_prompt = gen_prompt_map.get(content_type, 'abstract data visualization, dark tech style')

    return ContentBinding(
        assetSource='generated',
        contentType=content_type,
        caption=caption,
        style=style,
        genPrompt=gen_prompt,
        audioHint=ir.get('audio', 'neutral'),
        duration=ir.get('duration', 0),
        intensity=ir.get('intensity', 0.5),
    )
