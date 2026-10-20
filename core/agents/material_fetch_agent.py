"""
素材检索Agent - 为内容场景检索匹配的素材。

职责：
- 消费内容分析Agent产出的ContentStructure
- 为每个内容场景（非纯文字场景）检索匹配素材
- 多源搜索（Pexels / Pixabay / Unsplash）
- AI 生图（E2: Bailian WanX / OpenAI DALL-E）
- AI 生视频（E3: DashScope Wan / Kling）
- 下载并缓存素材
- 质量评分
- 降级：无结果或下载失败时生成占位符

设计特点：
- 复用现有 material_api / material_downloader 基础设施
- 依赖注入：api_manager 和 downloader 可注入，便于离线测试
- 容错：任何单场景失败都降级为占位符，不影响整体
"""

import os
import asyncio
import time
import hashlib
import logging
from pathlib import Path
from typing import List, Optional, Callable, Any

from core.agents.base_agent import BaseAgent
from core.models import (
    Message,
    ContentStructure,
    Scene,
    MaterialAsset,
    SceneMaterialMap,
)
from services.config import load_config

logger = logging.getLogger(__name__)


# 默认缓存目录
DEFAULT_CACHE_DIR = "data/material_cache"
# 默认每个场景检索的素材数
DEFAULT_MATERIALS_PER_SCENE = 1
# 4K分辨率基准，用于质量评分
_QUALITY_BASELINE_PIXELS = 4096 * 2304


def _default_downloader(download_url: str, local_path: str, timeout: int = 30) -> bool:
    """默认的同步下载器。

    Args:
        download_url: 下载URL
        local_path: 本地保存路径
        timeout: 超时时间（秒）

    Returns:
        True如果下载成功
    """
    try:
        import requests

        # 已存在则视为成功（缓存命中）
        if Path(local_path).exists() and Path(local_path).stat().st_size > 0:
            return True

        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(download_url, stream=True, timeout=timeout)
        response.raise_for_status()

        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # 校验文件非空
        return Path(local_path).exists() and Path(local_path).stat().st_size > 0
    except Exception:
        return False


