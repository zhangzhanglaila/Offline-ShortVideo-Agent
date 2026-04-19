# -*- coding: utf-8 -*-
"""
Remotion Bridge - Python → Remotion Render Server

Python端通过HTTP调用Remotion渲染服务，生成专业级动画视频。

工作流程:
1. start_server() 启动服务（首次需要bundle项目）
2. render_sync() 发送布局数据，等待渲染完成
3. 返回MP4文件路径

Remotion服务运行在 localhost:3333。
"""
import subprocess
import time
import requests
import os
import shutil
import signal
from pathlib import Path
from typing import Optional

# ==================== Remotion Bridge ====================

class RemotionBridge:
    """
    Python → Remotion 渲染桥接器

    使用静态bundle模式（避免webpack dev server的registerRoot问题）：
    1. npx remotion bundle 创建静态bundle
    2. Express静态文件服务器服务bundle
    3. renderMedia从静态URL加载并渲染
    """

    DEFAULT_SERVER_PORT = 3333

    def __init__(self, server_port: int = None, cwd: str = None):
        self.server_port = server_port or int(
            os.environ.get("REMOTION_PORT", self.DEFAULT_SERVER_PORT)
        )
        self.base_url = f"http://localhost:{self.server_port}"
        self.cwd = cwd or str(Path(__file__).parent.parent / "remotion-renderer")
        self.bundle_dir = Path(self.cwd) / "bundle"
        self.renders_dir = Path(self.cwd) / "renders"
        self.process: Optional[subprocess.Popen] = None
        self._started = False

    # ==================== 服务生命周期 ====================

    def ensure_bundle(self) -> bool:
        """
        确保Remotion bundle存在。如果不存在，运行npx remotion bundle。
        Returns: bundle是否成功
        """
        index_html = self.bundle_dir / "index.html"
        if index_html.exists():
            print(f"[RemotionBridge] Bundle already exists at {self.bundle_dir}")
            return True

        print(f"[RemotionBridge] Creating Remotion bundle...")
        entry_point = Path(self.cwd) / "remotion" / "index.ts"

        if not entry_point.exists():
            print(f"[RemotionBridge] ERROR: Entry point not found: {entry_point}")
            return False

        result = subprocess.run(
            [
                "npx", "remotion", "bundle",
                "--entry-point", str(entry_point),
                "--out-dir", str(self.bundle_dir),
            ],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            print(f"[RemotionBridge] Bundle failed: {result.stderr[:500]}")
            return False

        print(f"[RemotionBridge] Bundle created successfully")
        return True

    def start_server(self, timeout: int = 60) -> bool:
        """
        启动Remotion渲染服务（静态bundle模式）

        Args:
            timeout: 等待服务启动的超时时间（秒）

        Returns:
            是否启动成功
        """
        if self._started and self.process:
            print("[RemotionBridge] Server already running")
            return True

        # 确保bundle存在
        if not self.ensure_bundle():
            return False

        print(f"[RemotionBridge] Starting Remotion server on port {self.server_port}...")

        # 检查是否已经在运行
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=2)
            if resp.status_code == 200:
                print(f"[RemotionBridge] Server already running at {self.base_url}")
                self._started = True
                return True
        except requests.exceptions.ConnectionError:
            pass

        # 启动服务（使用tsx运行server）
        env = os.environ.copy()
        env["PORT"] = str(self.server_port)

        server_script = Path(self.cwd) / "server" / "index.ts"
        self.process = subprocess.Popen(
            ["npx", "tsx", str(server_script)],
            cwd=self.cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # 等待服务就绪
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                resp = requests.get(f"{self.base_url}/health", timeout=2)
                if resp.status_code == 200:
                    print(f"[RemotionBridge] Server started at {self.base_url}")
                    self._started = True
                    return True
            except requests.exceptions.ConnectionError:
                # 检查进程是否崩溃
                if self.process.poll() is not None:
                    stdout, _ = self.process.communicate(timeout=1)
                    print(f"[RemotionBridge] Server process died: {stdout[:500]}")
                    return False
                time.sleep(1)

        print(f"[RemotionBridge] Server start timeout ({timeout}s)")
        if self.process:
            self.process.terminate()
        return False

    def stop_server(self):
        """停止Remotion渲染服务"""
        if self.process:
            print("[RemotionBridge] Stopping server...")
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            self._started = False
            print("[RemotionBridge] Server stopped")

    def is_server_running(self) -> bool:
        """检查服务是否在运行"""
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    # ==================== 布局构建 ====================

    def create_layout(
        self,
        background_image: str,
        boxes: list,
        arrows: list = None,
        width: int = 1080,
        height: int = 1920,
    ) -> dict:
        """创建布局数据"""
        return {
            "backgroundImage": background_image,
            "boxes": boxes,
            "arrows": arrows or [],
            "width": width,
            "height": height,
        }

    def create_box(
        self,
        box_id: str,
        label: str,
        x: int, y: int,
        width: int = 200,
        height: int = 80,
        color: str = "#4EC9B0",
        fill_color: str = "#4EC9B033",
        text_color: str = "#FFFFFF",
        font_size: int = 18,
        sub_label: str = "",
        show_from: int = 0,
        duration: int = 150,
        highlighted: bool = False,
        highlight_color: str = "#CE9178",
    ) -> dict:
        """创建单个方框的数据"""
        return {
            "id": box_id,
            "label": label,
            "subLabel": sub_label,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "color": color,
            "fillColor": fill_color,
            "textColor": text_color,
            "fontSize": font_size,
            "showFrom": show_from,
            "durationInFrames": duration,
            "highlighted": highlighted,
            "highlightColor": highlight_color,
        }

    def create_arrow(
        self,
        arrow_id: str,
        from_box_id: str,
        to_box_id: str,
        label: str = "",
        color: str = "#808080",
        show_from: int = 30,
    ) -> dict:
        """创建单个箭头的数据"""
        return {
            "id": arrow_id,
            "fromBoxId": from_box_id,
            "toBoxId": to_box_id,
            "label": label,
            "color": color,
            "showFrom": show_from,
        }

    # ==================== 渲染API ====================

    def render(self, layout: dict) -> Optional[str]:
        """发起渲染任务"""
        try:
            resp = requests.post(
                f"{self.base_url}/render",
                json={"layout": layout},
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("jobId")
            else:
                print(f"[RemotionBridge] Render failed: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"[RemotionBridge] Render request failed: {e}")
            return None

    def get_status(self, job_id: str) -> dict:
        """查询渲染任务状态"""
        try:
            resp = requests.get(f"{self.base_url}/status/{job_id}", timeout=5)
            return resp.json()
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def wait_for_completion(
        self,
        job_id: str,
        poll_interval: float = 3.0,
        timeout: float = 600,
    ) -> Optional[str]:
        """
        等待渲染完成

        Returns:
            本地文件路径
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            status = self.get_status(job_id)
            s = status.get("status")

            if s == "completed":
                # 下载视频到本地renders目录
                local_path = self.renders_dir / f"{job_id}.mp4"
                if local_path.exists():
                    print(f"[RemotionBridge] Render complete: {local_path}")
                    return str(local_path)
                else:
                    print(f"[RemotionBridge] Completed but file not found at {local_path}")
                    return None

            elif s == "failed":
                print(f"[RemotionBridge] Render failed: {status.get('error', 'Unknown')}")
                return None

            elif s == "rendering":
                progress = status.get("progress", 0)
                print(f"\r[RemotionBridge] Rendering: {progress*100:.1f}%", end="", flush=True)

            time.sleep(poll_interval)

        print(f"\n[RemotionBridge] Timeout after {timeout}s")
        return None

    def render_sync(
        self,
        layout: dict,
        output_path: str = None,
        timeout: float = 600,
    ) -> Optional[str]:
        """
        同步渲染：发起渲染 + 等待完成 + 返回路径
        """
        job_id = self.render(layout)
        if not job_id:
            return None

        result = self.wait_for_completion(job_id, timeout=timeout)
        if result and output_path and result != output_path:
            shutil.copy2(result, output_path)
            return output_path
        return result

    # ==================== 上下文管理器 ====================

    def __enter__(self):
        self.start_server()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_server()
        return False


# ==================== 单例便捷函数 ====================

_default_bridge: Optional[RemotionBridge] = None


def get_remotion_bridge() -> RemotionBridge:
    """获取全局Remotion桥接器单例"""
    global _default_bridge
    if _default_bridge is None:
        _default_bridge = RemotionBridge()
    return _default_bridge
