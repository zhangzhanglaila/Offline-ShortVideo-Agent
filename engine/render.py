"""
render.py — Narrative OS Pipeline
Events → Narrative Kernel → Compiled IR → Content Binding → Shot Curves → Frames → Video

Usage:
    python render.py input.json [--output output.mp4] [--fps 30] [--width 1280] [--height 720]
"""

import json
import sys
import math
import argparse
from pathlib import Path

# Add project root to path so engine/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Engine imports
from engine.narrative import SceneResolver, ModeResolver, IntentQueue, Intent
from engine.compiler import compile_narrative_instruction, compile_content_binding, compile_audio_params
from engine.renderer import compile_shot_curve, evaluate_shot_curve


# ─────────────────────────────────────────────────────────────────────────────
# Constants (mirrored from original JS)
# ─────────────────────────────────────────────────────────────────────────────

RHYTHM = {
    'MIN_HOLD':    0.018,
    'MIN_GAP':     0.004,
    'TOTAL_DUR':   12000,
    'ACCENT_DURATION_MULT': 1.35,
    'MIN_SEG_DURATION': 0.008,
}

DIRECTOR_PLAN = {
    'CLAIMED':    lambda ctx: (['claimed'] if not ((ctx.get('nextSameType') or {}).get('type') == 'COMPLETED') else ['absorb']),
    'UNBLOCKED':  lambda ctx: ['unblocked', 'accent-beat'],
    'COMPLETED':  lambda ctx: (['absorb'] if (ctx.get('nextSameType') or {}).get('type') == 'COMPLETED' else ['completed']),
    'FAILED':     lambda ctx: ['failed'],
    'RETRY':      lambda ctx: (['retry', 'retry-storm', 'absorb'] if ctx.get('isRetryStorm') else ['retry']),
    'POISON_PILL':lambda ctx: ['poison-pill', 'accent-beat', 'cascade-start'],
    'HEARTBEAT':  lambda ctx: ['absorb'],
    'CREATED':    lambda ctx: ['absorb'],
    'PROGRESS':   lambda ctx: ['absorb'],
}


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — Semantic Context Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_ctx(events, i, start_ts, end_ts, job_deps):
    ev    = events[i]
    prev  = events[i - 1] if i > 0 else None
    next_ev = events[i + 1] if i < len(events) - 1 else None

    # Parallel burst detection
    norm = lambda ts: (ts - start_ts) / (end_ts - start_ts or 1)
    THR  = 0.03

    pS = i
    while pS > 0 and norm(ev['ts']) - norm(events[pS - 1]['ts']) < THR and events[pS - 1]['type'] == ev['type']:
        pS -= 1
    pE = i
    while pE < len(events) - 1 and norm(events[pE + 1]['ts']) - norm(ev['ts']) < THR and events[pE + 1]['type'] == ev['type']:
        pE += 1
    parallel_size = pE - pS + 1
    is_montage    = parallel_size > 1

    # Same-type neighbours
    prev_same = next_same = None
    for k in range(i - 1, -1, -1):
        if events[k]['type'] == ev['type']:
            prev_same = events[k]
            break
    for k in range(i + 1, len(events)):
        if events[k]['type'] == ev['type']:
            next_same = events[k]
            break

    # Retry storm
    retry_run = sum(1 for k in range(i, -1, -1) if events[k]['type'] == 'RETRY')

    # Cascade
    fail_cascade = sum(1 for k in range(i, -1, -1) if events[k]['type'] in ('FAILED', 'POISON_PILL'))

    # Past claimed
    past_claimed = sum(1 for k in range(i) if events[k]['type'] == 'CLAIMED')

    norm_here = norm(ev['ts'])
    gap_prev  = norm_here - norm(prev['ts']) if prev else 1.0
    gap_next  = norm(next_ev['ts']) - norm_here if next_ev else 1.0

    deps = job_deps.get(ev.get('jobId') or ev.get('job_id'), [])

    return {
        'index': i,
        'totalEvents': len(events),
        'prevEvent': prev,
        'nextEvent': next_ev,
        'prevSameType': prev_same,
        'nextSameType': next_same,
        'isMontage': is_montage,
        'isFirstInMontage': is_montage and pS == i,
        'isLastInMontage':  is_montage and pE == i,
        'parallelBurstSize': parallel_size,
        'isRetryStorm': retry_run >= 3,
        'isCascade': fail_cascade >= 2,
        'pastClaimed': past_claimed,
        'gapPrev': gap_prev,
        'gapNext': gap_next,
        'jobDegree': len(deps),
        'isOnCriticalPath': len(deps) <= 1,
        'isFirstEvent': i == 0,
        'isLastEvent': i == len(events) - 1,
        'prevType': prev['type'] if prev else None,
        'nextType': next_ev['type'] if next_ev else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — Rhythm Resolver
# ─────────────────────────────────────────────────────────────────────────────

def resolve_rhythm(tags, ctx, mode, scene):
    """Returns {hold, merge, accent}."""
    if   mode == 'chaos':  return {'hold': 0.012, 'merge': False, 'accent': True}
    elif mode == 'burst':   return {'hold': 0.018, 'merge': True,  'accent': True}
    elif mode == 'focus':   return {'hold': 0.060, 'merge': False, 'accent': True}
    elif mode == 'linger':  return {'hold': 0.100, 'merge': False, 'accent': False}

    if   'claimed' in tags and 'montage-first' in tags: return {'hold': 0.050, 'accent': True}
    elif 'claimed' in tags and 'montage' in tags:        return {'hold': 0.025, 'accent': True}
    elif 'claimed' in tags and 'critical' in tags:       return {'hold': 0.055, 'accent': True}
    elif 'claimed' in tags:                                return {'hold': 0.040, 'accent': False}
    elif 'unblocked' in tags:                             return {'hold': 0.050, 'accent': True}
    elif 'completed' in tags:                              return {'hold': 0.030, 'accent': False}
    elif 'failed' in tags and 'cascade' in tags:         return {'hold': 0.060, 'accent': True}
    elif 'failed' in tags:                                return {'hold': 0.050, 'accent': False}
    elif 'retry' in tags:                                  return {'hold': 0.025, 'accent': False}
    elif 'poison-pill' in tags:                            return {'hold': 0.060, 'accent': True}
    elif 'absorb' in tags:                                 return {'hold': 0.000, 'merge': True}
    return {'hold': 0.030, 'merge': False, 'accent': False}


def resolve_emphasis(tags, ctx, mode):
    if 'claimed' in tags or 'poison-pill' in tags: return 'strong'
    if 'unblocked' in tags or 'completed' in tags or 'failed' in tags: return 'medium'
    if 'retry' in tags: return 'weak'
    return 'none'


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3 — Change Arbiter
# ─────────────────────────────────────────────────────────────────────────────

def resolve_visual(tags, ctx, mode):
    """Returns {cam: {}, flash: {}, vis: {}} — mirrors original JS resolveVisual."""
    cam = {'zoom': 1.0, 'move': False, 'cutSnap': False, 'duration': 0}
    fl  = {'color': None, 'duration': 0}
    vis = {'glow': False, 'edgeColor': None, 'edgeWidth': None, 'labelDelay': 0, 'shake': False, 'pulse': False}

    if mode == 'chaos':
        cam.update({'zoom': 1.6, 'move': True, 'cutSnap': True, 'duration': 60})
        fl.update({'color': '#6b5000', 'duration': 50})
        vis.update({'shake': True, 'labelDelay': 0.90})
        return {'cam': cam, 'fl': fl, 'vis': vis}
    elif mode == 'burst':
        cam.update({'zoom': 0.75, 'move': True, 'cutSnap': True, 'duration': 100})
        fl.update({'color': '#0088cc', 'duration': 70})
        vis.update({'glow': True, 'labelDelay': 0.65})
        return {'cam': cam, 'fl': fl, 'vis': vis}
    elif mode == 'focus':
        cam.update({'zoom': 1.6, 'move': True, 'cutSnap': False, 'duration': 400})
        fl.update({'color': '#0088cc', 'duration': 100})
        vis.update({'glow': True, 'labelDelay': 0.60})
        return {'cam': cam, 'fl': fl, 'vis': vis}
    elif mode == 'linger':
        cam.update({'zoom': 1.0, 'move': False, 'cutSnap': False, 'duration': 0})
        fl.update({'color': '#004488', 'duration': 40})
        vis.update({'glow': False, 'labelDelay': 0.80})
        return {'cam': cam, 'fl': fl, 'vis': vis}

    # Tag-based fallthrough
    burst_zoom  = 0.85 if ctx['parallelBurstSize'] > 3 else (1.0 if ctx['parallelBurstSize'] > 1 else 1.15)
    degree_zoom = 0.80 if ctx['jobDegree'] > 4 else (1.0 if ctx['jobDegree'] > 2 else 1.10)
    gap_zoom    = 1.30 if ctx['gapPrev'] < 0.01 else (0.95 if ctx['gapPrev'] > 0.3 else 1.0)

    if 'claimed' in tags and 'montage-first' in tags:
        cam.update({'zoom': 0.75 * burst_zoom, 'move': True, 'cutSnap': True, 'duration': 400})
        fl.update({'color': '#0088cc', 'duration': 80})
        vis.update({'glow': True, 'labelDelay': 0.75})
    elif 'claimed' in tags and 'montage' in tags:
        cam.update({'zoom': 1.5 * degree_zoom * burst_zoom, 'move': True, 'cutSnap': True, 'duration': 80})
        fl.update({'color': '#0088cc', 'duration': 60})
        vis.update({'glow': True, 'labelDelay': 0.75})
    elif 'claimed' in tags and 'critical' in tags:
        cam.update({'zoom': 1.5 * gap_zoom, 'move': True, 'cutSnap': False, 'duration': 350})
        fl.update({'color': '#0088cc', 'duration': 90})
        vis.update({'glow': True, 'labelDelay': 0.70})
    elif 'claimed' in tags:
        cam.update({'zoom': 1.4 * degree_zoom, 'move': True, 'cutSnap': False, 'duration': 320})
        fl.update({'color': '#0088cc', 'duration': 80})
        vis.update({'glow': True, 'labelDelay': 0.75})
    elif 'unblocked' in tags:
        cam.update({'zoom': 1.1 * burst_zoom * gap_zoom, 'move': True, 'cutSnap': True, 'duration': 380})
        fl.update({'color': '#6b5000', 'duration': 80})
        vis.update({'glow': True, 'edgeColor': '#f0c040', 'edgeWidth': 2.5, 'labelDelay': 0.70})
    elif 'completed' in tags:
        fl.update({'color': '#00aa44', 'duration': 60})
        vis.update({'glow': True, 'edgeColor': '#00aa44', 'edgeWidth': 2.0, 'labelDelay': 0.70})
    elif 'failed' in tags and 'cascade' in tags:
        cam.update({'zoom': 1.3 * burst_zoom, 'move': True, 'cutSnap': False, 'duration': 400})
        fl.update({'color': '#aa0033', 'duration': 130})
        vis.update({'glow': True, 'edgeColor': '#aa0033', 'edgeWidth': 2.5, 'labelDelay': 0.60})
    elif 'failed' in tags:
        cam.update({'zoom': 1.2 * gap_zoom, 'move': True, 'cutSnap': False, 'duration': 350})
        fl.update({'color': '#aa0033', 'duration': 100})
        vis.update({'glow': True, 'edgeColor': '#aa0033', 'edgeWidth': 2.0, 'labelDelay': 0.65})
    elif 'retry' in tags and not 'retry-storm' in tags:
        fl.update({'color': '#6b5000', 'duration': 80})
        vis.update({'shake': True, 'labelDelay': 0.70})
    elif 'retry-storm' in tags:
        fl.update({'color': '#6b5000', 'duration': 60})
        vis.update({'shake': True, 'labelDelay': 0.80})
    elif 'poison-pill' in tags:
        cam.update({'zoom': 1.1 * burst_zoom, 'move': True, 'cutSnap': True, 'duration': 380})
        fl.update({'color': '#ff0033', 'duration': 120})
        vis.update({'glow': True, 'edgeColor': '#ff0033', 'edgeWidth': 2.5, 'labelDelay': 0.50})

    return {'cam': cam, 'fl': fl, 'vis': vis}


# ─────────────────────────────────────────────────────────────────────────────
# Scene transitions map
# ─────────────────────────────────────────────────────────────────────────────

SCENE_TRANSITIONS = {
    'intro→buildup':    'zoom-in',
    'buildup→climax':   'flash-cut',
    'climax→release':   'silence-hold',
    'focus-arc→climax': 'tighten',
    'release→idle':     'fade-out',
    'release→buildup':  'cut',
}


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def build_director(trace):
    """
    Full Narrative OS pipeline: events → segments → compiled IRs → shot curves.
    Returns list of fully-populated segment dicts ready for rendering.
    """
    events  = trace['events']
    start_ts = trace['startTs']
    end_ts   = trace['endTs']
    jobs    = trace.get('jobs', [])

    if not events:
        return []

    job_deps = {j['id']: j.get('dependsOn', []) for j in jobs}

    raw_segs = []
    scene_resolver = SceneResolver()
    mode_resolver = ModeResolver()
    intent_queue  = IntentQueue()

    prev_scene = None
    prev_mode  = 'normal'
    prev_hold = 0.03

    for i, ev in enumerate(events):
        fn = DIRECTOR_PLAN.get(ev['type'])
        if not fn:
            continue

        ctx = build_ctx(events, i, start_ts, end_ts, job_deps)
        tags = fn(ctx)
        if 'absorb' in tags:
            continue

        start   = (ev['ts'] - start_ts) / (end_ts - start_ts or 1)
        next_ts = events[i + 1]['ts'] if i < len(events) - 1 else ev['ts'] + 300
        end     = (next_ts - start_ts) / (end_ts - start_ts or 1)

        # Scene resolver
        scene_result  = scene_resolver.resolve(tags, ctx, ev['ts'])
        scene         = scene_result.scene
        from_scene    = scene_result.from_scene

        if scene_result.epoch != intent_queue.scene_epoch:
            intent_queue.advance_epoch()

        scene_transition = (SCENE_TRANSITIONS.get(f'{from_scene}→{scene}') if from_scene else None)

        # Mode resolver
        mode_result   = mode_resolver.resolve(tags, ctx, scene, ev['ts'])
        mode          = mode_result.mode
        transition    = mode_result.transition
        from_mode     = mode_result.from_mode

        scene_changed = from_scene is not None
        mode_changed  = from_mode  is not None

        # Change Arbiter
        committed_mode = mode
        committed_trans = transition
        if scene_changed:
            committed_mode = prev_mode
            committed_trans = None
            if mode_changed:
                intent_queue.push(Intent(
                    mode=mode,
                    transition=transition,
                    scene_at_capture=scene,
                    scene_epoch_id=intent_queue.scene_epoch,
                    created_at=ev['ts'],
                ))
        elif len(intent_queue) > 0:
            released = intent_queue.try_release(scene, intent_queue.scene_epoch, ev['ts'])
            if released:
                committed_mode   = released.mode
                committed_trans  = released.transition
            else:
                if len(intent_queue) == 0:
                    committed_mode   = mode
                    committed_trans  = (transition if mode != prev_mode else None)
        else:
            committed_trans = (transition if mode != prev_mode else None)

        # Rhythm
        rhythm = resolve_rhythm(tags, ctx, committed_mode, scene)
        if not scene_changed and not mode_changed and prev_hold:
            if abs(rhythm['hold'] - prev_hold) / prev_hold < 0.15:
                rhythm = {**rhythm, 'hold': prev_hold}

        # Visual
        vis_result = resolve_visual(tags, ctx, committed_mode)
        cam = vis_result['cam']
        fl  = vis_result['fl']
        vis = vis_result['vis']
        emphasis = resolve_emphasis(tags, ctx, committed_mode)

        # Narrative Compiler: compile segment → renderIR
        seg_for_compile = {
            'scene': scene,
            'mode':  committed_mode,
            'transition': committed_trans,
            'camZoom':   cam['zoom'],
            'camMove':   cam['move'],
            'camCutSnap': cam['cutSnap'],
            'camDur':    cam['duration'],
            'flashColor': fl['color'],
            'flashDur':  fl['duration'],
            'rhythm':    rhythm,
            'emphasis':   emphasis,
            'tags':       tags,
            'glow':       vis['glow'],
            'shake':      vis['shake'],
            'edgeColor':  vis['edgeColor'],
            'edgeWidth':  vis['edgeWidth'],
            'duration':   max(end - start, rhythm['hold'] or RHYTHM['MIN_SEG_DURATION']),
        }
        render_ir = compile_narrative_instruction(seg_for_compile)

        # Content Binding + Audio Compiler
        content_binding = compile_content_binding(render_ir.__dict__, seg_for_compile)
        audio_params    = compile_audio_params(render_ir.__dict__)

        # Timeline Renderer: compile shot curve
        total_ms = max(end - start, rhythm['hold'] or 0) * RHYTHM['TOTAL_DUR']
        shot_curve = compile_shot_curve(render_ir.__dict__, total_ms)

        duration = max(end - start, rhythm['hold'] or RHYTHM['MIN_SEG_DURATION'])
        if rhythm.get('accent'):
            duration *= RHYTHM['ACCENT_DURATION_MULT']

        raw_segs.append({
            **ev,
            'ctx':            ctx,
            'tags':           tags,
            'emphasis':       emphasis,
            'scene':          scene,
            'fromScene':      from_scene,
            'sceneTransition': scene_transition,
            'mode':           committed_mode,
            'transition':     committed_trans,
            'start':          start,
            'end':            end,
            'duration':       duration,
            'origDuration':   end - start,
            'camZoom':        cam['zoom'],
            'camMove':        cam['move'],
            'camCutSnap':     cam['cutSnap'],
            'camDur':         cam['duration'],
            'flashColor':     fl['color'],
            'flashDur':       fl['duration'],
            'glow':           vis['glow'],
            'edgeColor':      vis['edgeColor'],
            'edgeWidth':      vis['edgeWidth'],
            'labelDelay':     vis['labelDelay'],
            'shake':          vis['shake'],
            'pulse':          vis['pulse'],
            'merge':          rhythm.get('merge', False),
            'accent':         rhythm.get('accent', False),
            'merged':         False,
            'renderIR':       render_ir.__dict__,
            'contentBinding': content_binding.__dict__,
            'audioParams':    audio_params.__dict__,
            'shotCurve':      shot_curve,  # ShotCurve object
        })

        prev_scene = scene
        prev_mode  = committed_mode
        prev_hold  = rhythm['hold']

    if not raw_segs:
        return []

    # ── Pass 2: MERGE ─────────────────────────────────────────────────────────
    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    merged = []
    i = 0
    while i < len(raw_segs):
        seg = raw_segs[i]
        if seg['merge'] and i + 1 < len(raw_segs):
            next_seg = raw_segs[i + 1]
            next_seg['start'] = clamp(seg['start'], 0, 1)
            next_seg['duration'] = clamp(next_seg['end'] - next_seg['start'], RHYTHM['MIN_SEG_DURATION'], 1)
            if not next_seg.get('stateEffects'):
                next_seg['stateEffects'] = []
            next_seg['stateEffects'].append({'jobId': seg['jobId'], 'type': seg['type']})
            seg['merged'] = True
            i += 1
            continue

        j = i + 1
        while j < len(raw_segs) and raw_segs[j]['merged']:
            j += 1
        if j > i + 1:
            last = raw_segs[j - 1]
            seg['end']     = clamp(last['end'], seg['start'], 1)
            seg['duration'] = clamp(seg['end'] - seg['start'], RHYTHM['MIN_SEG_DURATION'], 1)

        merged.append(seg)
        i += 1

    # ── Pass 3: MIN_HOLD stretch ────────────────────────────────────────────────
    for seg in merged:
        if seg['duration'] < RHYTHM['MIN_HOLD']:
            pad = (RHYTHM['MIN_HOLD'] - seg['duration']) / 2
            seg['start'] = max(0, seg['start'] - pad)
            seg['end']   = min(1, seg['end']   + pad)
            seg['duration'] = seg['end'] - seg['start']

    # ── Pass 4: MIN_GAP ────────────────────────────────────────────────────────
    for i in range(1, len(merged)):
        prev = merged[i - 1]
        curr = merged[i]
        gap  = curr['start'] - prev['end']
        if gap < RHYTHM['MIN_GAP']:
            adj = (RHYTHM['MIN_GAP'] - gap) / 2
            curr['start'] = clamp(curr['start'] + adj, 0, 1)
            prev['end']   = clamp(prev['end']   - adj, 0, 1)
            curr['duration'] = clamp(curr['end'] - curr['start'], RHYTHM['MIN_SEG_DURATION'], 1)
            prev['duration'] = clamp(prev['end'] - prev['start'], RHYTHM['MIN_SEG_DURATION'], 1)

    for seg in merged:
        seg['start']    = clamp(seg['start'], 0, 1)
        seg['end']      = clamp(seg['end'], 0, 1)
        seg['duration'] = clamp(seg['end'] - seg['start'], RHYTHM['MIN_SEG_DURATION'], 1)

    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Frame Evaluation (Python port of applySegAtT)
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_segment_frame(seg, local_t):
    """
    Evaluate a segment at normalized local_t (0-1).
    Uses the pre-compiled shotCurve for zoom/glow/shake.
    """
    curve_result = evaluate_shot_curve(seg['shotCurve'], max(0.0, min(1.0, local_t)))
    return {
        'zoom':  curve_result['zoom'],
        'glow':  curve_result['glow'],
        'shake': curve_result['shake'],
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI / Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Narrative OS Renderer')
    parser.add_argument('input',  help='Input trace JSON file')
    parser.add_argument('--output', '-o', default='output.mp4', help='Output video file')
    parser.add_argument('--fps',   type=int, default=30, help='Frames per second')
    parser.add_argument('--width', type=int, default=1280, help='Output width')
    parser.add_argument('--height', type=int, default=720, help='Output height')
    parser.add_argument('--duration-ms', type=int, default=12000, help='Total duration in ms')
    parser.add_argument('--export-ir', action='store_true', help='Dump compiled IRs to JSON')
    parser.add_argument('--export-video', action='store_true', help='Render video via PIL+ffmpeg (requires ffmpeg in PATH)')
    args = parser.parse_args()

    with open(args.input) as f:
        trace = json.load(f)

    print(f"[trace] {len(trace['events'])} events, {len(trace.get('jobs', []))} jobs")
    print(f"[pipeline] Running Narrative OS...")

    segments = build_director(trace)
    print(f"[OK] Compiled {len(segments)} segments")

    if args.export_ir:
        out = {'segments': [], 'totalDuration': args.duration_ms}
        for seg in segments:
            out['segments'].append({
                'jobId':           seg.get('jobId') or seg.get('job_id'),
                'type':            seg.get('type'),
                'scene':           seg['scene'],
                'mode':            seg['mode'],
                'transition':      seg['transition'],
                'start':           seg['start'],
                'end':             seg['end'],
                'duration':        seg['duration'],
                'renderIR':        seg['renderIR'],
                'contentBinding':  seg['contentBinding'],
                'audioParams':     seg['audioParams'],
                'shotCurve':       {
                    'keyframes': [{'t': k.t, 'zoom': k.zoom, 'glow': k.glow, 'shake': k.shake}
                                  for k in seg['shotCurve'].keyframes],
                    'easing':    seg['shotCurve'].easing,
                },
            })
        with open('render_ir_export.json', 'w') as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"📤 IR exported to render_ir_export.json")

    if args.export_video:
        from engine.renderer import render_video
        output_file = args.output or 'output.mp4'
        print(f"\n🎬 Rendering video: {output_file}")
        render_video(
            segments,
            output=output_file,
            total_ms=args.duration_ms,
            frames_dir='frames',
        )

    print(f"\n🎯 Pipeline complete. {len(segments)} segments ready for rendering.")


if __name__ == '__main__':
    main()
