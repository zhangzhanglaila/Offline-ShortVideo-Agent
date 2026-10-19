# -*- coding: utf-8 -*-
"""视频生成技能（Skill）—— 可热加载的提示词配方 + 参数预设。

一个技能就是 `skills/video/<id>.json`：
提示词模板（含 `{subject}` 这类占位符）+ 参数定义 + 推荐生成参数。
用户选技能、填一两个字段，就能拿到结构完整的专业提示词。

设计约束：
  - 纯文本模板，不联网、不花钱（L1）；LLM 扩写留作后续能力
  - 加载时校验：占位符必须在 params 里声明，避免渲染出 `{xxx}` 残留
  - 渲染结果做长度截断与标点清理，防止空参数留下 `，，`
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills" / "video"

MAX_PROMPT_CHARS = 1200
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_PARAM_KEYS = ("size", "resolution", "duration", "provider", "model")


class SkillError(Exception):
    """技能相关错误的基类。"""


class SkillNotFound(SkillError):
    def __init__(self, skill_id: str):
        self.skill_id = skill_id
        super().__init__(f"技能不存在: {skill_id}")


class SkillParamError(SkillError):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("缺少必填参数: " + ", ".join(missing))


@dataclass
class VideoSkill:
    id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    featured: bool = False
    prompt_template: str = ""
    params: list[dict] = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    comfyui: dict = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    path: str = ""

    def to_dict(self, include_template: bool = True) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "featured": self.featured,
            "params": [dict(p) for p in self.params],
            "defaults": dict(self.defaults),
            "examples": list(self.examples),
        }
        if include_template:
            data["prompt_template"] = self.prompt_template
        return data


def _load_file(path: Path) -> VideoSkill:
    """读取并校验单个技能文件，失败抛 ValueError（由调用方决定是否跳过）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("技能文件必须是 JSON 对象")

    skill_id = str(data.get("id") or path.stem)
    name = str(data.get("name") or "").strip()
    template = str(data.get("prompt_template") or "").strip()
    if not name:
        raise ValueError("缺少 name")
    if not template:
        raise ValueError("缺少 prompt_template")

    params = data.get("params") or []
    if not isinstance(params, list):
        raise ValueError("params 必须是数组")
    param_keys = set()
    for item in params:
        if not isinstance(item, dict) or not item.get("key"):
            raise ValueError("params 每项必须包含 key")
        param_keys.add(str(item["key"]))

    # 模板里的占位符必须都有对应参数，否则会渲染出 `{xxx}` 这种脏数据
    unknown = set(_PLACEHOLDER.findall(template)) - param_keys
    if unknown:
        raise ValueError(f"模板占位符未在 params 中声明: {sorted(unknown)}")

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("defaults 必须是对象")

    return VideoSkill(
        id=skill_id,
        name=name,
        description=str(data.get("description") or "").strip(),
        tags=[str(t) for t in (data.get("tags") or [])],
        featured=bool(data.get("featured", False)),
        prompt_template=template,
        params=params,
        defaults={k: v for k, v in defaults.items() if k in _PARAM_KEYS},
        comfyui=data.get("comfyui") or {},
        examples=[str(e) for e in (data.get("examples") or [])],
        path=str(path),
    )


def load_skills() -> list[VideoSkill]:
    """加载全部技能：精选优先，其次按名称排序。无效文件跳过。"""
    if not SKILLS_DIR.is_dir():
        return []

    skills: dict[str, VideoSkill] = {}
    for path in sorted(SKILLS_DIR.glob("*.json")):
        try:
            skill = _load_file(path)
        except Exception:
            continue
        skills.setdefault(skill.id, skill)

    return sorted(skills.values(), key=lambda s: (not s.featured, s.name))


def get_skill(skill_id: str) -> VideoSkill:
    for skill in load_skills():
        if skill.id == skill_id:
            return skill
    raise SkillNotFound(skill_id)


def render_prompt(skill: VideoSkill, params: dict[str, Any] | None = None) -> dict:
    """把用户参数套进模板，返回最终提示词与推荐生成参数。

    Raises:
        SkillParamError: 必填参数为空
    """
    values: dict[str, str] = {}
    missing: list[str] = []

    for item in skill.params:
        key = str(item.get("key"))
        raw = (params or {}).get(key)
        if raw is None or str(raw).strip() == "":
            raw = item.get("default", "")
        text = str(raw or "").strip()
        if not text:
            if item.get("required", True):
                missing.append(key)
            continue
        values[key] = text

    if missing:
        raise SkillParamError(missing)

    prompt = skill.prompt_template
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value)

    # 空参数可能留下连续逗号 / 悬空标点
    prompt = re.sub(r"\s*[，,]\s*(?=[，,])", "", prompt)
    prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ，,。;；")

    result = {
        "prompt": prompt[:MAX_PROMPT_CHARS],
        "skill_id": skill.id,
        "skill_name": skill.name,
    }
    for key in _PARAM_KEYS:
        if key in skill.defaults:
            result[key] = skill.defaults[key]
    return result
