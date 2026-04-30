# -*- coding: utf-8 -*-
"""
Spring Physics & Easing - Ported from Remotion (Apache 2.0)
https://github.com/remotion-dev/remotion/tree/main/packages/core/src/spring

Remotion的spring()物理引擎端口，提供真实的弹簧回弹、惯性效果。
相比PIL的线性动画，有自然减速、overshoot、settling行为。
"""
import math
from typing import Tuple, Optional

# ==================== Easing ====================

class Easing:
    """缓动函数集合 - 来自Remotion"""

    @staticmethod
    def step0(n: float) -> float:
        return 1.0 if n > 0 else 0.0

    @staticmethod
    def step1(n: float) -> float:
        return 1.0 if n >= 1 else 0.0

    @staticmethod
    def linear(t: float) -> float:
        return t

    @staticmethod
    def quad(t: float) -> float:
        return t * t

    @staticmethod
    def cubic(t: float) -> float:
        return t * t * t

    @staticmethod
    def poly(n: float):
        def func(t: float) -> float:
            return t ** n
        return func

    @staticmethod
    def sin(t: float) -> float:
        return 1 - math.cos((t * math.pi) / 2)

    @staticmethod
    def circle(t: float) -> float:
        return 1 - math.sqrt(1 - t * t)

    @staticmethod
    def exp(t: float) -> float:
        return 2 ** (10 * (t - 1))

    @staticmethod
    def elastic(bounciness: float = 1) -> callable:
        """弹性回弹"""
        p = bounciness * math.pi
        def func(t: float) -> float:
            return 1 - math.cos((t * math.pi) / 2) ** 3 * math.cos(t * p)
        return func

    @staticmethod
    def back(s: float = 1.70158) -> callable:
        """先行后回弹"""
        def func(t: float) -> float:
            return t * t * ((s + 1) * t - s)
        return func

    @staticmethod
    def bounce(t: float) -> float:
        """反弹球效果"""
        if t < 1 / 2.75:
            return 7.5625 * t * t
        if t < 2 / 2.75:
            t2 = t - 1.5 / 2.75
            return 7.5625 * t2 * t2 + 0.75
        if t < 2.5 / 2.75:
            t2 = t - 2.25 / 2.75
            return 7.5625 * t2 * t2 + 0.9375
        t2 = t - 2.625 / 2.75
        return 7.5625 * t2 * t2 + 0.984375

    @staticmethod
    def bezier(x1: float, y1: float, x2: float, y2: float) -> callable:
        """三次贝塞尔曲线"""
        return _bezier_factory(x1, y1, x2, y2)

    @staticmethod
    def in_(easing: callable) -> callable:
        """正向"""
        return easing

    @staticmethod
    def out(easing: callable) -> callable:
        """反向"""
        def func(t: float) -> float:
            return 1 - easing(1 - t)
        return func

    @staticmethod
    def in_out(easing: callable) -> callable:
        """双向"""
        def func(t: float) -> float:
            if t < 0.5:
                return easing(t * 2) / 2
            return 1 - easing((1 - t) * 2) / 2
        return func


def _bezier_factory(x1: float, y1: float, x2: float, y2: float) -> callable:
    """三次贝塞尔工厂函数"""
    # Pre-calculate common expressions
    x2_x1 = x2 - x1
    y2_y1 = y2 - y1

    # Newton-Raphson iteration for finding t for given x
    def _newton_raphson(x: float, epsilon: float = 1e-6) -> float:
        t = x  # Initial guess
        for _ in range(8):  # Max 8 iterations
            x_t = _sample_curve(t, x1, x2) - x
            if abs(x_t) < epsilon:
                break
            t = max(0.0, min(1.0, t - x_t / _derivative_curve(t, x1, x2)))
        return max(0.0, min(1.0, t))

    def func(t: float) -> float:
        if t == 0 or t == 1:
            return t
        return _sample_curve(_newton_raphson(t), y1, y2)
    return func


def _sample_curve(t: float, p1: float, p2: float) -> float:
    return (3 * p1 * t * (1 - t) * (1 - t) +
            3 * p2 * t * t * (1 - t) +
            t * t * t)


def _derivative_curve(t: float, p1: float, p2: float) -> float:
    return (3 * p1 * (1 - t) * (1 - t) +
            6 * p1 * t * (1 - t) * (1 - t) +
            3 * p2 * t * t * (1 - t) * (1 - t) +
            6 * p2 * t * (1 - t) * t)


