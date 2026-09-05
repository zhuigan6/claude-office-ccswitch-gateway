# -*- coding: utf-8 -*-
"""
test_gateway.py —— 统一网关回归测试（纯标准库 unittest，不依赖 cc-switch.db / 不启动服务器 / 零费用）

运行：python -m unittest discover -s tests -v
"""
import io
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 在导入被测模块前设置隔离环境（模块级配置在 import 时读取）
_TEST_DATA = tempfile.mkdtemp(prefix="office-edge-test-")
os.environ["EDGE_DATA_DIR"] = _TEST_DATA
os.environ.pop("EDGE_TOKEN", None)

_spec = importlib.util.spec_from_file_location("office_edge", os.path.join(REPO, "gateway", "office_edge.py"))
office_edge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(office_edge)


class TestModelAliases(unittest.TestCase):
    def test_canonical_model_split(self):
        self.assertEqual(office_edge.canonical_model("claude-opus-5--ccswitch--deepseek-v4-pro[1m]"), "claude-opus-5")

    def test_canonical_model_map(self):
        old = office_edge._MODEL_MAP
        office_edge._MODEL_MAP = {"claude-ccswitch-default": "claude"}
        try:
            self.assertEqual(office_edge.canonical_model("claude-ccswitch-default--ccswitch--deepseek-v4-flash"), "claude")
        finally:
            office_edge._MODEL_MAP = old

    def test_canonical_model_passthrough(self):
        self.assertEqual(office_edge.canonical_model("claude-sonnet-5"), "claude-sonnet-5")
        self.assertEqual(office_edge.canonical_model(123), 123)

    def test_build_models_list_uses_claude_aliases(self):
        state = {"active": {"name": "DeepSeek"},
                 "model_entries": [("claude-sonnet-4-6", "claude-sonnet-4-6", "deepseek-v4-flash")]}
        data = office_edge.build_models_list(state)["data"]
        self.assertEqual(len(data), 1)
        self.assertIn("claude", data[0]["id"])
        self.assertIn("deepseek-v4-flash", data[0]["display_name"])


class TestToolsCompat(unittest.TestCase):
    def test_custom_shell_flat(self):
        tools = [{"type": "custom", "name": "f", "description": "d", "input_schema": {"type": "object"}}]
        out = office_edge.normalize_tools(tools)
        self.assertEqual(out, [{"name": "f", "description": "d", "input_schema": {"type": "object"}}])

    def test_custom_shell_nested(self):
        tools = [{"type": "custom", "custom": {"name": "g", "input_schema": {"type": "object"}}}]
        out = office_edge.normalize_tools(tools)
        self.assertEqual(out, [{"name": "g", "input_schema": {"type": "object"}}])

    def test_drops_unnamed_and_normalizes_choice(self):
        p = {"model": "claude-opus-5--ccswitch--x", "tools": [{"type": "custom"}, {"nope": 1}],
             "tool_choice": {"type": "tool", "name": "f"}}
        out = office_edge.normalize_payload(p)
        self.assertEqual(out["model"], "claude-opus-5")
        self.assertEqual(out["tool_choice"], {"type": "auto"})
        self.assertEqual(out["tools"], [])


class TestDeepSanitize(unittest.TestCase):
    def test_strips_unsupported_and_defaults(self):
        p = {
            "model": "m", "metadata": {"user_id": "x"}, "service_tier": "auto",
            "max_tokens": None, "stream": True,
            "system": [{"type": "text", "text": "sys1", "cache_control": {"type": "ephemeral"}}, "sys2"],
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "hi", "citations": []},
                {"type": "thinking", "thinking": "hidden"},
                {"type": "image", "source": {"type": "base64"}},
            ]}],
            "tools": [{"type": "custom", "custom": {"name": "t", "input_schema": {}}}],
        }
        out = office_edge.deep_sanitize(json.loads(json.dumps(p)))
        self.assertNotIn("metadata", out)
        self.assertNotIn("service_tier", out)
        self.assertEqual(out["max_tokens"], 4096)
        self.assertEqual(out["system"], "sys1\n\nsys2")
        self.assertEqual(out["messages"][0]["content"],
                         [{"type": "text", "text": "hi"},
                          {"type": "image", "source": {"type": "base64"}}])  # 图片块保留：绝不静默丢弃用户内容
        self.assertEqual(out["tools"], [{"name": "t", "input_schema": {}}])


