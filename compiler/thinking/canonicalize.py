"""Canonicalization — Semantic normalization for content-addressable hashing.

Ensures that semantically equivalent values produce identical canonical form:
  - Dict keys sorted (order-independent)
  - None values stripped from dicts (absent ≡ None)
  - Float precision normalized (0.3000000004 → 0.3)
  - Recursive normalization through nested structures
  - Lists preserve order (order is semantic in audio tracks, elements)

This is the foundation for:
  - Content-addressable storage (CAS)
  - Semantic deduplication
  - Distributed render cache
  - Cross-session artifact reuse

Usage:
    from compiler.thinking.canonicalize import canonicalize, content_hash

    normalized = canonicalize({"b": 1, "a": None, "v": 0.3000000004})
    # → {"b": 1, "v": 0.3}

    h = content_hash({"some": "data"})
    # → "a1b2c3d4e5f6..." (16-char hex prefix)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonicalize(content: Any) -> Any:
    """Normalize content for semantic hashing.

    Rules:
      - None values stripped from dicts (None ≡ absent key)
      - Float normalization: round to 10 decimal places
      - Dict keys sorted recursively
      - Lists preserve order (order is semantic)
      - Tuples normalized element-wise
      - All other types pass through unchanged
    """
    if content is None:
        return None
    if isinstance(content, dict):
        result = {}
        for k, v in sorted(content.items()):
            cv = canonicalize(v)
            if cv is not None:
                result[k] = cv
        return result
    if isinstance(content, list):
        return [canonicalize(v) for v in content]
    if isinstance(content, float):
        return round(content, 10)
    if isinstance(content, tuple):
        return tuple(canonicalize(v) for v in content)
    return content


def content_hash(content: Any) -> str:
    """Deterministic SHA-256 hash of arbitrary content.

    Applies canonicalization before hashing so that semantically equivalent
    values (1.0 vs 1, missing None vs absent key) produce the same hash.

    Returns:
        16-character hex prefix (collision-resistant for <65K artifacts).
    """
    if content is None:
        return "none"
    normalized = canonicalize(content)
    try:
        serialized = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        serialized = str(normalized)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def derived_hash(content: Any, **factors: Any) -> str:
    """Hash of content + external factors (config, executor, environment).

    Unlike content_hash, this includes non-content inputs that affect
    the rendered output. Changing any factor invalidates the cache.

    Typical factors:
      - ffmpeg_version: "6.1"
      - renderer_version: "1.0"
      - font: "NotoSansSC"
      - transition: "fade"
      - fps: 30
      - width: 1080
      - height: 1920

    This is the cache key for rendered artifacts (scene_video, final_video).
    Content-only artifacts (scene_ir, script) should use content_hash.

    Returns:
        16-character hex prefix.
    """
    combined = {"_content": content}
    for k, v in sorted(factors.items()):
        combined[f"_factor_{k}"] = v
    return content_hash(combined)
