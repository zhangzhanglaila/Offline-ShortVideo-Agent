"""Asset Retriever — Search and download assets for video production.

Pluggable search backends (local, online APIs) with content-addressable
caching via AssetStore. Supports concurrent downloads with semaphore limiting.

    retriever = AssetRetriever(
        store=AssetStore("output/.assets"),
        backend=LocalSearchBackend(Path("assets/library")),
    )
    results = await retriever.search_images(["redis", "database"], count=5)
    results = await retriever.search_music(["dramatic"], mood="tense")
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from backend.asset_store import AssetStore
from thinking.canonicalize import content_hash


# ── Data Structures ──

@dataclass(frozen=True)
class AssetResult:
    """A discovered/retreived asset."""
    asset_id: str              # Content hash (from store)
    asset_type: str            # "image" | "video" | "audio"
    source: str                # "local" | "pexels" | "unsplash" | etc.
    path: str                  # Local file path (after download)
    metadata: dict[str, Any] = field(default_factory=dict)
    # metadata may include: width, height, duration, tags, license, url

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_type": self.asset_type,
            "source": self.source,
            "path": self.path,
            "metadata": self.metadata,
        }


# ── Search Backends ──

class SearchBackend(ABC):
    """Abstract search backend for finding assets."""

    @abstractmethod
    async def search(
        self,
        keywords: list[str],
        asset_type: str,
        count: int = 5,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """Search for assets matching keywords.

        Returns list of dicts with at minimum:
            - source_path: Path to the file (local) or URL (remote)
            - metadata: dict with tags, dimensions, duration, etc.
        """
        ...


class LocalSearchBackend(SearchBackend):
    """Search a local asset library directory.

    Matches keywords against filenames and directory names.
    Supports .jpg, .png, .mp4, .mp3, .wav files.
    """

    _EXTENSIONS = {
        "image": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"},
        "video": {".mp4", ".webm", ".mov", ".avi", ".mkv"},
        "audio": {".mp3", ".wav", ".ogg", ".m4a", ".flac"},
    }

    def __init__(self, library_dir: Path):
        self.library_dir = Path(library_dir)

    async def search(
        self,
        keywords: list[str],
        asset_type: str,
        count: int = 5,
        **kwargs,
    ) -> list[dict[str, Any]]:
        if not self.library_dir.exists():
            return []

        extensions = self._EXTENSIONS.get(asset_type, set())
        keywords_lower = [k.lower() for k in keywords]
        results = []

        for file in self.library_dir.rglob("*"):
            if not file.is_file():
                continue
            if file.suffix.lower() not in extensions:
                continue

            # Match keywords against filename path
            name_lower = file.stem.lower().replace("-", " ").replace("_", " ")
            path_lower = str(file.parent).lower()
            score = sum(
                1 for kw in keywords_lower
                if kw in name_lower or kw in path_lower
            )
            if score > 0:
                results.append({
                    "source_path": str(file),
                    "metadata": {
                        "tags": [file.stem],
                        "score": score,
                        "filename": file.name,
                    },
                })

        # Sort by relevance score, descending
        results.sort(key=lambda r: r["metadata"].get("score", 0), reverse=True)
        return results[:count]


class PexelsSearchBackend(SearchBackend):
    """Search Pexels API for stock images and videos.

    Requires PEXELS_API_KEY environment variable.
    Free tier: 200 requests/hour, 20k requests/month.
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.base_url = "https://api.pexels.com"

    async def search(
        self,
        keywords: list[str],
        asset_type: str,
        count: int = 5,
        **kwargs,
    ) -> list[dict[str, Any]]:
        if not self.api_key:
            return []

        import aiohttp

        query = " ".join(keywords)
        endpoint = "/videos/search" if asset_type == "video" else "/v1/search"
        headers = {"Authorization": self.base_url}
        params = {"query": query, "per_page": count}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}{endpoint}",
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()

            results = []
            items = data.get("photos" if asset_type == "image" else "videos", [])
            for item in items[:count]:
                if asset_type == "image":
                    src_url = item.get("src", {}).get("large", "")
                    meta = {
                        "width": item.get("width", 0),
                        "height": item.get("height", 0),
                        "tags": item.get("alt", "").split(),
                        "photographer": item.get("photographer", ""),
                        "license": "Pexels",
                        "url": item.get("url", ""),
                    }
                else:  # video
                    video_files = item.get("video_files", [])
                    src_url = video_files[0]["link"] if video_files else ""
                    meta = {
                        "width": item.get("width", 0),
                        "height": item.get("height", 0),
                        "duration": item.get("duration", 0),
                        "tags": [],
                        "license": "Pexels",
                        "url": item.get("url", ""),
                    }

                if src_url:
                    results.append({
                        "source_url": src_url,
                        "metadata": meta,
                    })
            return results
        except Exception:
            return []


