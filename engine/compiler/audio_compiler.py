"""
Audio Instruction Compiler — maps audio hint → audio track parameters.
Generates BPM, filter type, and ADSR envelope from IR audio hint.
"""

from dataclasses import dataclass


@dataclass
class AudioParams:
    bpm: int
    filter: str
    envelope: dict
    scene: str
    intensity: float


def compile_audio_params(ir: dict) -> AudioParams:
    """
    Compile IR audio hint → AudioParams.
    ir: RenderIR (as dict from narrative_compiler)
    """
    audio     = ir.get('audio', 'neutral')
    scene     = ir.get('scene', 'normal')
    intensity = ir.get('intensity', 0.5)
    base_bpm  = 120

    bpm_map = {
        'build-tension': base_bpm + 20,
        'pulse':         base_bpm + 10,
        'wind-down':     base_bpm - 20,
        'disrupt':       base_bpm + 40,
        'sustain':       base_bpm,
        'neutral':       base_bpm,
    }

    filter_map = {
        'build-tension': 'lowpass-rise',  # rising sweep
        'pulse':         'pulse',          # rhythmic filter
        'wind-down':     'lowpass-fall',  # closing sweep
        'disrupt':       'bandpass',       # harsh resonance
        'sustain':       'flat',           # no filter
        'neutral':       'flat',
    }

    envelope_map = {
        'build-tension': {'attack': 0.8, 'decay': 0.1, 'sustain': 0.6, 'release': 0.4},
        'pulse':         {'attack': 0.2, 'decay': 0.1, 'sustain': 0.8, 'release': 0.2},
        'wind-down':     {'attack': 0.1, 'decay': 0.2, 'sustain': 0.4, 'release': 0.8},
        'disrupt':       {'attack': 0.9, 'decay': 0.05, 'sustain': 0.3, 'release': 0.1},
        'sustain':       {'attack': 0.1, 'decay': 0.2, 'sustain': 0.9, 'release': 0.3},
        'neutral':       {'attack': 0.1, 'decay': 0.2, 'sustain': 0.8, 'release': 0.3},
    }

    return AudioParams(
        bpm=bpm_map.get(audio, base_bpm),
        filter=filter_map.get(audio, 'flat'),
        envelope=envelope_map.get(audio, envelope_map['neutral']),
        scene=scene,
        intensity=intensity,
    )
