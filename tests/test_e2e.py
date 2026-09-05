# -*- coding: utf-8 -*-
"""
test_e2e.py —— 端到端测试：真实起网关子进程 + mock CC Switch 上游（纯标准库，零费用，CI 可跑）

覆盖：
  1. healthz / 模型别名
  2. 非流式消息 + 上游 4xx 自动深清洗重试（mock 第一次拒绝 metadata，重试后成功）
  3. 流式消息 + 畸形 SSE 事件防护（坏事件被替换为 gateway_bad_event，好事件原样透传）
  4. Files 上传 -> file_id 内联为文本块后转发
"""
import http.server
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY = os.path.join(REPO, "gateway", "office_edge.py")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockCCSwitch(http.server.BaseHTTPRequestHandler):
    """模拟旧版 CC Switch 代理（claude-desktop 通道）。"""
    request_count = 0
    last_payload = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        if not self.path.endswith("/v1/messages"):
            body = b'{"error":"not found"}'
            self.send_response(404)
        else:
            length = int(self.headers.get("Content-Length", 0) or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            MockCCSwitch.request_count += 1
            MockCCSwitch.last_payload = payload
            if "metadata" in payload:  # 模拟 DeepSeek 类上游拒绝该字段
                body = json.dumps({"type": "error",
                                   "error": {"message": "extra field metadata not allowed"}}).encode()
                self.send_response(400)
            elif payload.get("stream"):
                # 无 Content-Length + Connection: close -> 客户端读至 EOF（标准 SSE 形态）
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                events = [
                    b'event: message_start\n'
                    b'data: {"type":"message_start","message":{"id":"msg_1"}}\n\n',
                    b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n',
                    b'data: {this-is-intentionally-broken\n\n',   # 畸形事件
                    b'data: {"type":"message_stop"}\n\n',
                ]
                for ev in events:
                    try:
                        self.wfile.write(ev)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionError):
                        return
                return
            else:
                body = json.dumps({
                    "id": "msg_1", "type": "message", "role": "assistant",
                    "content": [{"type": "text", "text": "Hi"}],
                    "model": payload.get("model"), "stop_reason": "end_turn",
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                }).encode()
                self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestEndToEnd(unittest.TestCase):
    proc = None
    mock = None
    mock_thread = None
    gateway_port = None
    data_dir = None
    db_path = None

    @classmethod
    def setUpClass(cls):
        # 1) 合成 cc-switch.db（旧版结构：claude-desktop 当前供应商 + 网关令牌）
        cls.data_dir = tempfile.mkdtemp(prefix="edge-e2e-")
        cls.db_path = os.path.join(cls.data_dir, "cc-switch.db")
        con = sqlite3.connect(cls.db_path)
        con.execute("CREATE TABLE providers (id TEXT, name TEXT, app_type TEXT, "
                    "is_current INTEGER, settings_config TEXT, meta TEXT)")
        con.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        con.execute("INSERT INTO providers VALUES ('1','MockProv','claude-desktop',1,?,?)",
                    (json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://mock.example"}}),
                     json.dumps({"apiFormat": "anthropic",
                                 "claudeDesktopModelRoutes": {"claude-sonnet-5": {"model": "mock-v4"}}})))
        con.execute("INSERT INTO settings VALUES ('claude_desktop_gateway_token','tok-e2e')")
        con.commit()
        con.close()

        # 2) mock 上游
        cls.mock_port = free_port()
        cls.mock = http.server.ThreadingHTTPServer(("127.0.0.1", cls.mock_port), MockCCSwitch)
        cls.mock_thread = threading.Thread(target=cls.mock.serve_forever, daemon=True)
        cls.mock_thread.start()

        # 3) 网关子进程
        cls.gateway_port = free_port()
        env = os.environ.copy()
        env.update({
            "EDGE_HOST": "127.0.0.1",
            "EDGE_PORT": str(cls.gateway_port),
            "EDGE_DATA_DIR": os.path.join(cls.data_dir, "runtime"),
            "CCSWITCH_DB": cls.db_path,
            "CCSWITCH_BASE": f"http://127.0.0.1:{cls.mock_port}",
            "CCSWITCH_CHANNEL": "claude-desktop",
            "EDGE_TOKEN": "tok-e2e",
            "EDGE_LOG_REDACT": "1",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, "-u", GATEWAY],
            cwd=REPO, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{cls.gateway_port}/healthz", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError("gateway did not become healthy for e2e tests")

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        if cls.mock:
            cls.mock.shutdown()
            cls.mock.server_close()

    # ---------- helpers ---------- #
    def _req(self, method, path, body=None, headers=None, timeout=15):
        r = urllib.request.Request(f"http://127.0.0.1:{self.gateway_port}{path}",
                                   data=body, method=method)
        for k, v in (headers or {}).items():
            r.add_header(k, v)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            resp = opener.open(r, timeout=timeout)
            return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _auth(self, extra=None):
        h = {"x-api-key": "tok-e2e", "anthropic-version": "2023-06-01"}
        h.update(extra or {})
        return h

    # ---------- tests ---------- #
    def test_01_healthz_and_models(self):
        status, body = self._req("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")
        status, body = self._req("GET", "/v1/models", headers=self._auth())
        self.assertEqual(status, 200)
        models = json.loads(body)["data"]
        self.assertTrue(models and all("claude" in m["id"] for m in models))

    def test_02_non_stream_with_sanitize_retry(self):
        MockCCSwitch.request_count = 0
        payload = {"model": "claude-sonnet-5", "max_tokens": 16,
                   "metadata": {"user_id": "u1"},  # 触发 mock 400 -> 网关深清洗重试
                   "messages": [{"role": "user", "content": "hello"}]}
        status, body = self._req("POST", "/v1/messages", headers=self._auth(),
                                 body=json.dumps(payload).encode())
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(json.loads(body)["content"][0]["text"], "Hi")
        self.assertEqual(MockCCSwitch.request_count, 2, "expected transparent 400 then sanitized retry")
        self.assertNotIn("metadata", MockCCSwitch.last_payload)

    def test_03_stream_malformed_event_guard(self):
        payload = {"model": "claude-sonnet-5", "max_tokens": 16, "stream": True,
                   "messages": [{"role": "user", "content": "hello"}]}
        status, body = self._req("POST", "/v1/messages", headers=self._auth(),
                                 body=json.dumps(payload).encode())
        self.assertEqual(status, 200)
        text = body.decode("utf-8", "replace")
        self.assertIn('"message_start"', text)          # 好事件透传
        self.assertIn("message_stop", text)             # 流完整结束
        self.assertIn("gateway_bad_event", text)        # 畸形事件被替换
        self.assertNotIn("this-is-intentionally-broken", text)  # 原始坏载荷未透传

    def test_04_files_upload_and_inline(self):
        boundary = "e2ebn"
        content = b"HELLO-FILE-CONTENT-FROM-E2E"
        mp = (("--%s\r\n" % boundary).encode() +
              b'Content-Disposition: form-data; name="file"; filename="note.txt"\r\n'
              b"Content-Type: text/plain\r\n\r\n" + content + b"\r\n" +
              ("--%s--\r\n" % boundary).encode())
        status, body = self._req("POST", "/v1/files", headers=self._auth(
            {"Content-Type": f"multipart/form-data; boundary={boundary}"}), body=mp)
        self.assertEqual(status, 200, body[:300])
        fid = json.loads(body)["id"]

        payload = {"model": "claude-sonnet-5", "max_tokens": 16,
                   "messages": [{"role": "user", "content": [
                       {"type": "text", "text": "read this"},
                       {"type": "file", "file_id": fid}]}]}
        status, body = self._req("POST", "/v1/messages", headers=self._auth(),
                                 body=json.dumps(payload).encode())
        self.assertEqual(status, 200, body[:300])
        sent = json.dumps(MockCCSwitch.last_payload, ensure_ascii=False)
        self.assertIn("HELLO-FILE-CONTENT-FROM-E2E", sent)  # file_id 已内联为文本


if __name__ == "__main__":
    unittest.main(verbosity=2)