# ==================== Spring Physics ====================

class SpringConfig:
    """弹簧配置"""
    def __init__(
        self,
        damping: float = 10,
        mass: float = 1,
        stiffness: float = 100,
        overshootClamping: bool = False,
    ):
        self.damping = damping
        self.mass = mass
        self.stiffness = stiffness
        self.overshootClamping = overshootClamping


# AnimationNode for tracking spring state
_anim_cache = {}


def _advance(
    current: float,
    to_value: float,
    velocity: float,
    last_timestamp: float,
    now: float,
    damping: float,
    mass: float,
    stiffness: float,
) -> Tuple[float, float, float]:
    """
    Advance spring simulation by deltaTime.
    Returns: (current, velocity, new_timestamp)
    """
    delta_time = min(now - last_timestamp, 64) / 1000  # cap at 64ms, convert to seconds

    if damping <= 0:
        raise ValueError("Spring damping must be > 0")

    v0 = -velocity
    x0 = to_value - current

    zeta = damping / (2 * math.sqrt(stiffness * mass))  # damping ratio
    omega0 = math.sqrt(stiffness / mass)  # undamped angular frequency
    omega1 = omega0 * math.sqrt(max(0, 1 - zeta ** 2))  # exponential decay

    t = delta_time

    sin1 = math.sin(omega1 * t)
    cos1 = math.cos(omega1 * t)

    # under-damped envelope
    under_damped_envelope = math.exp(-zeta * omega0 * t)
    under_damped_frag = (
        under_damped_envelope *
        (sin1 * ((v0 + zeta * omega0 * x0) / omega1) + x0 * cos1)
    )
    under_damped_position = to_value - under_damped_frag
    under_damped_velocity = (
        zeta * omega0 * under_damped_frag -
        under_damped_envelope *
        (cos1 * (v0 + zeta * omega0 * x0) - omega1 * x0 * sin1)
    )

    # critically damped
    crit_env = math.exp(-omega0 * t)
    crit_position = to_value - crit_env * (x0 + (v0 + omega0 * x0) * t)
    crit_velocity = crit_env * (v0 * (t * omega0 - 1) + t * x0 * omega0 * omega0)

    if zeta < 1:
        return under_damped_position, under_damped_velocity, now
    else:
        return crit_position, crit_velocity, now


_calc_cache = {}


def spring_calculation(
    frame: float,
    fps: float,
    damping: float = 10,
    mass: float = 1,
    stiffness: float = 100,
    overshoot_clamping: bool = False,
) -> Tuple[float, float]:
    """
    Calculate spring position and velocity at given frame.
    Returns: (current, velocity)
    """
    cache_key = (frame, fps, damping, mass, overshoot_clamping, stiffness)
    if cache_key in _calc_cache:
        return _calc_cache[cache_key]

    current = 0.0
    velocity = 0.0
    last_timestamp = 0.0

    frame_clamped = max(0, frame)
    uneven_rest = frame_clamped % 1

    for f in range(int(math.floor(frame_clamped)) + 1):
        if f == int(math.floor(frame_clamped)):
            f += uneven_rest

        time_ms = (f / fps) * 1000
        current, velocity, last_timestamp = _advance(
            current, 1.0, velocity, last_timestamp, time_ms,
            damping, mass, stiffness
        )

    _calc_cache[cache_key] = (current, velocity)
    return current, velocity


def spring(
    frame: float,
    fps: float,
    config: Optional[SpringConfig] = None,
    from_val: float = 0,
    to_val: float = 1,
    duration_in_frames: Optional[float] = None,
    delay: float = 0,
    reverse: bool = False,
) -> float:
    """
    Spring-animated value (port from Remotion).

    Args:
        frame: Current frame number
        fps: Frames per second
        config: SpringConfig with damping/mass/stiffness
        from_val: Start value
        to_val: End value
        duration_in_frames: Optional fixed duration
        delay: Delay before animation starts
        reverse: If True, animate from to_val to from_val

    Returns:
        Animated value at current frame
    """
    if config is None:
        config = SpringConfig()

    needs_natural_duration = reverse or duration_in_frames is not None

    if needs_natural_duration:
        natural_duration = measure_spring(
            fps, config.damping, config.mass, config.stiffness
        )
    else:
        natural_duration = None

    if reverse:
        duration_processed = (duration_in_frames or natural_duration) - frame
    else:
        duration_processed = frame

    delay_processed = duration_processed + (-delay if not reverse else delay)

    if duration_in_frames is not None:
        ratio = delay_processed / duration_in_frames
        if natural_duration:
            ratio = ratio * (natural_duration / duration_in_frames)
        duration_processed = ratio

    if duration_in_frames and delay_processed > duration_in_frames:
        return to_val

    # Clamp to non-negative
    duration_processed = max(0, duration_processed)

    current, _ = spring_calculation(
        duration_processed,
        fps,
        config.damping,
        config.mass,
        config.stiffness,
        config.overshootClamping,
    )

    if config.overshootClamping:
        if to_val >= from_val:
            current = min(current, to_val)
        else:
            current = max(current, to_val)

    # Map from [0,1] to [from_val, to_val]
    if from_val == 0 and to_val == 1:
        result = current
    else:
        result = from_val + current * (to_val - from_val)

    return result


