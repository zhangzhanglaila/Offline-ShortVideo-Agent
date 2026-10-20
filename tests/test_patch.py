"""Tests for Patch System — event sourcing for VideoProjectState."""

import pytest
from compiler.thinking.patch import (
    EditSentencePatch, AddSentencePatch, RemoveSentencePatch,
    ApproveModulePatch, EditGraphNodePatch, ConditionalPatch, BatchPatch,
    PatchHistory, _patch_from_dict,
)
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


class TestEditSentencePatch:
    def test_apply(self, state):
        mod = state.get_current_module()
        patch = EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="Edited",
        )
        assert patch.apply(state) is True
        assert mod.script[0].text == "Edited"

    def test_revert(self, state):
        mod = state.get_current_module()
        patch = EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="Edited",
        )
        patch.apply(state)
        assert patch.revert(state) is True
        assert mod.script[0].text == "First sentence"

    def test_roundtrip(self, state):
        mod = state.get_current_module()
        patch = EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="Changed",
        )
        d = patch.to_dict()
        loaded = EditSentencePatch.from_dict(d)
        assert loaded.new_text == "Changed"
        assert loaded.module_id == mod.id


class TestAddSentencePatch:
    def test_apply(self, state):
        mod = state.get_current_module()
        count_before = len(mod.script)
        patch = AddSentencePatch(module_id=mod.id, text="New sentence")
        assert patch.apply(state) is True
        assert len(mod.script) == count_before + 1
        assert mod.script[-1].text == "New sentence"

    def test_revert(self, state):
        mod = state.get_current_module()
        patch = AddSentencePatch(module_id=mod.id, text="New sentence")
        patch.apply(state)
        assert patch.revert(state) is True
        assert len(mod.script) == 2

    def test_roundtrip(self, state):
        mod = state.get_current_module()
        patch = AddSentencePatch(module_id=mod.id, text="Test")
        patch.apply(state)
        d = patch.to_dict()
        loaded = AddSentencePatch.from_dict(d)
        assert loaded.text == "Test"
        assert loaded.created_sentence_id != ""


class TestRemoveSentencePatch:
    def test_apply(self, state):
        mod = state.get_current_module()
        sid = mod.script[0].id
        patch = RemoveSentencePatch(module_id=mod.id, sentence_id=sid)
        assert patch.apply(state) is True
        assert len(mod.script) == 1

    def test_revert(self, state):
        mod = state.get_current_module()
        sid = mod.script[0].id
        patch = RemoveSentencePatch(module_id=mod.id, sentence_id=sid)
        patch.apply(state)
        assert patch.revert(state) is True
        assert len(mod.script) == 2


class TestApproveModulePatch:
    def test_apply(self, state):
        mod = state.get_current_module()
        patch = ApproveModulePatch(module_id=mod.id, component="all")
        assert patch.apply(state) is True
        assert mod.script_approved is True

    def test_revert(self, state):
        mod = state.get_current_module()
        patch = ApproveModulePatch(module_id=mod.id, component="all")
        patch.apply(state)
        assert patch.revert(state) is True
        assert mod.script_approved is False


class TestConditionalPatch:
    def test_apply_when_condition_met(self, state):
        mod = state.get_current_module()
        inner = EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="Conditional edit",
        )
        patch = ConditionalPatch(condition_fn=lambda s: True, inner_patch=inner)
        assert patch.apply(state) is True
        assert mod.script[0].text == "Conditional edit"

    def test_noop_when_condition_not_met(self, state):
        mod = state.get_current_module()
        inner = EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="Should not apply",
        )
        patch = ConditionalPatch(condition_fn=lambda s: False, inner_patch=inner)
        assert patch.apply(state) is True
        assert mod.script[0].text == "First sentence"

    def test_revert_noop_when_not_applied(self, state):
        mod = state.get_current_module()
        inner = EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="X",
        )
        patch = ConditionalPatch(condition_fn=lambda s: False, inner_patch=inner)
        patch.apply(state)
        assert patch.revert(state) is True

    def test_roundtrip(self):
        inner = EditSentencePatch(module_id="m", sentence_id="s", old_text="a", new_text="b")
        patch = ConditionalPatch(inner_patch=inner)
        d = patch.to_dict()
        loaded = ConditionalPatch.from_dict(d)
        assert loaded.inner_patch.new_text == "b"


