# -*- coding: utf-8 -*-
"""
素材下载管理器
处理素材的下载、缓存和本地化
"""
import requests
import threading
from typing import List, Dict, Optional, Callable
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
import queue


class DownloadStatus(Enum):
    """下载状态"""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CACHED = "cached"


@dataclass
class DownloadTask:
    """下载任务"""
    url: str
    local_path: str
    status: DownloadStatus = DownloadStatus.PENDING
    progress: float = 0.0
    error: str = None


class DownloadManager:
    """素材下载管理器"""

    def __init__(self, cache_dir: str = None, max_workers: int = 3):
        """
        初始化下载管理器

        Args:
            cache_dir: 缓存目录
            max_workers: 最大并发下载数
        """
        self.cache_dir = Path(cache_dir or "data/material_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_workers = max_workers
        self.tasks: Dict[str, DownloadTask] = {}
        self.queue = queue.Queue()
        self.workers: List[threading.Thread] = []
        self._running = False
        self.progress_callback: Optional[Callable] = None

    def add_task(self, url: str, local_path: str = None) -> str:
        """
        添加下载任务

        Args:
            url: 下载URL
            local_path: 本地保存路径

        Returns:
            任务ID
        """
        if local_path is None:
            import hashlib
            url_hash = hashlib.md5(url.encode()).hexdigest()
            local_path = str(self.cache_dir / f"{url_hash}.jpg")

        task_id = url
        task = DownloadTask(url=url, local_path=local_path)
        self.tasks[task_id] = task
        self.queue.put(task)

        return task_id

    def start_download(self, num_workers: int = None):
        """启动下载线程"""
        num_workers = num_workers or self.max_workers
        self._running = True

        for _ in range(num_workers):
            worker = threading.Thread(target=self._worker, daemon=True)
            worker.start()
            self.workers.append(worker)

        print(f"[DownloadManager] Started {num_workers} workers")

    def _worker(self):
        """下载线程工作函数"""
        while self._running:
            try:
                task = self.queue.get(timeout=1)

                if task is None:  # 停止信号
                    break

                self._download_file(task)
                self.queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                print(f"[DownloadManager] Worker error: {e}")

    def _download_file(self, task: DownloadTask):
        """下载单个文件"""
        if Path(task.local_path).exists():
            task.status = DownloadStatus.CACHED
            print(f"[DownloadManager] Already cached: {task.url}")
            return

        try:
            task.status = DownloadStatus.DOWNLOADING

            response = requests.get(task.url, stream=True, timeout=30)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            # 创建父目录
            Path(task.local_path).parent.mkdir(parents=True, exist_ok=True)

            # 下载文件
            with open(task.local_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        # 更新进度
                        if total_size > 0:
                            task.progress = downloaded / total_size
                            if self.progress_callback:
                                self.progress_callback(task)

            task.status = DownloadStatus.COMPLETED
            print(f"[DownloadManager] Downloaded: {task.local_path}")

        except Exception as e:
            task.status = DownloadStatus.FAILED
            task.error = str(e)
            print(f"[DownloadManager] Download failed: {task.url} - {e}")

    def wait_for_completion(self):
        """等待所有下载完成"""
        self.queue.join()
        self._running = False

        for _ in self.workers:
            self.queue.put(None)

        for worker in self.workers:
            worker.join()

    def get_task_status(self, task_id: str) -> Optional[DownloadTask]:
        """获取任务状态"""
        return self.tasks.get(task_id)

    def get_all_status(self) -> Dict[str, DownloadStatus]:
        """获取所有任务状态"""
        return {task_id: task.status for task_id, task in self.tasks.items()}

    def get_stats(self) -> Dict:
        """获取下载统计"""
        completed = sum(1 for t in self.tasks.values() if t.status == DownloadStatus.COMPLETED)
        failed = sum(1 for t in self.tasks.values() if t.status == DownloadStatus.FAILED)
        cached = sum(1 for t in self.tasks.values() if t.status == DownloadStatus.CACHED)

        return {
            "total": len(self.tasks),
            "completed": completed,
            "failed": failed,
            "cached": cached,
            "pending": len(self.tasks) - completed - failed - cached
        }


class MaterialFetcher:
    """素材获取器 - 集成API和下载"""

    def __init__(self, api_manager=None, cache_manager=None):
        """
        初始化素材获取器

        Args:
            api_manager: API管理器
            cache_manager: 缓存管理器
        """
        from core.material_providers import create_api_manager
        from core.material_library import MaterialLibrary

        self.api_manager = api_manager or create_api_manager()
        self.cache_manager = cache_manager
        self.download_manager = DownloadManager()
        self.library = MaterialLibrary()

    def fetch_materials(
        self,
        query: str,
        count: int = 10,
        auto_download: bool = True,
        progress_callback: Callable = None
    ) -> List[Dict]:
        """
        获取素材

        Args:
            query: 搜索关键词
            count: 获取数量
            auto_download: 是否自动下载
            progress_callback: 进度回调

        Returns:
            素材列表
        """
        print(f"[MaterialFetcher] Fetching '{query}'...")

        # 搜索素材
        results = self.api_manager.search_best(query, top_k=count)

        if not results:
            print(f"[MaterialFetcher] No results found for '{query}'")
            return []

        materials = []

        for i, (api_name, result) in enumerate(results):
            material_data = {
                "title": result.title,
                "source": api_name,
                "url": result.url,
                "download_url": result.download_url,
                "resolution": f"{result.width}x{result.height}",
                "keywords": result.tags or [query],
                "quality_score": self._estimate_quality(result)
            }

            # 自动下载
            if auto_download:
                import hashlib
                cache_path = f"data/material_cache/{hashlib.md5(result.download_url.encode()).hexdigest()}.jpg"
                self.download_manager.add_task(result.download_url, cache_path)
                material_data["local_path"] = cache_path

            materials.append(material_data)

            # 进度回调
            if progress_callback:
                progress = (i + 1) / len(results)
                progress_callback(progress)

        # 启动下载
        if auto_download and materials:
            print(f"[MaterialFetcher] Starting downloads...")
            self.download_manager.start_download(num_workers=3)
            self.download_manager.wait_for_completion()
            print(f"[MaterialFetcher] Downloads completed")

        return materials

    def _estimate_quality(self, result) -> float:
        """估算素材质量"""
        # 基于分辨率估算
        pixels = result.width * result.height
        quality = min(1.0, pixels / (4096 * 2304))  # 基于4K分辨率

        return max(0.3, quality)  # 最低0.3分

    def add_to_library(self, materials: List[Dict], source_name: str = "fetched"):
        """将获取的素材添加到库"""
        from core.material_library import MaterialSource

        for material in materials:
            try:
                source = MaterialSource[material.get("source", "CC0").upper()]
            except KeyError:
                source = MaterialSource.CC0

            self.library.add_material(
                title=material["title"],
                source=source,
                url=material["url"],
                keywords=material.get("keywords", []),
                quality_score=material.get("quality_score", 0.5),
                local_path=material.get("local_path")
            )

        print(f"[MaterialFetcher] Added {len(materials)} materials to library")

        return self.library


# 便捷函数
def create_download_manager(cache_dir: str = None) -> DownloadManager:
    """创建下载管理器"""
    return DownloadManager(cache_dir)


def create_material_fetcher() -> MaterialFetcher:
    """创建素材获取器"""
    return MaterialFetcher()