class TestShapeSummary(unittest.TestCase):
    def test_no_body_leak(self):
        secret = "SECRET-CONTENT-" + "x" * 64
        s = office_edge._shape_summary({"model": "m", "stream": False, "max_tokens": 16,
                                        "tools": [], "messages": [{"role": "user", "content": secret}]})
        self.assertNotIn(secret, s)
        self.assertIn("roles=['user']", s)


class TestMultipart(unittest.TestCase):
    def test_parse(self):
        boundary = "bn"
        body = (
            ("--%s\r\n" % boundary).encode() +
            b'Content-Disposition: form-data; name="file"; filename="a.txt"\r\n'
            b"Content-Type: text/plain\r\n\r\n" + b"hello" + b"\r\n" +
            ("--%s\r\n" % boundary).encode() +
            b'Content-Disposition: form-data; name="purpose"\r\n\r\n' + b"user_message" + b"\r\n" +
            ("--%s--\r\n" % boundary).encode()
        )
        parts = office_edge.parse_multipart(body, f"multipart/form-data; boundary={boundary}")
        self.assertEqual(parts["file"][0], "a.txt")
        self.assertEqual(parts["file"][2], b"hello")
        self.assertEqual(parts["purpose"][2], b"user_message")


class TestOpenAiConversion(unittest.TestCase):
    def test_openai_to_anthropic(self):
        p = {"model": "gpt-x", "max_tokens": 32, "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"}]}
        ant = office_edge.openai_to_anthropic(p)
        self.assertEqual(ant["system"], "be brief")
        self.assertEqual(ant["messages"], [{"role": "user", "content": "hello"}])
        self.assertTrue(ant["model"].startswith("claude"))  # 非 claude 模型归一到默认

    def test_anthropic_to_openai(self):
        ant = {"id": "msg_1", "content": [{"type": "text", "text": "hi"}],
               "usage": {"input_tokens": 3, "output_tokens": 2}, "stop_reason": "end_turn"}
        out = office_edge.anthropic_to_openai(ant, "req-model")
        self.assertEqual(out["choices"][0]["message"]["content"], "hi")
        self.assertEqual(out["choices"][0]["finish_reason"], "stop")
        self.assertEqual(out["usage"]["total_tokens"], 5)


class TestFilesLifecycle(unittest.TestCase):
    def test_roundtrip_with_sha256(self):
        meta = office_edge.files_create("hello.txt", "text/plain", b"hello world", "user_message", 60)
        fid = meta["id"]
        self.assertTrue(fid.startswith("file_"))
        self.assertEqual(meta["size_bytes"], 11)
        con = sqlite3.connect(office_edge.FILES_DB)
        row = con.execute("SELECT sha256 FROM files WHERE id=?", (fid,)).fetchone()
        con.close()
        self.assertEqual(row[0], hashlib.sha256(b"hello world").hexdigest())
        blob_meta, data = office_edge.files_blob(fid)
        self.assertEqual(data, b"hello world")
        self.assertIsNotNone(office_edge.files_delete(fid))
        self.assertIsNone(office_edge.files_meta(fid))

    def test_archive_rejected_415(self):
        with self.assertRaises(office_edge.FileInlineError) as cm:
            office_edge.files_create("evil.zip", "application/zip", b"PK", "user_message", 60)
        self.assertEqual(cm.exception.code, 415)

    def test_too_large_rejected_413(self):
        old = office_edge.MAX_FILE_BYTES
        office_edge.MAX_FILE_BYTES = 10
        try:
            with self.assertRaises(office_edge.FileInlineError) as cm:
                office_edge.files_create("big.bin", "application/octet-stream", b"x" * 11, "user_message", 60)
            self.assertEqual(cm.exception.code, 413)
        finally:
            office_edge.MAX_FILE_BYTES = old