class TestBatchPatch:
    def test_apply_all(self, state):
        mod = state.get_current_module()
        batch = BatchPatch(patches=[
            EditSentencePatch(module_id=mod.id, sentence_id=mod.script[0].id,
                              old_text="First sentence", new_text="Edit 1"),
            EditSentencePatch(module_id=mod.id, sentence_id=mod.script[1].id,
                              old_text="Second sentence", new_text="Edit 2"),
        ])
        assert batch.apply(state) is True
        assert mod.script[0].text == "Edit 1"
        assert mod.script[1].text == "Edit 2"

    def test_revert_reverse_order(self, state):
        mod = state.get_current_module()
        batch = BatchPatch(patches=[
            EditSentencePatch(module_id=mod.id, sentence_id=mod.script[0].id,
                              old_text="First sentence", new_text="Edit 1"),
            EditSentencePatch(module_id=mod.id, sentence_id=mod.script[1].id,
                              old_text="Second sentence", new_text="Edit 2"),
        ])
        batch.apply(state)
        assert batch.revert(state) is True
        assert mod.script[0].text == "First sentence"
        assert mod.script[1].text == "Second sentence"

    def test_roundtrip(self):
        batch = BatchPatch(patches=[
            EditSentencePatch(module_id="m", sentence_id="s", old_text="a", new_text="b"),
        ])
        d = batch.to_dict()
        loaded = BatchPatch.from_dict(d)
        assert len(loaded.patches) == 1


class TestPatchHistory:
    def test_record_and_undo(self, state):
        history = PatchHistory()
        mod = state.get_current_module()
        patch = EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="Edited",
        )
        patch.apply(state)
        history.record(patch)
        assert history.can_undo
        assert len(history.applied) == 1

        history.undo(state)
        assert mod.script[0].text == "First sentence"
        assert history.can_redo

    def test_redo(self, state):
        history = PatchHistory()
        mod = state.get_current_module()
        patch = EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="Edited",
        )
        patch.apply(state)
        history.record(patch)
        history.undo(state)
        history.redo(state)
        assert mod.script[0].text == "Edited"

    def test_undo_clears_redo(self, state):
        history = PatchHistory()
        mod = state.get_current_module()
        p1 = EditSentencePatch(module_id=mod.id, sentence_id=mod.script[0].id,
                               old_text="First sentence", new_text="Edit 1")
        p1.apply(state)
        history.record(p1)

        p2 = EditSentencePatch(module_id=mod.id, sentence_id=mod.script[0].id,
                               old_text="Edit 1", new_text="Edit 2")
        p2.apply(state)
        history.record(p2)

        history.undo(state)
        assert history.can_redo

        p3 = EditSentencePatch(module_id=mod.id, sentence_id=mod.script[0].id,
                               old_text="First sentence", new_text="Edit 3")
        p3.apply(state)
        history.record(p3)
        assert not history.can_redo

    def test_replay(self, state):
        history = PatchHistory()
        mod = state.get_current_module()
        p1 = EditSentencePatch(module_id=mod.id, sentence_id=mod.script[0].id,
                               old_text="First sentence", new_text="A")
        p1.apply(state)
        history.record(p1)
        p2 = EditSentencePatch(module_id=mod.id, sentence_id=mod.script[1].id,
                               old_text="Second sentence", new_text="B")
        p2.apply(state)
        history.record(p2)

        # Replay on a fresh state with the same module/sentence IDs
        fresh = VideoProjectState(topic="Test")
        fresh_mod = ModuleState(title="Test Module", index=0, id=mod.id)
        fresh_mod.script.append(ScriptSentence(text="First sentence", index=0, id=mod.script[0].id))
        fresh_mod.script.append(ScriptSentence(text="Second sentence", index=1, id=mod.script[1].id))
        fresh.modules.append(fresh_mod)
        fresh.current_module_index = 0
        count = history.replay(fresh)
        assert count == 2

    def test_to_dicts_load_dicts(self, state):
        history = PatchHistory()
        mod = state.get_current_module()
        patch = EditSentencePatch(module_id=mod.id, sentence_id=mod.script[0].id,
                                  old_text="First sentence", new_text="Edited")
        patch.apply(state)
        history.record(patch)

        dicts = history.to_dicts()
        assert len(dicts) == 1

        history2 = PatchHistory()
        history2.load_dicts(dicts)
        assert len(history2.applied) == 1

    def test_empty_undo(self):
        history = PatchHistory()
        assert history.undo(None) is False

    def test_empty_redo(self):
        history = PatchHistory()
        assert history.redo(None) is False


class TestPatchFromDict:
    def test_known_types(self):
        for type_name, cls in [
            ("edit_sentence", EditSentencePatch),
            ("add_sentence", AddSentencePatch),
            ("remove_sentence", RemoveSentencePatch),
            ("approve_module", ApproveModulePatch),
            ("edit_graph_node", EditGraphNodePatch),
        ]:
            patch = _patch_from_dict({"type": type_name, "id": "test"})
            assert isinstance(patch, cls)

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown patch type"):
            _patch_from_dict({"type": "nonexistent"})
