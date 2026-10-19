# -*- coding: utf-8 -*-
"""视频技能（Skill）测试：加载、渲染、参数校验与 API。"""
import json

import pytest

from services.ai_video.skills import (
    SKILLS_DIR,
    SkillNotFound,
    SkillParamError,
    _load_file,
    get_skill,
    load_skills,
    render_prompt,
)


def test_builtin_skills_are_loaded():
    skills = load_skills()
    assert len(skills) >= 10, "内置技能数量偏少"
    ids = [s.id for s in skills]
    assert len(ids) == len(set(ids)), "技能 id 重复"
    # 精选排在前面
    flags = [s.featured for s in skills]
    assert flags == sorted(flags, reverse=True)


def test_every_builtin_skill_renders_cleanly():
    """每个内置技能都能用占位值渲染，且不残留 {xxx}。"""
    for skill in load_skills():
        params = {
            p["key"]: f"测试{p['label']}"
            for p in skill.params
            if p.get("required", True)
        }
        result = render_prompt(skill, params)
        prompt = result["prompt"]
        assert prompt, f"{skill.id} 渲染为空"
        assert "{" not in prompt and "}" not in prompt, f"{skill.id} 残留占位符: {prompt}"
        assert "，，" not in prompt, f"{skill.id} 标点未清理: {prompt}"


def test_missing_required_param_raises():
    skill = get_skill("cinematic_push_in")
    with pytest.raises(SkillParamError) as exc:
        render_prompt(skill, {})
    assert exc.value.missing == ["subject"]


def test_optional_param_falls_back_to_default():
    skill = get_skill("cinematic_push_in")
    result = render_prompt(skill, {"subject": "雨夜街道"})
    assert "静止不动" in result["prompt"]  # action 的 default


def test_render_returns_default_generation_params():
    skill = get_skill("aerial_landscape")
    result = render_prompt(skill, {"place": "雪山日出"})
    assert result["size"] == "16:9"
    assert result["resolution"] == "1080P"
    assert result["duration"] == 5
    assert result["skill_id"] == "aerial_landscape"


def test_unknown_skill_raises():
    with pytest.raises(SkillNotFound):
        get_skill("definitely_not_exists")


def test_invalid_template_placeholder_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "id": "bad",
        "name": "坏技能",
        "prompt_template": "{subject} 但没声明 {unknown_param}",
        "params": [{"key": "subject", "label": "主体"}],
    }, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError):
        _load_file(bad)


# ---------- API ----------

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main_fastapi import create_app

    return TestClient(create_app())


def test_api_lists_skills(client):
    response = client.get("/api/ai/video/skills")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == len(load_skills())
    assert data["skills"][0]["id"]
    assert "params" in data["skills"][0]


def test_api_renders_skill(client):
    response = client.post(
        "/api/ai/video/skills/cyberpunk_neon/render",
        json={"params": {"subject": "机械义体特写"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert "机械义体特写" in body["prompt"]
    assert body["skill_id"] == "cyberpunk_neon"


def test_api_render_reports_missing_params(client):
    response = client.post("/api/ai/video/skills/cyberpunk_neon/render", json={"params": {}})
    assert response.status_code == 400
    assert response.json()["detail"]["missing"] == ["subject"]


def test_api_unknown_skill_404(client):
    response = client.post("/api/ai/video/skills/nope/render", json={"params": {}})
    assert response.status_code == 404
