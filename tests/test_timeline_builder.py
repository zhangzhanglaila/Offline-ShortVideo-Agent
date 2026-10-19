"""时间轴构建器测试：把 ThinkingSession 状态投影成可编辑轨道。"""
from types import SimpleNamespace

from thinking.artifacts import ArtifactType
from thinking.state import (
    AudioTrack,
    ModuleState,
    ScriptSentence,
    VideoProjectState,
)
from thinking.timeline_builder import (
    apply_tracks,
    build_timeline_tracks,
    ensure_timeline_graph,
    estimate_frames,
    module_span_frames,
)


def _state(with_audio: bool = True) -> VideoProjectState:
    state = VideoProjectState(topic="数据结构 - 二叉树", width=1080, height=1920, fps=30)
    for index, (title, lines) in enumerate([
        ("开场", ["二叉树是最多两个子节点的树。", "三分钟讲清楚。"]),
        ("结构", ["左子树小于根。", "右子树大于根。"]),
    ]):
        module = ModuleState(id=f"m{index}", title=title, index=index)
        module.script = [ScriptSentence(index=i, text=t) for i, t in enumerate(lines)]
        if with_audio:
            module.audio_tracks = [
                AudioTrack(id=f"a{index}_{i}", start=i * 90, duration=90, text=t)
                for i, t in enumerate(lines)
            ]
        state.modules.append(module)
    return state


def test_estimate_frames_has_minimum_one_second():
    assert estimate_frames("", 30) == 30
    assert estimate_frames("短", 30) == 30
    assert estimate_frames("字" * 45, 30) == 300  # 45 字 / 4.5 字每秒 = 10 秒


def test_module_span_prefers_audio():
    state = _state(with_audio=True)
    assert module_span_frames(state.modules[0], 30) == 180  # 2 段 × 90 帧


def test_module_span_falls_back_to_text_estimate():
    state = _state(with_audio=False)
    span = module_span_frames(state.modules[0], 30)
    assert span >= 30 * 3  # 无音频时至少 3 秒
    assert span == sum(estimate_frames(s.text, 30) for s in state.modules[0].script) or span == 90


def test_tracks_are_three_per_module_and_contiguous():
    tracks = build_timeline_tracks(_state())
    assert len(tracks) == 6  # 2 模块 × 3 轨
    assert {t["track_type"] for t in tracks} == {"visual", "subtitle", "audio"}

    # 同一模块的三条轨道共用起止与 scene_id
    first = [t for t in tracks if t["scene_id"] == "m0"]
    assert len(first) == 3
    assert {(t["start_frame"], t["end_frame"]) for t in first} == {(0, 180)}

    second = [t for t in tracks if t["scene_id"] == "m1"]
    assert {t["start_frame"] for t in second} == {180}

    # 轨道按模块顺序排列，首尾衔接
    ends = [t["end_frame"] for t in tracks]
    assert max(ends) == 360


def test_ensure_timeline_graph_is_idempotent():
    session = SimpleNamespace(state=_state())
    graph = ensure_timeline_graph(session)
    assert graph is session.artifact_graph

    timeline = graph.find_by_type(ArtifactType.TIMELINE)
    assert timeline is not None
    assert len(timeline.content["tracks"]) == 6
    assert timeline.content["fps"] == 30

    # 再次调用不应重复创建产物
    ensure_timeline_graph(session)
    assert len(graph.find_all_by_type(ArtifactType.TIMELINE)) == 1


def test_apply_tracks_overrides_timeline_content():
    session = SimpleNamespace(state=_state())
    graph = ensure_timeline_graph(session)
    tracks = build_timeline_tracks(_state())
    tracks[0]["content"]["title"] = "改过的标题"

    apply_tracks(session, tracks)

    timeline = graph.find_by_type(ArtifactType.TIMELINE)
    assert timeline.content["tracks"][0]["content"]["title"] == "改过的标题"
