# -*- coding: utf-8 -*-
"""
素材API集成
支持Pexels和Pixabay等素材源的API接入
"""
import requests
import json
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path
from abc import ABC, abstractmethod
import hashlib


@dataclass
class APIResult:
    """API结果"""
    id: str
    title: str
    url: str
    download_url: str
    width: int = 0
    height: int = 0
    media_type: str = "image"
    tags: List[str] = None


class BaseMaterialAPI(ABC):
    """素材API基类"""

    def __init__(self, api_key: str):
        """
        初始化API客户端

        Args:
            api_key: API密钥
        """
        self.api_key = api_key
        self.timeout = 10
        self.headers = self._build_headers()

    @abstractmethod
    def search(self, query: str, per_page: int = 15, page: int = 1) -> List[APIResult]:
        """
        搜索素材

        Args:
            query: 搜索关键词
            per_page: 每页数量
            page: 页码

        Returns:
            APIResult列表
        """
        pass

    @abstractmethod
    def get_trending(self, per_page: int = 20) -> List[APIResult]:
        """获取热门素材"""
        pass

    def _build_headers(self) -> Dict:
        """构建请求头"""
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def _request(self, url: str, params: Dict = None) -> Optional[Dict]:
        """发送HTTP请求"""
        try:
            response = requests.get(
                url,
                params=params,
                headers=self.headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"[API] Request failed: {e}")
            return None


class PexelsAPI(BaseMaterialAPI):
    """Pexels API客户端"""

    BASE_URL = "https://api.pexels.com/v1"

    def __init__(self, api_key: str):
        super().__init__(api_key)

    def _build_headers(self) -> Dict:
        headers = super()._build_headers()
        headers["Authorization"] = self.api_key
        return headers

    def search(self, query: str, per_page: int = 15, page: int = 1) -> List[APIResult]:
        """搜索Pexels图片"""
        params = {
            "query": query,
            "per_page": min(per_page, 80),
            "page": page
        }

        data = self._request(f"{self.BASE_URL}/search", params)

        if not data or "photos" not in data:
            return []

        results = []
        for photo in data["photos"]:
            result = APIResult(
                id=str(photo["id"]),
                title=query,
                url=photo["url"],
                download_url=photo["src"]["large"],
                width=photo["width"],
                height=photo["height"],
                media_type="image",
                tags=[query]
            )
            results.append(result)

        return results

    def get_trending(self, per_page: int = 20) -> List[APIResult]:
        """获取Pexels热门图片"""
        params = {
            "per_page": min(per_page, 80),
            "page": 1
        }

        data = self._request(f"{self.BASE_URL}/curated", params)

        if not data or "photos" not in data:
            return []

        results = []
        for photo in data["photos"]:
            result = APIResult(
                id=str(photo["id"]),
                title="Featured",
                url=photo["url"],
                download_url=photo["src"]["large"],
                width=photo["width"],
                height=photo["height"],
                media_type="image",
                tags=["trending"]
            )
            results.append(result)

        return results


class PixabayAPI(BaseMaterialAPI):
    """Pixabay API客户端"""

    BASE_URL = "https://pixabay.com/api"

    def __init__(self, api_key: str):
        super().__init__(api_key)

    def search(self, query: str, per_page: int = 15, page: int = 1) -> List[APIResult]:
        """搜索Pixabay图片"""
        params = {
            "key": self.api_key,
            "q": query,
            "per_page": min(per_page, 200),
            "page": page,
            "image_type": "photo",
            "order": "popular",
            "safesearch": True
        }

        data = self._request(self.BASE_URL, params)

        if not data or "hits" not in data:
            return []

        results = []
        for hit in data["hits"]:
            result = APIResult(
                id=str(hit["id"]),
                title=query,
                url=hit["pageURL"],
                download_url=hit["largeImageURL"],
                width=hit["imageWidth"],
                height=hit["imageHeight"],
                media_type="image",
                tags=[query, hit.get("type", "photo")]
            )
            results.append(result)

        return results

    def get_trending(self, per_page: int = 20) -> List[APIResult]:
        """获取Pixabay热门图片"""
        params = {
            "key": self.api_key,
            "per_page": min(per_page, 200),
            "page": 1,
            "image_type": "photo",
            "order": "popular",
            "safesearch": True
        }

        data = self._request(self.BASE_URL, params)

        if not data or "hits" not in data:
            return []

        results = []
        for hit in data["hits"]:
            result = APIResult(
                id=str(hit["id"]),
                title="Trending",
                url=hit["pageURL"],
                download_url=hit["largeImageURL"],
                width=hit["imageWidth"],
                height=hit["imageHeight"],
                media_type="image",
                tags=["trending"]
            )
            results.append(result)

        return results


