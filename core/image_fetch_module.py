# -*- coding: utf-8 -*-
"""
联网图库抓取模块 - Pexels/Unsplash/网页爬虫
自动根据脚本关键词下载匹配配图
"""
import os
import re
import time
import random
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import quote_plus, urljoin
from dataclasses import dataclass

import config


@dataclass
class ImageResult:
    """图片下载结果"""
    url: str
    local_path: str
    width: int
    height: int
    source: str
    keywords: str


class ImageFetchModule:
    """联网图库抓取模块"""

    # Pexels API (免费每天200张)
    PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")
    PEXELS_URL = "https://api.pexels.com/v1/search"

    # Unsplash API (免费每月50次请求)
    UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    UNSPLASH_URL = "https://api.unsplash.com/search/photos"

    # 备用搜索引擎
    BING_IMAGE_URL = "https://www.bing.com/images/search?q="

    # 请求头
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    def __init__(self):
        self.output_dir = config.MATERIAL_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._session.headers.update(self.HEADERS)
        self._pexels_used = 0
        self._unsplash_used = 0

    def fetch_from_pexels(self, keywords: str, per_page: int = 5) -> List[Dict]:
        """从Pexels抓取图片"""
        if not self.PEXELS_API_KEY:
            return []

        try:
            headers = {
                "Authorization": self.PEXELS_API_KEY
            }
            params = {
                "query": keywords,
                "per_page": per_page,
                "orientation": "portrait",
                "size": "large"
            }

            response = self._session.get(
                self.PEXELS_URL,
                headers=headers,
                params=params,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                self._pexels_used += len(data.get("photos", []))
                return data.get("photos", [])
            elif response.status_code == 429:
                print(f"[Pexels] 请求过于频繁，今日配额可能已用尽")
            else:
                print(f"[Pexels] 请求失败: {response.status_code}")

        except Exception as e:
            print(f"[Pexels] 抓取异常: {str(e)}")

        return []

    def fetch_from_unsplash(self, keywords: str, per_page: int = 5) -> List[Dict]:
        """从Unsplash抓取图片"""
        if not self.UNSPLASH_ACCESS_KEY:
            return []

        try:
            headers = {
                "Authorization": f"Client-ID {self.UNSPLASH_ACCESS_KEY}"
            }
            params = {
                "query": keywords,
                "per_page": per_page,
                "orientation": "portrait"
            }

            response = self._session.get(
                self.UNSPLASH_URL,
                headers=headers,
                params=params,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                self._unsplash_used += len(data.get("results", []))
                return data.get("results", [])
            elif response.status_code == 403:
                print(f"[Unsplash] API配额已用尽")
            else:
                print(f"[Unsplash] 请求失败: {response.status_code}")

        except Exception as e:
            print(f"[Unsplash] 抓取异常: {str(e)}")

        return []

    def download_image(self, url: str, filename: str = None) -> Optional[str]:
        """下载单张图片"""
        if not filename:
            filename = f"img_{int(time.time() * 1000)}_{random.randint(1000, 9999)}.jpg"

        output_path = self.output_dir / filename

        try:
            response = self._session.get(url, timeout=15, stream=True)
            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                return str(output_path)
        except Exception as e:
            print(f"[下载] 失败 {url}: {str(e)}")

        return None

    def fetch_and_download(self, keywords: str, count: int = 5) -> Tuple[List[ImageResult], List[str]]:
        """
        抓取并下载图片

        参数:
            keywords: 搜索关键词
            count: 需要下载数量

        返回:
            (图片结果列表, 本地路径列表)
        """
        results = []
        local_paths = []

        # 优先使用Pexels
        if self.PEXELS_API_KEY:
            photos = self.fetch_from_pexels(keywords, per_page=count)
            for photo in photos:
                src = photo.get("src", {})
                orig_url = src.get("original") or src.get("large2x") or src.get("large") or photo.get("url", "")
                if orig_url:
                    local = self.download_image(orig_url)
                    if local:
                        results.append(ImageResult(
                            url=orig_url,
                            local_path=local,
                            width=photo.get("width", 0),
                            height=photo.get("height", 0),
                            source="pexels",
                            keywords=keywords
                        ))
                        local_paths.append(local)

                        if len(results) >= count:
                            break

        # Pexels不够用Unsplash
        if len(results) < count and self.UNSPLASH_ACCESS_KEY:
            remaining = count - len(results)
            photos = self.fetch_from_unsplash(keywords, per_page=remaining)
            for photo in photos:
                urls = photo.get("urls", {})
                orig_url = urls.get("raw") or urls.get("full") or urls.get("regular")
                if orig_url:
                    local = self.download_image(orig_url)
                    if local:
                        results.append(ImageResult(
                            url=orig_url,
                            local_path=local,
                            width=photo.get("width", 0),
                            height=photo.get("height", 0),
                            source="unsplash",
                            keywords=keywords
                        ))
                        local_paths.append(local)

                        if len(results) >= count:
                            break

        return results, local_paths

    def fetch_by_script_keywords(self, script_text: str, count_per_keyword: int = 2) -> Tuple[List[ImageResult], List[str]]:
        """
        根据脚本关键词自动抓取配图

        参数:
            script_text: 脚本文本
            count_per_keyword: 每个关键词抓取数量

        返回:
            (图片结果列表, 本地路径列表)
        """
        # 提取关键词
        keywords = self._extract_keywords(script_text)

        if not keywords:
            print("[图库抓取] 无法从脚本提取关键词")
            return [], []

        all_results = []
        all_paths = []

        print(f"[图库抓取] 从脚本提取到 {len(keywords)} 个关键词")

        for kw in keywords[:5]:
            print(f"  抓取关键词: {kw}")
            results, paths = self.fetch_and_download(kw, count_per_keyword)
            all_results.extend(results)
            all_paths.extend(paths)
            time.sleep(random.uniform(0.5, 1.5))

            if len(all_paths) >= count_per_keyword * 3:
                break

        print(f"[图库抓取] 共抓取 {len(all_paths)} 张图片")
        return all_results, all_paths

    def _extract_keywords(self, text: str) -> List[str]:
        """从文本提取关键词"""
        # 移除标点
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', '', text)

        # 常见停用词
        stopwords = {
            "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
            "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
            "自己", "这", "那", "里", "为", "什么", "可以", "这个", "那个", "如果", "因为",
            "所以", "但是", "而且", "或者", "还是", "以及", "对于", "关于", "通过", "进行",
            "已经", "正在", "将会", "可能", "应该", "必须", "需要", "如何", "怎么", "怎样",
            "为什么", "哪里", "谁", "多少", "几", "什么", "怎样", "这样", "那样", "如此",
            "的话", "的话", "一下", "一点", "一些", "然后", "接着", "首先", "其次", "最后",
            "总之", "总的来说", "实际上", "事实上", "其实", "本身", "之中", "之间", "之间",
            "来", "去", "出", "入", "起", "过", "起来", "下来", "上去", "出去", "进来",
            "一个", "一下", "一点", "一些", "这种", "那种", "各种", "每个", "某些", "其他",
            "大家", "我们", "你们", "他们", "她们", "它们", "自己", "本人", "自己",
            "现在", "目前", "当前", "今天", "明天", "昨天", "今年", "明年", "去年",
            "这里", "那里", "哪里", "这边", "那边", "各处", "到处", "处处",
            "什么", "怎", "怎么", "怎样", "如何", "为何", "为啥", "为啥", "为毛",
            "非常", "特别", "十分", "极其", "相当", "比较", "尤其", "更", "最", "太",
            "做", "当", "作为", "成为", "形成", "造成", "导致", "引起", "产生",
            "使用", "利用", "运用", "应用", "采用", "采纳", "接受", "同意",
            "认为", "觉得", "以为", "表示", "说明", "指出", "提出", "看到",
            "知道", "了解", "认识", "理解", "明白", "清楚", "熟悉", "掌握",
            "开", "始", "终", "完", "成", "结束", "完成", "成功", "完毕",
            "大", "小", "多", "少", "长", "短", "高", "低", "快", "慢",
            "新", "旧", "老", "早", "晚", "先", "后", "前", "后", "中",
        }

        # 按空格分割
        words = text.split()
        keywords = []

        for w in words:
            w = w.strip()
            if len(w) >= 2 and w not in stopwords:
                keywords.append(w)

        # 去重保持顺序
        seen = set()
        unique = []
        for k in keywords:
            if k not in seen:
                seen.add(k)
                unique.append(k)

        return unique[:15]

    def get_usage_stats(self) -> Dict:
        """获取API使用统计"""
        return {
            "pexels_requests": self._pexels_used,
            "unsplash_requests": self._unsplash_used,
            "pexels_configured": bool(self.PEXELS_API_KEY),
            "unsplash_configured": bool(self.UNSPLASH_ACCESS_KEY),
        }


# ==================== 便捷函数 ====================
_module_instance = None


def get_image_fetch_module() -> ImageFetchModule:
    """获取图库抓取模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = ImageFetchModule()
    return _module_instance


def fetch_images_by_keywords(keywords: str, count: int = 5) -> Tuple[List[ImageResult], List[str]]:
    """根据关键词快速抓取图片"""
    return get_image_fetch_module().fetch_and_download(keywords, count)


def fetch_images_by_script(script: str, count_per_keyword: int = 2) -> Tuple[List[ImageResult], List[str]]:
    """根据脚本快速抓取配图"""
    return get_image_fetch_module().fetch_by_script_keywords(script, count_per_keyword)