# ── Asset Retriever ──

class AssetRetriever:
    """Search and download assets with content-addressable caching.

    Args:
        store: AssetStore for CAS storage.
        backend: SearchBackend for finding assets.
        max_concurrent: Max parallel downloads (semaphore limit).
    """

    def __init__(
        self,
        store: AssetStore,
        backend: SearchBackend,
        max_concurrent: int = 3,
    ):
        self.store = store
        self.backend = backend
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def search_images(self, keywords: list[str], count: int = 5) -> list[AssetResult]:
        return await self._search_and_fetch(keywords, "image", count)

    async def search_videos(self, keywords: list[str], count: int = 3) -> list[AssetResult]:
        return await self._search_and_fetch(keywords, "video", count)

    async def search_music(self, keywords: list[str], count: int = 3) -> list[AssetResult]:
        return await self._search_and_fetch(keywords, "audio", count)

    async def _search_and_fetch(
        self,
        keywords: list[str],
        asset_type: str,
        count: int,
    ) -> list[AssetResult]:
        """Search backend, then fetch/cache each result."""
        raw_results = await self.backend.search(keywords, asset_type, count)
        if not raw_results:
            return []

        tasks = [self._fetch_one(r, asset_type) for r in raw_results]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return [r for r in results if isinstance(r, AssetResult)]

    async def _fetch_one(
        self,
        raw: dict[str, Any],
        asset_type: str,
    ) -> AssetResult:
        """Download/cache a single asset."""
        async with self._semaphore:
            source_path = raw.get("source_path", "")
            source_url = raw.get("source_url", "")
            metadata = raw.get("metadata", {})
            source = "local" if source_path else "remote"

            if source_path:
                # Local file — store in CAS
                local_path = Path(source_path)
                if not local_path.exists():
                    raise FileNotFoundError(f"Local asset not found: {local_path}")
                content_hash_id = self.store.put(
                    local_path, metadata=metadata, asset_type=asset_type,
                )
                stored_path = self.store.get(content_hash_id)
                return AssetResult(
                    asset_id=content_hash_id,
                    asset_type=asset_type,
                    source=source,
                    path=str(stored_path) if stored_path else str(local_path),
                    metadata=metadata,
                )
            elif source_url:
                # Remote URL — download then store
                local_path = await self._download(source_url)
                if not local_path:
                    raise RuntimeError(f"Failed to download: {source_url}")
                content_hash_id = self.store.put(
                    local_path, metadata=metadata, asset_type=asset_type,
                )
                stored_path = self.store.get(content_hash_id)
                # Clean up temp download if it's not the stored path
                if stored_path and Path(local_path) != stored_path:
                    Path(local_path).unlink(missing_ok=True)
                return AssetResult(
                    asset_id=content_hash_id,
                    asset_type=asset_type,
                    source="remote",
                    path=str(stored_path) if stored_path else str(local_path),
                    metadata=metadata,
                )
            else:
                raise ValueError("No source_path or source_url in result")

    async def _download(self, url: str) -> Optional[Path]:
        """Download a file from URL to a temp location."""
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        return None
                    # Determine extension from content type
                    ct = resp.headers.get("content-type", "")
                    ext = _ext_from_content_type(ct)
                    # Write to temp file
                    tmp = Path(f"_asset_dl_{hashlib.md5(url.encode()).hexdigest()[:8]}{ext}")
                    with open(tmp, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)
                    return tmp
        except Exception:
            return None


def _ext_from_content_type(ct: str) -> str:
    """Map content-type to file extension."""
    ct = ct.split(";")[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/ogg": ".ogg",
    }.get(ct, ".bin")