class UnsplashAPI(BaseMaterialAPI):
    """Unsplash API客户端"""

    BASE_URL = "https://api.unsplash.com"

    def __init__(self, api_key: str):
        super().__init__(api_key)

    def _build_headers(self) -> Dict:
        headers = super()._build_headers()
        headers["Authorization"] = f"Client-ID {self.api_key}"
        return headers

    def search(self, query: str, per_page: int = 15, page: int = 1) -> List[APIResult]:
        """搜索Unsplash图片"""
        params = {
            "query": query,
            "per_page": min(per_page, 30),
            "page": page,
            "order_by": "relevant"
        }

        data = self._request(f"{self.BASE_URL}/search/photos", params)

        if not data or "results" not in data:
            return []

        results = []
        for photo in data["results"]:
            result = APIResult(
                id=photo["id"],
                title=photo.get("description", query),
                url=photo["links"]["html"],
                download_url=photo["urls"]["full"],
                width=photo["width"],
                height=photo["height"],
                media_type="image",
                tags=[query] + [tag.get("title", "") for tag in photo.get("tags", [])]
            )
            results.append(result)

        return results

    def get_trending(self, per_page: int = 20) -> List[APIResult]:
        """获取Unsplash热门图片"""
        params = {
            "per_page": min(per_page, 30),
            "order_by": "popular"
        }

        data = self._request(f"{self.BASE_URL}/photos", params)

        if not isinstance(data, list):
            return []

        results = []
        for photo in data:
            result = APIResult(
                id=photo["id"],
                title=photo.get("description", "Featured"),
                url=photo["links"]["html"],
                download_url=photo["urls"]["full"],
                width=photo["width"],
                height=photo["height"],
                media_type="image",
                tags=["trending"]
            )
            results.append(result)

        return results


class APIManager:
    """API管理器 - 统一管理多个API"""

    def __init__(self):
        self.apis: Dict[str, BaseMaterialAPI] = {}

    def register_api(self, name: str, api_client: BaseMaterialAPI):
        """注册API客户端"""
        self.apis[name] = api_client
        print(f"[APIManager] Registered API: {name}")

    def search_all(self, query: str, per_page: int = 10) -> Dict[str, List[APIResult]]:
        """在所有API中搜索"""
        results = {}

        for api_name, api_client in self.apis.items():
            try:
                api_results = api_client.search(query, per_page)
                results[api_name] = api_results
                print(f"[APIManager] {api_name}: Found {len(api_results)} results")
            except Exception as e:
                print(f"[APIManager] {api_name} search failed: {e}")
                results[api_name] = []

        return results

    def search_best(self, query: str, top_k: int = 5) -> List[Tuple[str, APIResult]]:
        """搜索并返回最佳结果"""
        all_results = self.search_all(query, per_page=top_k)

        results = []
        for api_name, api_results in all_results.items():
            for result in api_results[:top_k]:
                results.append((api_name, result))

        # 按分辨率排序
        results.sort(
            key=lambda x: x[1].width * x[1].height,
            reverse=True
        )

        return results[:top_k]


class CacheManager:
    """缓存管理器 - 管理下载的素材缓存"""

    def __init__(self, cache_dir: str = None):
        """
        初始化缓存管理器

        Args:
            cache_dir: 缓存目录
        """
        self.cache_dir = Path(cache_dir or "data/material_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_index = self._load_index()

    def _load_index(self) -> Dict:
        """加载缓存索引"""
        index_file = self.cache_dir / "index.json"
        if index_file.exists():
            try:
                with open(index_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[CacheManager] Failed to load index: {e}")
        return {}

    def _save_index(self):
        """保存缓存索引"""
        index_file = self.cache_dir / "index.json"
        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(self.cache_index, f, indent=2)

    def get_cache_path(self, url: str) -> Path:
        """获取缓存路径"""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return self.cache_dir / f"{url_hash}.jpg"

    def is_cached(self, url: str) -> bool:
        """检查是否已缓存"""
        return url in self.cache_index

    def get_cached_path(self, url: str) -> Optional[str]:
        """获取缓存文件路径"""
        if url in self.cache_index:
            path = self.cache_index[url]
            if Path(path).exists():
                return path
        return None

    def add_to_cache(self, url: str, file_path: str):
        """添加到缓存"""
        self.cache_index[url] = file_path
        self._save_index()

    def clear_cache(self, max_age_days: int = 30):
        """清理过期缓存"""
        import time
        import os

        current_time = time.time()
        max_age_seconds = max_age_days * 24 * 3600

        to_remove = []
        for url, path in list(self.cache_index.items()):
            if Path(path).exists():
                file_age = current_time - Path(path).stat().st_mtime
                if file_age > max_age_seconds:
                    Path(path).unlink()
                    to_remove.append(url)
            else:
                to_remove.append(url)

        for url in to_remove:
            del self.cache_index[url]

        self._save_index()
        print(f"[CacheManager] Cleared {len(to_remove)} expired items")

    def get_stats(self) -> Dict:
        """获取缓存统计"""
        total_size = 0
        for path in self.cache_dir.glob("*.jpg"):
            total_size += path.stat().st_size

        return {
            "cached_urls": len(self.cache_index),
            "total_size_mb": total_size / (1024 * 1024),
            "cache_dir": str(self.cache_dir)
        }


# 便捷函数
def create_pexels_api(api_key: str) -> PexelsAPI:
    """创建Pexels API客户端"""
    return PexelsAPI(api_key)


def create_pixabay_api(api_key: str) -> PixabayAPI:
    """创建Pixabay API客户端"""
    return PixabayAPI(api_key)


def create_unsplash_api(api_key: str) -> UnsplashAPI:
    """创建Unsplash API客户端"""
    return UnsplashAPI(api_key)


def create_api_manager() -> APIManager:
    """创建API管理器"""
    return APIManager()
