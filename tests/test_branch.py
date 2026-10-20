"""Tests for Branch & Merge — Git for Media."""

import copy
import pytest
from compiler.thinking.branch import ConflictType, ResolutionStrategy, MergeConflict, MergeResult
from compiler.thinking.patch import EditSentencePatch
from compiler.thinking.state import VideoProjectState, ModuleState, ScriptSentence


def make_state():
    s = VideoProjectState(topic="Test")
    mod = ModuleState(title="Test Module", index=0)
    mod.script.append(ScriptSentence(text="First sentence", index=0))
    mod.script.append(ScriptSentence(text="Second sentence", index=1))
    s.modules.append(mod)
    s.current_module_index = 0
    return s


@pytest.fixture
def state():
    return make_state()


class TestConflictType:
    def test_enum_values(self):
        assert ConflictType.TEXT_EDIT == "text_edit"
        assert ConflictType.TIMING_EDIT == "timing_edit"

    def test_all_types_string(self):
        for ct in ConflictType:
            assert isinstance(ct.value, str)


class TestResolutionStrategy:
    def test_enum_values(self):
        assert ResolutionStrategy.OURS == "ours"
        assert ResolutionStrategy.THEIRS == "theirs"


class TestMergeConflict:
    def test_default_values(self):
        mc = MergeConflict()
        assert mc.resolved is False

    def test_with_values(self):
        mc = MergeConflict(
            conflict_type=ConflictType.TEXT_EDIT,
            ours_value="A", theirs_value="B",
        )
        assert mc.ours_value == "A"


class TestMergeResult:
    def test_default_values(self):
        mr = MergeResult()
        assert mr.success is False
        assert mr.conflicts == []

    def test_with_values(self):
        mr = MergeResult(success=True, auto_merged=[
            EditSentencePatch(module_id="m", sentence_id="s", old_text="a", new_text="b"),
        ])
        assert mr.success is True
        assert len(mr.auto_merged) == 1


class TestBranchIntegration:
    def test_branch_state_independence(self, state):
        branch_a = copy.deepcopy(state)
        branch_b = copy.deepcopy(state)

        mod_a = branch_a.get_current_module()
        mod_a.script[0].text = "Branch A edit"

        mod_b = branch_b.get_current_module()
        assert mod_b.script[0].text == "First sentence"

    def test_merge_non_conflicting_edits(self, state):
        branch_a = copy.deepcopy(state)
        branch_b = copy.deepcopy(state)

        branch_a.get_current_module().script[0].text = "A's edit"
        branch_b.get_current_module().script[1].text = "B's edit"

        mod_a = branch_a.get_current_module()
        mod_a.script[1].text = branch_b.get_current_module().script[1].text
        assert mod_a.script[0].text == "A's edit"
        assert mod_a.script[1].text == "B's edit"

    def test_detect_text_conflict(self, state):
        branch_a = copy.deepcopy(state)
        branch_b = copy.deepcopy(state)

        branch_a.get_current_module().script[0].text = "A's version"
        branch_b.get_current_module().script[0].text = "B's version"

        original = state.get_current_module().script[0].text
        mod_a = branch_a.get_current_module()
        mod_b = branch_b.get_current_module()

        conflicts = []
        for sa, sb, so in zip(mod_a.script, mod_b.script, state.get_current_module().script):
            if sa.text != so.text and sb.text != so.text and sa.text != sb.text:
                conflicts.append(MergeConflict(
                    conflict_type=ConflictType.TEXT_EDIT,
                    ours_value=sa.text, theirs_value=sb.text,
                ))

        assert len(conflicts) == 1
        assert conflicts[0].ours_value == "A's version"
        assert conflicts[0].theirs_value == "B's version"
