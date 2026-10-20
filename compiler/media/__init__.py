"""media — Incremental media rendering engine.

Domain-specific layer on top of the domain-agnostic runtime kernel.
Handles scene rendering, caching, composition, and ffmpeg execution.

Architecture:
    thinking/    → runtime kernel (domain-agnostic)
    media/       → media engine (domain-specific)
    engine/      → legacy pipeline (being migrated)
"""
