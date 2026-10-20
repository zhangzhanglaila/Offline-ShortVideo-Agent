"""Tests for Persistence Layer — disk-backed patch log."""

import json
import pytest
from pathlib import Path
from compiler.thinking.persistence import PatchStore, PersistentPatchHistory
from compiler.thinking.patch import EditSentencePatch, AddSentencePatch, BatchPatch
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
def store(tmp_path):
    return PatchStore(tmp_path / "session")


@pytest.fixture
def state():
    return make_state()


class TestPatchStore:
    def test_save_and_load_patch(self, store):
        patch = EditSentencePatch(
            module_id="mod_00", sentence_id="s_01",
            old_text="old", new_text="new",
        )
        path = store.save_patch(patch, 0)
        assert path.exists()
        assert "0000_" in path.name

        loaded = store.load_patch(path)
        assert isinstance(loaded, EditSentencePatch)
        assert loaded.new_text == "new"

    def test_load_all_patches(self, store):
        for i in range(3):
            patch = EditSentencePatch(
                module_id="m", sentence_id=f"s_{i}",
                old_text="old", new_text=f"new_{i}",
            )
            store.save_patch(patch, i)

        patches = store.load_all_patches()
        assert len(patches) == 3
        assert all(isinstance(p, EditSentencePatch) for p in patches)

    def test_patch_count(self, store):
        assert store.patch_count() == 0
        store.save_patch(EditSentencePatch(module_id="m", sentence_id="s"), 0)
        assert store.patch_count() == 1

    def test_save_and_load_checkpoint(self, store, state):
        path = store.save_checkpoint(state, 5)
        assert path.exists()

        result = store.load_latest_checkpoint()
        assert result is not None
        state_dict, patch_index = result
        assert patch_index == 5
        assert "topic" in state_dict

    def test_load_checkpoint_at(self, store, state):
        store.save_checkpoint(state, 3)
        result = store.load_checkpoint_at(3)
        assert result is not None
        assert result.get("topic") == "Test"

    def test_load_checkpoint_at_missing(self, store):
        assert store.load_checkpoint_at(99) is None

    def test_no_checkpoint(self, store):
        assert store.load_latest_checkpoint() is None

    def test_meta_save_load(self, store):
        store.save_meta({"topic": "Redis", "version": 1})
        meta = store.load_meta()
        assert meta["topic"] == "Redis"

    def test_meta_missing(self, store):
        assert store.load_meta() == {}

    def test_create_branch(self, store):
        store.save_patch(EditSentencePatch(module_id="m", sentence_id="s"), 0)
        branch_dir = store.create_branch("experiment")
        assert branch_dir.exists()
        branch_store = PatchStore(branch_dir)
        assert branch_store.patch_count() == 1

    def test_list_branches(self, store):
        store.create_branch("a")
        store.create_branch("b")
        branches = store.list_branches()
        assert "a" in branches
        assert "b" in branches

    def test_list_branches_empty(self, store):
        assert store.list_branches() == []

    def test_replay_from_checkpoint(self, store, state):
        mod = state.get_current_module()
        store.save_checkpoint(state, 0)
        store.save_patch(EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="Edited",
        ), 0)
        store.save_patch(EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[1].id,
            old_text="Second sentence", new_text="Also edited",
        ), 1)

        new_state = make_state()
        count = store.replay(new_state, from_checkpoint=True)
        assert count == 2


class TestPersistentPatchHistory:
    def test_record_saves_to_disk(self, store):
        history = PersistentPatchHistory(store)
        patch = EditSentencePatch(module_id="m", sentence_id="s", old_text="a", new_text="b")
        history.record(patch)
        assert store.patch_count() == 1

    def test_undo_saves_marker(self, store, state):
        history = PersistentPatchHistory(store)
        mod = state.get_current_module()
        patch = EditSentencePatch(
            module_id=mod.id, sentence_id=mod.script[0].id,
            old_text="First sentence", new_text="Edited",
        )
        patch.apply(state)
        history.record(patch)
        history.undo(state)
        assert store.patch_count() == 2

    def test_take_checkpoint(self, store, state):
        history = PersistentPatchHistory(store)
        path = history.take_checkpoint(state)
        assert path.exists()
