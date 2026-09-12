"""Regression checks for protocol fidelity, WAL updates and process ownership."""
import copy
import io
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_gateway import office_edge as edge
from test_supervisor import supervisor


class ReliabilityTests(unittest.TestCase):
    def test_legacy_fallback_requires_explicit_opt_in(self):
        payload = {"thinking": {"type": "adaptive"}, "tool_choice": {"type": "tool", "name": "run"},
                   "tools": [{"name": "run", "input_schema": {}, "strict": True}],
                   "messages": [{"role": "user", "content": "hello"}]}
        with patch.dict(os.environ, {"EDGE_LEGACY_SANITIZE": "0"}):
            self.assertIsNone(edge._legacy_retry(payload, 400, "thinking not supported"))
        with patch.dict(os.environ, {"EDGE_LEGACY_SANITIZE": "1"}):
            result = edge._legacy_retry(payload, 400, "thinking not supported")
            self.assertNotIn("thinking", result)
            self.assertEqual(result["tool_choice"], {"type": "auto"})
            self.assertEqual(payload["thinking"], {"type": "adaptive"})
            self.assertIsNone(edge._legacy_retry(payload, 502, "thinking not supported"))

    def test_tools_preserve_native_fields_and_forced_choice(self):
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2},
                 {"name": "run", "input_schema": {}, "strict": True,
                  "cache_control": {"type": "ephemeral"}},
                 {"type": "custom", "custom": {"name": "nested", "input_schema": {},
                                                 "defer_loading": True}}]
        choice = {"type": "tool", "name": "run", "disable_parallel_tool_use": True}
        result = edge.normalize_payload({"tools": tools, "tool_choice": choice})
        self.assertEqual(result["tools"][:2], tools[:2])
        self.assertTrue(result["tools"][2]["defer_loading"])
        self.assertEqual(result["tool_choice"], choice)

    def test_retry_removes_only_explicit_optional_field(self):
        payload = {"metadata": {}, "thinking": {"type": "enabled"}, "service_tier": "auto",
                   "system": [{"type": "text", "text": "instructions"}],
                   "messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "reason"}]}]}
        original = copy.deepcopy(payload)
        result = edge._targeted_retry(payload, 400, "extra field metadata not allowed")
        self.assertEqual(result, {k: v for k, v in payload.items() if k != "metadata"})
        self.assertEqual(payload, original)
        for status in (401, 403, 429, 500, 502):
            self.assertIsNone(edge._targeted_retry(payload, status, "extra field metadata not allowed"))
        self.assertIsNone(edge._targeted_retry(payload, 400, "thinking not supported"))

    def test_compatibility_key_changes_with_model_and_configuration(self):
        state = {"channel": "claude", "active": {"id": "one", "env": {"url": "a"}},
                 "model_entries": [["claude", "claude", "model-a"]]}
        key = edge._compatibility_key(state, "claude")
        self.assertNotEqual(key, edge._compatibility_key(state, "haiku"))
        state["model_entries"][0][2] = "model-b"
        self.assertNotEqual(key, edge._compatibility_key(state, "claude"))

    def test_wal_commit_refreshes_models_without_main_db_change(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "cc.db")
            con = sqlite3.connect(path)
            try:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA wal_autocheckpoint=0")
                con.execute("CREATE TABLE providers(id TEXT,name TEXT,app_type TEXT,is_current INTEGER,settings_config TEXT,meta TEXT)")
                def cfg(model):
                    return json.dumps({"env": {"ANTHROPIC_MODEL": model}})
                con.execute("INSERT INTO providers VALUES ('id','provider','claude',1,?,'{}')", (cfg("first"),))
                con.commit()
                with patch.object(edge, "CCSWITCH_DB", path), patch.object(edge, "CCSWITCH_CHANNEL", "claude"), \
                     patch.object(edge, "_state_cache", {}), patch.object(edge, "_db_mtime", None), patch.object(edge, "_MODEL_MAP", {}):
                    self.assertEqual(edge.read_ccswitch_state()["model_entries"][0][2], "first")
                    main_time = os.stat(path).st_mtime_ns
                    con.execute("UPDATE providers SET settings_config=?", (cfg("second"),))
                    con.commit()
                    self.assertEqual(main_time, os.stat(path).st_mtime_ns)
                    self.assertEqual(edge.read_ccswitch_state()["model_entries"][0][2], "second")
            finally:
                con.close()

    def test_unowned_listener_is_never_killed(self):
        with patch.object(supervisor, "listener_pids", return_value={12345}), \
             patch.object(supervisor.subprocess, "run") as run, patch.object(supervisor, "_log"):
            self.assertFalse(supervisor.stop_stale_listeners())
            run.assert_not_called()

    def test_child_log_handles_close_after_spawn_failure(self):
        out, err = io.BytesIO(), io.BytesIO()
        with patch("builtins.open", side_effect=[out, err]), \
             patch.object(supervisor.subprocess, "Popen", side_effect=OSError("cannot spawn")):
            with self.assertRaises(OSError):
                supervisor.start_gateway()
        self.assertTrue(out.closed)
        self.assertTrue(err.closed)