class TestExtraction(unittest.TestCase):
    def test_extract_docx_stdlib(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml",
                       "<w:document><w:body><w:p><w:r><w:t>Hello Gateway Doc</w:t></w:r></w:p></w:body></w:document>")
        text = office_edge.extract_office(buf.getvalue())
        self.assertIn("Hello Gateway Doc", text)

    def test_extract_xlsx_stdlib(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/worksheets/sheet1.xml",
                       "<worksheet><sheetData><row><c><v>42</v></c></row></sheetData></worksheet>")
            z.writestr("xl/sharedStrings.xml", "<sst><si><t>Cell Text</t></si></sst>")
        text = office_edge.extract_office(buf.getvalue())
        self.assertIn("42", text)

    def test_extract_pdf_stdlib(self):
        payload = zlib.compress(b"BT (Hello PDF Gateway) Tj ET")
        pdf = (b"%PDF-1.4\n<< /Length " + str(len(payload)).encode() + b" >>\nstream\n"
               + payload + b"\nendstream\n%%EOF")
        text = office_edge.extract_pdf(pdf)
        self.assertIn("Hello PDF Gateway", text)

    def test_inline_unknown_binary_415(self):
        meta = office_edge.files_create("blob.bin", "application/octet-stream", bytes(range(64)), "user_message", 60)
        try:
            with self.assertRaises(office_edge.FileInlineError) as cm:
                office_edge.inline_file_block({"type": "file", "file_id": meta["id"]})
            self.assertEqual(cm.exception.code, 415)
        finally:
            office_edge.files_delete(meta["id"])

    def test_inline_missing_404(self):
        with self.assertRaises(office_edge.FileInlineError) as cm:
            office_edge.inline_file_block({"type": "file", "file_id": "file_does_not_exist"})
        self.assertEqual(cm.exception.code, 404)


class TestCCSwitchState(unittest.TestCase):
    def _make_db(self, path):
        """合成一个最小 cc-switch.db：同时存在 claude 与 claude-desktop 两条当前供应商。"""
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE providers (id TEXT, name TEXT, app_type TEXT, "
                    "is_current INTEGER, settings_config TEXT, meta TEXT)")
        con.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        con.execute("INSERT INTO providers VALUES ('1','ClaudeCode Prov','claude',1,?,?)",
                    (json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://code.example"}}), "{}"))
        con.execute("INSERT INTO providers VALUES ('2','DeepSeek','claude-desktop',1,?,?)",
                    (json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"}}),
                     json.dumps({"apiFormat": "anthropic",
                                 "claudeDesktopModelRoutes": {"claude-sonnet-5": {"model": "deepseek-v4-flash"}}})))
        con.execute("INSERT INTO settings VALUES ('claude_desktop_gateway_token','tok-abc')")
        con.commit()
        con.close()

    def test_channel_prefer_claude_desktop(self):
        # 回归：旧版库里 'claude' 是 Claude Code（编辑器）分类，必须优先识别 claude-desktop
        db = os.path.join(tempfile.gettempdir(), "cc-switch-test-%d.db" % os.getpid())
        self._make_db(db)
        old_db, old_ch = office_edge.CCSWITCH_DB, os.environ.get("CCSWITCH_CHANNEL")
        office_edge.CCSWITCH_DB = db
        os.environ["CCSWITCH_CHANNEL"] = "auto"
        try:
            state = office_edge.read_ccswitch_state(force=True)
            self.assertEqual(state["channel"], "claude-desktop")
            self.assertEqual(state["prefix"], "/claude-desktop")
            self.assertEqual(state["token"], "tok-abc")
            self.assertEqual(state["active"]["name"], "DeepSeek")
            self.assertEqual(state["model_entries"],
                             [("claude-sonnet-5", "claude-sonnet-5", "deepseek-v4-flash")])
        finally:
            office_edge.CCSWITCH_DB = old_db
            if old_ch is None:
                os.environ.pop("CCSWITCH_CHANNEL", None)
            else:
                os.environ["CCSWITCH_CHANNEL"] = old_ch
            os.remove(db)
            office_edge.read_ccswitch_state(force=True)  # 还原缓存

    def test_missing_db_reports_error(self):
        old = office_edge.CCSWITCH_DB
        office_edge.CCSWITCH_DB = os.path.join(tempfile.gettempdir(), "no-such-db-%d" % os.getpid())
        try:
            state = office_edge.read_ccswitch_state(force=True)
            self.assertIn("error", state)
            self.assertIsNone(state.get("channel"))
        finally:
            office_edge.CCSWITCH_DB = old
            office_edge.read_ccswitch_state(force=True)  # 还原缓存


if __name__ == "__main__":
    unittest.main(verbosity=2)
