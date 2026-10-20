"""Retry — Configurable retry mechanism for flaky operations.

Provides:
  - @retry decorator with exponential backoff
  - RetryPolicy dataclass for configuration
  - RetryContext passed to each attempt for logging/metrics

Usage:
    from compiler.runtime.retry import retry, RetryPolicy

    @retry(RetryPolicy(max_attempts=3, base_delay=0.5))
    def render_scene(scene_ir):
        ...

    # Async version
    @retry(RetryPolicy(max_attempts=3))
    async def fetch_asset(url):
        ...
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    base_delay: float = 1.0        # seconds
    max_delay: float = 30.0        # seconds
    backoff_factor: float = 2.0    # exponential multiplier
    jitter: float = 0.1            # ±10% random jitter
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)

    def delay_for_attempt(self, attempt: int) -> float:
        """Calculate delay with exponential backoff + jitter."""
        delay = min(self.base_delay * (self.backoff_factor ** attempt), self.max_delay)
        jitter_range = delay * self.jitter
        return delay + random.uniform(-jitter_range, jitter_range)


@dataclass
class RetryContext:
    """Context passed to retry callbacks."""
    attempt: int = 0
    max_attempts: int = 0
    last_error: Exception | None = None
    total_elapsed: float = 0.0
    errors: list[Exception] = field(default_factory=list)


def retry(
    policy: RetryPolicy | None = None,
    on_retry: Callable[[RetryContext], None] | None = None,
) -> Callable[[F], F]:
    """Decorator that retries a function on failure.

    Args:
        policy: Retry configuration. Uses defaults if None.
        on_retry: Optional callback called after each failed attempt.
    """
    if policy is None:
        policy = RetryPolicy()

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                ctx = RetryContext(max_attempts=policy.max_attempts)
                start = time.monotonic()
                for attempt in range(policy.max_attempts):
                    ctx.attempt = attempt
                    try:
                        result = await func(*args, **kwargs)
                        return result
                    except policy.retryable_exceptions as e:
                        ctx.last_error = e
                        ctx.errors.append(e)
                        ctx.total_elapsed = time.monotonic() - start
                        if attempt < policy.max_attempts - 1:
                            delay = policy.delay_for_attempt(attempt)
                            logger.warning(
                                "Attempt %d/%d failed for %s: %s. Retrying in %.2fs",
                                attempt + 1, policy.max_attempts, func.__name__, e, delay,
                            )
                            if on_retry:
                                on_retry(ctx)
                            await asyncio.sleep(delay)
                        else:
                            logger.error(
                                "All %d attempts failed for %s: %s",
                                policy.max_attempts, func.__name__, e,
                            )
                            raise
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                ctx = RetryContext(max_attempts=policy.max_attempts)
                start = time.monotonic()
                for attempt in range(policy.max_attempts):
                    ctx.attempt = attempt
                    try:
                        result = func(*args, **kwargs)
                        return result
                    except policy.retryable_exceptions as e:
                        ctx.last_error = e
                        ctx.errors.append(e)
                        ctx.total_elapsed = time.monotonic() - start
                        if attempt < policy.max_attempts - 1:
                            delay = policy.delay_for_attempt(attempt)
                            logger.warning(
                                "Attempt %d/%d failed for %s: %s. Retrying in %.2fs",
                                attempt + 1, policy.max_attempts, func.__name__, e, delay,
                            )
                            if on_retry:
                                on_retry(ctx)
                            time.sleep(delay)
                        else:
                            logger.error(
                                "All %d attempts failed for %s: %s",
                                policy.max_attempts, func.__name__, e,
                            )
                            raise
            return sync_wrapper
    return decorator
