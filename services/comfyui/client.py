"""ComfyUI HTTP 客户端。"""
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import requests

from .errors import (
    ComfyUIConnectionError,
    ComfyUIExecutionError,
    ComfyUITimeoutError,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkflowOutput:
    filename: str
    subfolder: str
    type: str  # "output" / "temp"


class ComfyUIClient:
    """ComfyUI HTTP 客户端,提交-轮询工作流,下载产物。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        poll_interval_sec: float = 2.0,
        timeout_sec: float = 600.0,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.poll_interval_sec = poll_interval_sec
        self.timeout_sec = timeout_sec
        self.session = session or requests.Session()

    # ---- 连接检测 ----
    def test_connection(self) -> bool:
        try:
            r = self.session.request(
                "GET",
                f"{self.base_url}/system_stats",
                timeout=5,
            )
            return r.status_code == 200
        except requests.RequestException as e:
            logger.warning(f"ComfyUI 不可达: {e}")
            return False

    # ---- 提交 ----
    def submit(self, workflow: dict) -> str:
        try:
            r = self.session.request(
                "POST",
                f"{self.base_url}/prompt",
                json={"prompt": workflow},
                timeout=30,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            raise ComfyUIConnectionError(f"提交工作流失败: {e}") from e

        data = r.json()
        if "prompt_id" not in data:
            raise ComfyUIExecutionError(
                f"提交响应缺少 prompt_id: {data}"
            )
        return data["prompt_id"]

    # ---- 轮询 / 下载 ----
    def _poll_history(self, prompt_id: str) -> dict:
        """单次 GET /history/{prompt_id};容错 5xx → 返回空 dict。"""
        try:
            r = self.session.request(
                "GET",
                f"{self.base_url}/history/{prompt_id}",
                timeout=10,
            )
            if r.status_code != 200:
                return {}
            return r.json()
        except requests.RequestException as e:
            logger.warning(f"轮询 history 失败: {e}")
            return {}

    def wait_for_completion(self, prompt_id: str) -> list[WorkflowOutput]:
        deadline = time.time() + self.timeout_sec
        while time.time() < deadline:
            history = self._poll_history(prompt_id)
            entry = history.get(prompt_id)
            if entry is None and history:
                # 测试/部分服务端可能直接以节点 id 为外层 key;
                # 此时取首个含 outputs 的 entry。
                for v in history.values():
                    if isinstance(v, dict) and v.get("outputs"):
                        entry = v
                        break
            entry = entry or {}
            status = entry.get("status") or {}
            if status.get("error"):
                raise ComfyUIExecutionError(
                    f"工作流执行报错: {status}"
                )
            outputs_raw = entry.get("outputs") or {}
            if outputs_raw:
                return self._parse_outputs(outputs_raw)
            time.sleep(self.poll_interval_sec)
        raise ComfyUITimeoutError(
            f"等待 {prompt_id} 超时 ({self.timeout_sec}s)"
        )

    def _parse_outputs(self, outputs_raw: dict) -> list[WorkflowOutput]:
        """从 /history 的 outputs 字典里抽出所有文件引用。"""
        result: list[WorkflowOutput] = []
        for node_id, node_out in outputs_raw.items():
            for kind in ("images", "gifs", "videos"):
                for item in node_out.get(kind, []) or []:
                    result.append(WorkflowOutput(
                        filename=item["filename"],
                        subfolder=item.get("subfolder", ""),
                        type=item.get("type", "output"),
                    ))
        return result

    def download_outputs(
        self,
        outputs: list[WorkflowOutput],
        save_dir: Union[str, Path],
    ) -> list[Path]:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for o in outputs:
            try:
                r = self.session.request(
                    "GET",
                    f"{self.base_url}/view",
                    params={"filename": o.filename,
                            "subfolder": o.subfolder,
                            "type": o.type},
                    timeout=60,
                )
                r.raise_for_status()
            except requests.RequestException as e:
                raise ComfyUIExecutionError(
                    f"下载 {o.filename} 失败: {e}"
                ) from e
            out_path = save_dir / o.filename
            out_path.write_bytes(r.content)
            paths.append(out_path)
        return paths
