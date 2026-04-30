"""Shared path utilities for bridge modules."""
import shutil
from pathlib import Path


def get_project_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "remotion-renderer").exists() and (cwd / "engine").exists():
        return cwd
    return Path(__file__).resolve().parents[3]


def ensure_public_audio_copy(source_path: Path, file_name: str) -> str:
    project_root = get_project_root()
    public_dir = project_root / "remotion-renderer" / "public" / "generated-audio"
    build_dir = project_root / "remotion-renderer" / "build" / "generated-audio"
    public_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, public_dir / file_name)
    if build_dir.parent.exists():
        build_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, build_dir / file_name)
    return f"/generated-audio/{file_name}"