class MaterialFetchAgent(BaseAgent):
    """素材检索Agent。

    为内容场景检索、下载、缓存匹配的素材，输出SceneMaterialMap。

    Attributes:
        api_manager: 素材API管理器（可注入）
        downloader: 下载函数 (download_url, local_path) -> bool（可注入）
        cache_dir: 缓存目录
        materials_per_scene: 每个场景检索的素材数
        enable_download: 是否实际下载（测试时可关闭）
    """

    def __init__(
        self,
        agent_id: str = "material_fetch",
        name: str = "MaterialFetchAgent",
        api_manager: Any = None,
        downloader: Optional[Callable[[str, str], bool]] = None,
        cache_dir: str = DEFAULT_CACHE_DIR,
        materials_per_scene: int = DEFAULT_MATERIALS_PER_SCENE,
        enable_download: bool = True,
        prefer_video: bool = True,
        video_module: Any = None,
    ):
        """初始化素材检索Agent。

        Args:
            agent_id: Agent ID
            name: Agent名称
            api_manager: 可选的API管理器。None时惰性从环境变量构建。
                         传入False强制禁用API（仅占位符，便于测试）。
            downloader: 可选的下载函数。None时使用默认同步下载器。
            cache_dir: 缓存目录
            materials_per_scene: 每个场景检索的素材数
            enable_download: 是否实际下载素材
        """
        super().__init__(agent_id, name)
        self._api_manager = api_manager
        self._api_disabled = api_manager is False
        self._downloader = downloader or _default_downloader
        self.cache_dir = cache_dir
        self.materials_per_scene = max(1, materials_per_scene)
        self.enable_download = enable_download
        # D5: 视频素材
        self.prefer_video = prefer_video
        self._video_module = video_module
        self._video_disabled = video_module is False
        # E2: AI 生图
        self._ai_image_enabled = True  # 默认启用 AI 生图
        self._ai_generator = None
        self._ai_disabled = False
        # E3: AI 生视频
        self._ai_video_enabled = True  # 默认启用 AI 生视频
        self._ai_video_generator = None
        self._ai_video_disabled = False
        # E5: provider 选择走 config.yaml(ai.image_provider / ai.video_provider)
        try:
            self._config = load_config("config.yaml")
        except Exception as e:  # pragma: no cover - 兜底
            logger.warning(f"加载 config.yaml 失败,使用空配置: {e}")
            self._config = {}

    # ---------- 惰性加载API管理器 ----------

    @property
    def api_manager(self):
        """惰性构建API管理器（从环境变量读取密钥）。"""
        if self._api_disabled:
            return None
        if self._api_manager is None:
            self._api_manager = self._build_api_manager()
        return self._api_manager

    @property
    def ai_generator(self):
        """惰性构建AI生图器(E2/E5)。按 config["ai"]["image_provider"] 选择 provider。"""
        if self._ai_generator is not None:
            return self._ai_generator
        provider = "bailian"  # 兜底默认,日志里也用到
        try:
            provider = self._config.get("ai", {}).get(
                "image_provider", "bailian"
            )
            if provider == "comfyui":
                # 本地 ComfyUI:不需要 API key
                from services.ai_image.comfyui_generator import (
                    ComfyUIImageGenerator,
                )
                self._ai_generator = ComfyUIImageGenerator()
                logger.info("AI 生图器已启用（ComfyUI 本地）")
            elif provider == "openai":
                from services.ai_image import OpenAIImageGenerator
                api_key = os.getenv("OPENAI_API_KEY", "")
                if not api_key:
                    logger.info("未配置 OPENAI_API_KEY,跳过 AI 生图")
                    return None
                self._ai_generator = OpenAIImageGenerator(api_key=api_key)
                logger.info("AI 生图器已启用（OpenAI）")
            else:
                # bailian / 其它未识别值都走 bailian 兜底,保持向后兼容
                from services.ai_image import BailianImageGenerator
                api_key = os.getenv("BAILIAN_API_KEY", "") or os.getenv(
                    "DASHSCOPE_API_KEY", ""
                )
                if not api_key:
                    logger.info(
                        "未配置 BAILIAN_API_KEY/DASHSCOPE_API_KEY,跳过 AI 生图"
                    )
                    return None
                self._ai_generator = BailianImageGenerator(api_key=api_key)
                logger.info("AI 生图器已启用（Bailian WanX）")
        except Exception as e:
            logger.warning(
                f"AI 生图 provider {provider} 初始化失败: {e}"
            )
            self._ai_generator = None
        return self._ai_generator

    @property
    def ai_video_generator(self):
        """惰性构建AI生视频器(E3/E5)。按 config["ai"]["video_provider"] 选择 provider。"""
        if self._ai_video_generator is not None:
            return self._ai_video_generator
        provider = "dashscope"  # 兜底默认
        try:
            provider = self._config.get("ai", {}).get(
                "video_provider", "dashscope"
            )
            if provider == "comfyui":
                # 本地 ComfyUI:不需要 API key
                from services.ai_video.comfyui_generator import (
                    ComfyUIVideoGenerator,
                )
                self._ai_video_generator = ComfyUIVideoGenerator()
                logger.info("AI 生视频器已启用（ComfyUI 本地）")
            else:
                # dashscope / 其它未识别值都走 dashscope 兜底,保持向后兼容
                from services.ai_video import DashScopeVideoGenerator
                api_key = os.getenv("DASHSCOPE_API_KEY", "")
                if not api_key:
                    logger.info(
                        "未配置 DASHSCOPE_API_KEY,跳过 AI 生视频"
                    )
                    return None
                self._ai_video_generator = DashScopeVideoGenerator(
                    api_key=api_key
                )
                logger.info("AI 生视频器已启用（DashScope Wan）")
        except Exception as e:
            logger.warning(
                f"AI 生视频 provider {provider} 初始化失败: {e}"
            )
            self._ai_video_generator = None
        return self._ai_video_generator

    def _build_api_manager(self):
        """从环境变量构建API管理器。

        Returns:
            APIManager实例，无任何密钥时返回None
        """
        try:
            from core.material_providers import (
                APIManager, PexelsAPI, PixabayAPI, UnsplashAPI,
            )
        except Exception as e:
            self.logger.warning(f"无法导入素材API模块: {e}")
            return None

        manager = APIManager()
        registered = 0

        pexels_key = os.environ.get("PEXELS_API_KEY", "")
        if pexels_key:
            manager.register_api("pexels", PexelsAPI(pexels_key))
            registered += 1

        pixabay_key = os.environ.get("PIXABAY_API_KEY", "")
        if pixabay_key:
            manager.register_api("pixabay", PixabayAPI(pixabay_key))
            registered += 1

        unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
        if unsplash_key:
            manager.register_api("unsplash", UnsplashAPI(unsplash_key))
            registered += 1

        if registered == 0:
            self.logger.warning("未配置任何素材API密钥，将全部使用占位符")
            self._api_disabled = True
            return None

        self.logger.info(f"已注册 {registered} 个素材API")
        return manager

    # ---------- 主执行入口 ----------

    async def execute(self, message: Message) -> Message:
        """执行素材检索任务。

        Args:
            message: 包含ContentStructure的任务消息

        Returns:
            包含SceneMaterialMap的结果消息
        """
        start_time = time.time()
        self.log_task_start(message)
        self.set_status("processing")

        try:
            # 1. 解析内容结构
            content = self._parse_content(message)
            content_scenes = content.get_content_scenes()

            if not content_scenes:
                self.logger.info("无需素材的内容（全为文字场景）")

            # 2. 为每个内容场景检索素材
            material_map = SceneMaterialMap()
            for scene in content_scenes:
                assets = self._fetch_for_scene(scene)
                for asset in assets:
                    material_map.add(scene.scene_id, asset)

            # 3. 统计元数据
            material_map.metadata = {
                "total_content_scenes": len(content_scenes),
                "match_rate": round(material_map.match_rate(len(content_scenes)), 3),
                "real_count": material_map.real_count,
                "placeholder_count": material_map.placeholder_count,
                "sources_used": self._sources_used(material_map),
            }

            duration = time.time() - start_time
            self.set_status("idle")
            result_msg = self.create_success_message(message, material_map.to_dict())
            self.log_task_end(result_msg, duration)
            self.logger.info(f"\n{material_map.get_summary()}")
            return result_msg

        except Exception as e:
            self.logger.error(f"素材检索失败: {e}", exc_info=True)
            return await self.handle_error(e, message)

    async def handle_error(self, error: Exception, message: Message) -> Message:
        """处理错误。"""
        self.set_status("error")
        return self.create_error_message(message, str(error))

    # ---------- 内容解析 ----------

    def _parse_content(self, message: Message) -> ContentStructure:
        """从消息中解析内容结构。

        Args:
            message: 任务消息

        Returns:
            ContentStructure对象
        """
        payload = message.payload or {}
        # payload本身就是ContentStructure的dict，或嵌套在content字段
        data = payload.get("content", payload)
        content = ContentStructure.from_dict(data)

        # 轻量结构校验：素材检索只关心结构是否可用，
        # 不重复校验时长偏差（那是内容分析Agent的职责）
        if not content.title or not content.title.strip():
            raise ValueError("无效的内容结构: 标题为空")
        if not content.scenes:
            raise ValueError("无效的内容结构: 场景列表为空")

        return content

    # ---------- 单场景素材检索 ----------

    def _fetch_for_scene(self, scene: Scene) -> List[MaterialAsset]:
        """为单个场景检索素材。

        检索顺序:
        1. 视频素材（D5，如果启用）
        2. AI 生视频（E3，如果启用）
        3. AI 生图（E2，如果启用）
        4. 素材库搜索（Pexels/Pixabay/Unsplash）
        5. 占位符（降级）

        Args:
            scene: 内容场景

        Returns:
            素材资产列表（至少1个，检索失败时为占位符）
        """
        queries = self._build_queries(scene)

        # D5: 优先尝试视频素材(本身动态)
        if self.prefer_video and self.enable_download:
            video_asset = self._fetch_video_for_scene(scene, queries)
            if video_asset is not None:
                return [video_asset]

        # E3: AI 生视频（在 AI 生图之前尝试）
        if self._ai_video_enabled and self.enable_download:
            ai_video_asset = self._fetch_ai_video_for_scene(scene, queries)
            if ai_video_asset is not None:
                return [ai_video_asset]

        # E2: AI 生图（在素材库搜索之前尝试）
        if self._ai_image_enabled and self.enable_download:
            ai_asset = self._fetch_ai_image_for_scene(scene, queries)
            if ai_asset is not None:
                return [ai_asset]

        # 图片素材
        manager = self.api_manager
        if manager is None:
            return [self._make_placeholder(scene)]

        assets: List[MaterialAsset] = []
        for api_name, api_result in self._search(manager, queries):
            asset = self._download_and_build(scene, api_name, api_result)
            if asset is not None:
                assets.append(asset)
            if len(assets) >= self.materials_per_scene:
                break

        # 降级：无任何素材则占位符
        if not assets:
            self.logger.info(f"场景 {scene.scene_id} 无匹配素材，使用占位符")
            assets.append(self._make_placeholder(scene))

        return assets

    # ---------- 视频素材检索(D5) ----------

    @property
    def video_module(self):
        """惰性构建视频素材模块。"""
        if self._video_disabled:
            return None
        if self._video_module is None:
            try:
                from core.stock_video_module import StockVideoModule
                self._video_module = StockVideoModule(orientation="portrait")
            except Exception as e:
                self.logger.warning(f"无法加载视频素材模块: {e}")
                self._video_disabled = True
                return None
        return self._video_module

    def _fetch_video_for_scene(
        self, scene: Scene, queries: List[str]
    ) -> Optional[MaterialAsset]:
        """尝试为场景检索并下载视频素材。

        Args:
            scene: 场景
            queries: 检索查询列表

        Returns:
            视频MaterialAsset，无则None
        """
        module = self.video_module
        if module is None:
            return None

        for query in queries:
            try:
                results = module.search_pexels(query, min_duration=3)
                if not results:
                    results = module.search_pixabay(query, min_duration=3)
            except Exception as e:
                self.logger.debug(f"视频搜索 '{query}' 失败: {e}")
                continue

            for item in results[:3]:
                try:
                    local_path = module.download_video(item.url)
                except Exception:
                    local_path = ""
                if local_path:
                    self.logger.info(f"场景 {scene.scene_id} 视频素材: {item.provider}")
                    return MaterialAsset(
                        asset_id=f"video_{scene.scene_id}",
                        scene_id=scene.scene_id,
                        source=item.provider,
                        media_type="video",
                        url=item.url,
                        download_url=item.url,
                        local_path=local_path,
                        width=getattr(item, "width", 0),
                        height=getattr(item, "height", 0),
                        quality_score=0.9,
                        keywords=scene.keywords,
                        is_placeholder=False,
                    )
        return None

    # ---------- AI 生图（E2） ----------

    def _fetch_ai_image_for_scene(
        self, scene: Scene, queries: List[str]
    ) -> Optional[MaterialAsset]:
        """使用 AI 为场景生成图片。

        Args:
            scene: 场景
            queries: 检索查询列表（用于构建 prompt）

        Returns:
            MaterialAsset，失败返回 None
        """
        generator = self.ai_generator
        if generator is None:
            return None

        try:
            # 构建 prompt（使用第一个查询）
            prompt = queries[0] if queries else scene.text
            if not prompt:
                return None

            # 确定尺寸（根据视频分辨率）
            from services.ai_image.base import ImageSize
            size = ImageSize.PORTRAIT_1080x1920  # 默认竖屏

            # 异步调用生图
            from services.ai_image import AIImageRequest, AIProvider

            request = AIImageRequest(
                prompt=prompt,
                provider=AIProvider.BAILIAN,
                model="wan2.6-t2i",
                size=size,
                n=1,
            )

            # 在同步上下文中运行异步函数
            try:
                loop = asyncio.get_running_loop()
                # 如果已有运行中的 loop，使用 create_task
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        asyncio.run, generator.generate(request)
                    )
                    result = future.result(timeout=120)
            except RuntimeError:
                # 没有运行中的 loop，直接使用 asyncio.run
                result = asyncio.run(generator.generate(request))

            if result.success and result.image_path:
                self.logger.info(
                    f"场景 {scene.scene_id} AI 生图成功: {result.image_path}"
                )
                return MaterialAsset(
                    asset_id=f"ai_image_{scene.scene_id}",
                    scene_id=scene.scene_id,
                    source="bailian",
                    media_type="image",
                    url=result.image_url or "",
                    download_url=result.image_url or "",
                    local_path=result.image_path,
                    width=1080,
                    height=1920,
                    quality_score=0.95,
                    keywords=scene.keywords,
                    is_placeholder=False,
                )
            else:
                self.logger.debug(f"AI 生图失败: {result.error}")
                return None
        except Exception as e:
            self.logger.warning(f"AI 生图异常: {e}")
            return None

    # ---------- AI 生视频（E3） ----------

    def _fetch_ai_video_for_scene(
        self, scene: Scene, queries: List[str]
    ) -> Optional[MaterialAsset]:
        """使用 AI 为场景生成视频。

        Args:
            scene: 场景
            queries: 检索查询列表（用于构建 prompt）

        Returns:
            MaterialAsset，失败返回 None
        """
        generator = self.ai_video_generator
        if generator is None:
            return None

        try:
            # 构建 prompt（使用第一个查询）
            prompt = queries[0] if queries else scene.text
            if not prompt:
                return None

            # 确定尺寸（根据视频分辨率）
            from services.ai_video.base import VideoSize

            size = VideoSize.PORTRAIT_9_16  # 默认竖屏

            # 异步调用生视频
            from services.ai_video import VideoGenerationRequest, VideoProvider

            request = VideoGenerationRequest(
                prompt=prompt,
                provider=VideoProvider.DASHSCOPE,
                model="wan2.7-t2v",
                size=size,
                duration=5,  # 默认 5 秒
            )

            # 在同步上下文中运行异步生成器
            import concurrent.futures

            try:
                loop = asyncio.get_running_loop()
                # 如果已有运行中的 loop，使用 ThreadPoolExecutor
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    def run_generator():
                        import asyncio
                        # 收集所有进度更新
                        results = []
                        async def collect():
                            async for progress in generator.generate(request):
                                results.append(progress)
                                if progress.video_path:
                                    return results
                        asyncio.run(collect())
                        return results

                    future = pool.submit(run_generator)
                    results = future.result(timeout=300)  # 视频生成可能需要更长时间

                    # 找到最后一个成功的结果
                    if results:
                        last = results[-1]
                        if hasattr(last, 'video_path') and last.video_path:
                            video_path = last.video_path
                            self.logger.info(
                                f"场景 {scene.scene_id} AI 生视频成功: {video_path}"
                            )
                            return MaterialAsset(
                                asset_id=f"ai_video_{scene.scene_id}",
                                scene_id=scene.scene_id,
                                source="dashscope",
                                media_type="video",
                                url=last.video_url if hasattr(last, 'video_url') else "",
                                download_url=last.video_url if hasattr(last, 'video_url') else "",
                                local_path=video_path,
                                width=1080,
                                height=1920,
                                quality_score=0.98,
                                keywords=scene.keywords,
                                is_placeholder=False,
                            )

            except RuntimeError:
                # 没有运行中的 loop，直接使用 asyncio.run
                results = []
                async def collect():
                    async for progress in generator.generate(request):
                        results.append(progress)
                        if progress.video_path:
                            return results
                asyncio.run(collect())

                if results:
                    last = results[-1]
                    if hasattr(last, 'video_path') and last.video_path:
                        video_path = last.video_path
                        self.logger.info(
                            f"场景 {scene.scene_id} AI 生视频成功: {video_path}"
                        )
                        return MaterialAsset(
                            asset_id=f"ai_video_{scene.scene_id}",
                            scene_id=scene.scene_id,
                            source="dashscope",
                            media_type="video",
                            url=last.video_url if hasattr(last, 'video_url') else "",
                            download_url=last.video_url if hasattr(last, 'video_url') else "",
                            local_path=video_path,
                            width=1080,
                            height=1920,
                            quality_score=0.98,
                            keywords=scene.keywords,
                            is_placeholder=False,
                        )

            self.logger.debug(f"AI 生视频失败: 无结果")
            return None
        except Exception as e:
            self.logger.warning(f"AI 生视频异常: {e}")
            return None

    def _build_queries(self, scene: Scene) -> List[str]:
        """构建场景的检索查询列表（按优先级）。

        Args:
            scene: 场景

        Returns:
            查询字符串列表
        """
        queries: List[str] = []
        keywords = [k for k in scene.keywords if k and k.strip()]

        if keywords:
            # 优先用前两个关键词组合
            queries.append(" ".join(keywords[:2]))
            # 再逐个关键词兜底
            for kw in keywords:
                if kw not in queries:
                    queries.append(kw)

        # 最后兜底用场景文字
        if scene.text and scene.text.strip():
            queries.append(scene.text.strip()[:20])

        return queries or ["abstract background"]

    def _search(self, manager, queries: List[str]) -> List:
        """按查询列表搜索，返回第一个有结果的查询的结果。

        Args:
            manager: API管理器
            queries: 查询列表

        Returns:
            [(api_name, APIResult), ...]
        """
        top_k = self.materials_per_scene * 2
        for query in queries:
            try:
                results = manager.search_best(query, top_k=top_k)
                if results:
                    return results
            except Exception as e:
                self.logger.debug(f"查询 '{query}' 失败: {e}")
        return []

    def _download_and_build(
        self, scene: Scene, api_name: str, api_result
    ) -> Optional[MaterialAsset]:
        """下载素材并构建资产对象。

        Args:
            scene: 场景
            api_name: 来源API名称
            api_result: APIResult对象

        Returns:
            MaterialAsset，下载失败返回None
        """
        local_path = None
        if self.enable_download:
            local_path = self._cache_path(api_result.download_url)
            ok = self._downloader(api_result.download_url, local_path)
            if not ok:
                self.logger.debug(f"下载失败: {api_result.download_url}")
                return None

        return MaterialAsset(
            asset_id=self._asset_id(api_name, api_result.id),
            scene_id=scene.scene_id,
            source=api_name,
            media_type=getattr(api_result, "media_type", "image"),
            url=getattr(api_result, "url", ""),
            download_url=api_result.download_url,
            local_path=local_path,
            width=getattr(api_result, "width", 0),
            height=getattr(api_result, "height", 0),
            quality_score=self._estimate_quality(api_result),
            keywords=scene.keywords,
            is_placeholder=False,
        )

    # ---------- 辅助方法 ----------

    def _make_placeholder(self, scene: Scene) -> MaterialAsset:
        """为场景生成占位符素材。

        Args:
            scene: 场景

        Returns:
            占位符MaterialAsset
        """
        return MaterialAsset(
            asset_id=f"placeholder_{scene.scene_id}",
            scene_id=scene.scene_id,
            source="placeholder",
            media_type="image",
            keywords=scene.keywords,
            is_placeholder=True,
            quality_score=0.0,
        )

    def _estimate_quality(self, api_result) -> float:
        """基于分辨率估算素材质量。

        Args:
            api_result: APIResult对象

        Returns:
            质量评分 (0.3-1.0)
        """
        width = getattr(api_result, "width", 0)
        height = getattr(api_result, "height", 0)
        pixels = width * height
        quality = min(1.0, pixels / _QUALITY_BASELINE_PIXELS) if pixels > 0 else 0.3
        return round(max(0.3, quality), 3)

    def _cache_path(self, url: str) -> str:
        """计算URL的缓存路径。

        Args:
            url: 素材URL

        Returns:
            本地缓存路径
        """
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return str(Path(self.cache_dir) / f"{url_hash}.jpg")

    def _asset_id(self, api_name: str, result_id: str) -> str:
        """生成素材ID。

        Args:
            api_name: API名称
            result_id: 素材原始ID

        Returns:
            素材ID
        """
        return f"{api_name}_{result_id}"

    def _sources_used(self, material_map: SceneMaterialMap) -> List[str]:
        """统计使用的素材来源。

        Args:
            material_map: 素材映射

        Returns:
            来源列表（去重）
        """
        sources = set()
        for assets in material_map.scene_materials.values():
            for a in assets:
                sources.add(a.source)
        return sorted(sources)