def measure_spring(
    fps: float,
    damping: float = 10,
    mass: float = 1,
    stiffness: float = 100,
    threshold: float = 0.005,
) -> float:
    """
    Measure how many frames a spring animation takes to settle.
    Returns the number of frames until spring is at rest.
    """
    cache_key = (fps, damping, mass, False, stiffness, threshold)
    # Simple cache
    if hasattr(measure_spring, '_cache') and cache_key in measure_spring._cache:
        return measure_spring._cache[cache_key]

    frame = 0
    finished_frame = 0
    prev_diff = float('inf')
    below_threshold_count = 0

    while True:
        current, _ = spring_calculation(
            frame, fps, damping, mass, stiffness, False
        )
        diff = abs(current - 1.0)  # spring_calculation targets 1.0

        if diff < threshold:
            below_threshold_count += 1
            if below_threshold_count >= 20:  # Stay under threshold for 20 frames
                finished_frame = frame - 19
                break
        else:
            below_threshold_count = 0

        frame += 1
        if frame > fps * 10:  # Max 10 seconds worth of frames
            finished_frame = frame
            break

        if diff == prev_diff and diff < threshold:
            # Stuck
            finished_frame = frame
            break
        prev_diff = diff

    if not hasattr(measure_spring, '_cache'):
        measure_spring._cache = {}
    measure_spring._cache[cache_key] = finished_frame

    return finished_frame


# ==================== Interpolation ====================

ExtrapolateType = ['extend', 'identity', 'clamp', 'wrap']


def interpolate(
    input_val: float,
    input_range: list,
    output_range: list,
    easing: callable = None,
    extrapolate_left: str = 'extend',
    extrapolate_right: str = 'extend',
) -> float:
    """
    Map a range of values to another range with easing.
    Ported from Remotion interpolate().
    """
    if len(input_range) != len(output_range):
        raise ValueError("inputRange and outputRange must have same length")

    if len(input_range) < 2:
        raise ValueError("Ranges must have at least 2 elements")

    for i in range(1, len(input_range)):
        if input_range[i] <= input_range[i-1]:
            raise ValueError("inputRange must be strictly monotonically increasing")

    result = input_val
    input_min, input_max = input_range[0], input_range[-1]
    output_min, output_max = output_range[0], output_range[-1]

    # Extrapolate left
    if result < input_min:
        if extrapolate_left == 'identity':
            pass
        elif extrapolate_left == 'clamp':
            result = input_min
        elif extrapolate_left == 'wrap':
            range_len = input_max - input_min
            if range_len > 0:
                result = (((result - input_min) % range_len) + range_len) % range_len + input_min
        # 'extend' = no-op

    # Extrapolate right
    if result > input_max:
        if extrapolate_right == 'identity':
            pass
        elif extrapolate_right == 'clamp':
            result = input_max
        elif extrapolate_right == 'wrap':
            range_len = input_max - input_min
            if range_len > 0:
                result = (((result - input_min) % range_len) + range_len) % range_len + input_min
        # 'extend' = no-op

    if output_min == output_max:
        return output_min

    # Find which segment
    seg_idx = 0
    for i in range(1, len(input_range)):
        if input_range[i] >= result:
            seg_idx = i - 1
            break

    # Map to [0,1]
    t = (result - input_range[seg_idx]) / (input_range[seg_idx + 1] - input_range[seg_idx])

    # Apply easing
    if easing:
        t = easing(t)
    else:
        t = t  # linear

    # Map to output range
    result = t * (output_range[seg_idx + 1] - output_range[seg_idx]) + output_range[seg_idx]

    return result
