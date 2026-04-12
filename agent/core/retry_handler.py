# -*- coding: utf-8 -*-
"""
错误重试机制 - 指数退避 + 降级方案
"""
import time
import logging
from typing import Callable, Any, Tuple, List, Type
from functools import wraps
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import AGENT_CONFIG

logger = logging.getLogger(__name__)


class RetryableError(Exception):
    """可重试的错误"""
    pass


class NonRetryableError(Exception):
    """不可重试的错误"""
    pass


class RetryHandler:
    """错误重试处理器"""

    # 默认重试配置
    DEFAULT_CONFIG = {
        'max_retries': AGENT_CONFIG.get('max_retries', 3),
        'backoff_factor': AGENT_CONFIG.get('retry_backoff', 2),
        'initial_delay': 1.0,  # 初始延迟（秒）
        'max_delay': 60.0,     # 最大延迟（秒）
        'retry_on': (ConnectionError, TimeoutError, RetryableError),
        'catch_all': True       # 是否捕获所有异常
    }

    def __init__(self, config: dict = None):
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}

    def with_retry(self, func: Callable = None, *,
                   max_retries: int = None,
                   backoff_factor: float = None,
                   retry_on: Tuple[Type[Exception], ...] = None) -> Callable:
        """
        装饰器：包装函数，自动重试

        用法:
            @RetryHandler().with_retry
            def my_function():
                ...

            # 或带参数
            @RetryHandler().with_retry(max_retries=5)
            def my_function():
                ...
        """

        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                _max_retries = max_retries or self.config['max_retries']
                _backoff = backoff_factor or self.config['backoff_factor']
                _retry_on = retry_on or self.config['retry_on']

                last_exception = None

                for attempt in range(_max_retries + 1):
                    try:
                        return fn(*args, **kwargs)

                    except _retry_on as e:
                        last_exception = e
                        if attempt < _max_retries:
                            delay = min(
                                self.config['initial_delay'] * (_backoff ** attempt),
                                self.config['max_delay']
                            )
                            logger.warning(
                                f"Attempt {attempt + 1}/{_max_retries + 1} failed: {e}. "
                                f"Retrying in {delay:.1f}s..."
                            )
                            time.sleep(delay)
                        else:
                            logger.error(
                                f"All {_max_retries + 1} attempts failed. Last error: {e}"
                            )

                    except NonRetryableError as e:
                        logger.error(f"Non-retryable error: {e}")
                        raise

                    except Exception as e:
                        if self.config.get('catch_all') and attempt < _max_retries:
                            last_exception = e
                            delay = min(
                                self.config['initial_delay'] * (_backoff ** attempt),
                                self.config['max_delay']
                            )
                            logger.warning(
                                f"Unexpected error on attempt {attempt + 1}: {e}. "
                                f"Retrying in {delay:.1f}s..."
                            )
                            time.sleep(delay)
                        else:
                            raise

                # 所有重试都失败
                if last_exception:
                    raise last_exception

            return wrapper

        # 支持无参数装饰器 @with_retry
        if func is None:
            return decorator
        return decorator(func)

    def execute_with_fallback(self, primary_fn: Callable,
                              fallback_fn: Callable = None,
                              *args, **kwargs) -> Any:
        """
        执行函数，失败时降级到备选方案

        用法:
            result = handler.execute_with_fallback(
                primary_fn=risky_operation,
                fallback_fn=safe_backup,
                arg1, arg2
            )
        """
        try:
            return self.with_retry(primary_fn)(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Primary function failed: {e}")

            if fallback_fn:
                logger.info("Falling back to backup function...")
                try:
                    return fallback_fn(*args, **kwargs)
                except Exception as fallback_error:
                    logger.error(f"Fallback function also failed: {fallback_error}")
                    raise
            else:
                raise


class CircuitBreaker:
    """熔断器 - 防止持续调用失败的服务"""

    def __init__(self, failure_threshold: int = 5,
                 recovery_timeout: float = 60.0,
                 expected_exception: Type[Exception] = Exception):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self._failure_count = 0
        self._last_failure_time = None
        self._state = 'closed'  # closed, open, half_open

    @property
    def state(self) -> str:
        if self._state == 'open':
            if (time.time() - self._last_failure_time) > self.recovery_timeout:
                self._state = 'half_open'
        return self._state

    def call(self, fn: Callable, *args, **kwargs) -> Any:
        """调用函数，自动熔断"""
        if self.state == 'open':
            raise Exception(f"Circuit breaker is OPEN. Service unavailable.")

        try:
            result = fn(*args, **kwargs)
            if self.state == 'half_open':
                self._reset()
            return result

        except self.expected_exception as e:
            self._record_failure()
            raise

    def _record_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.failure_threshold:
            self._state = 'open'
            logger.error(f"Circuit breaker OPENED after {self._failure_count} failures")

    def _reset(self):
        self._failure_count = 0
        self._state = 'closed'
        logger.info("Circuit breaker CLOSED")


# 全局重试处理器
_retry_handler = None


def get_retry_handler() -> RetryHandler:
    """获取全局重试处理器"""
    global _retry_handler
    if _retry_handler is None:
        _retry_handler = RetryHandler()
    return _retry_handler
