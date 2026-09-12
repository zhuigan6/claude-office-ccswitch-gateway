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
import http.client
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
    """与网关一致的探测方式：SO_REUSEADDR + bind(0) + listen 后关闭，取可用端口。"""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port


class MockCCSwitch(http.server.BaseHTTPRequestHandler):
    """模拟旧版 CC Switch 代理（claude-desktop 通道）。"""
    request_count = 0
    last_payload = None
    last_headers = None
    release_stream = threading.Event()
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path.endswith("/v1/messages/count_tokens"):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            MockCCSwitch.last_payload = payload
            MockCCSwitch.last_headers = dict(self.headers)
            body = b'{"input_tokens":7}'
            self.send_response(200)
        elif not self.path.endswith("/v1/messages"):
            body = b'{"error":"not found"}'
            self.send_response(404)
        else:
            length = int(self.headers.get("Content-Length", 0) or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            MockCCSwitch.request_count += 1
            MockCCSwitch.last_payload = payload
            MockCCSwitch.last_headers = dict(self.headers)
            if payload.get("model") == "fail-502":
                body = b'{"message":"metadata not allowed"}'
                self.send_response(502)
            elif "metadata" in payload:  # 模拟 DeepSeek 类上游拒绝该字段
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
                        if payload.get("model") == "delayed-stream" and ev is events[0]:
                            MockCCSwitch.release_stream.wait(5)
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


# macOS CI 已知限制：runner 上网关“子进程”在 bind/listen 阶段阻塞（同进程 mock 正常、
# 三端口重试均复现，证据见 CI 日志）。e2e 覆盖 Windows + Ubuntu；macOS 仍跑全部单元测试。
E2E_UNSUPPORTED = sys.platform == "darwin"


@unittest.skipIf(E2E_UNSUPPORTED, "macOS runner: gateway subprocess stalls in bind/listen; e2e runs on Windows/Ubuntu")
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

        # 3) 网关子进程：多端口重试（应对 CI 上偶发的 bind 卡死/端口竞争）
        base_env = os.environ.copy()
        base_env.update({
            "EDGE_HOST": "127.0.0.1",
            "EDGE_DATA_DIR": os.path.join(cls.data_dir, "runtime"),
            "CCSWITCH_DB": cls.db_path,
            "CCSWITCH_BASE": f"http://127.0.0.1:{cls.mock_port}",
            "CCSWITCH_CHANNEL": "claude-desktop",
            "EDGE_TOKEN": "tok-e2e",
            "EDGE_LOG_REDACT": "1",
        })
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        cls._gateway_log = open(os.path.join(cls.data_dir, "gateway-log.txt"), "ab")
        last_err = None
        for attempt in range(3):
            cls.gateway_port = free_port()
            env = dict(base_env, EDGE_PORT=str(cls.gateway_port))
            cls.proc = subprocess.Popen(
                [sys.executable, "-u", GATEWAY],
                cwd=REPO, env=env,
                stdout=cls._gateway_log, stderr=cls._gateway_log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            deadline = time.time() + 10
            healthy = False
            while time.time() < deadline:
                try:
                    with opener.open(f"http://127.0.0.1:{cls.gateway_port}/healthz", timeout=2) as r:
                        if r.status == 200:
                            healthy = True
                            break
                except Exception:
                    time.sleep(0.3)
            if healthy:
                last_err = None
                break
            last_err = f"attempt {attempt + 1}: poll={cls.proc.poll()!r} port={cls.gateway_port}"
            try:
                cls.proc.kill()
                cls.proc.wait(timeout=5)
            except Exception:
                pass
            time.sleep(1)
        if last_err is not None:
            try:
                cls._gateway_log.flush()
                with open(os.path.join(cls.data_dir, "gateway-log.txt"), "r",
                          encoding="utf-8", errors="replace") as fh:
                    tail = fh.read()[-1500:]
            except OSError:
                tail = "(log unreadable)"
            raise RuntimeError(
                f"gateway did not become healthy ({last_err})\n[gateway log tail]\n{tail or '(empty log)'}")

    @classmethod
    def tearDownClass(cls):
        if cls.proc:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        if cls._gateway_log:
            try:
                cls._gateway_log.close()
            except OSError:
                pass
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
        status, body = self._req("GET", "/status/ccswitch")
        self.assertEqual(status, 200)
        public_state = json.loads(body)
        self.assertNotIn("token", public_state)
        self.assertNotIn("active", public_state)

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


    def test_05_sanitize_memory_halves_roundtrips(self):
        # 依赖 test_02 已让 mock 上游"学会"metadata 被拒（ADR-0008）：
        # 第二次带 metadata 的请求应被预清洗，一次命中上游而非两次
        MockCCSwitch.request_count = 0
        payload = {"model": "claude-sonnet-5", "max_tokens": 16,
                   "metadata": {"user_id": "u2"},
                   "messages": [{"role": "user", "content": "hello again"}]}
        status, body = self._req("POST", "/v1/messages", headers=self._auth(),
                                 body=json.dumps(payload).encode())
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(MockCCSwitch.request_count, 1,
                         "expected pre-sanitized single roundtrip after memory learned")
        self.assertNotIn("metadata", MockCCSwitch.last_payload)


    def test_06_auth_error_carries_code(self):
        status, body = self._req("GET", "/v1/files", headers={"x-api-key": "definitely-wrong"})
        self.assertEqual(status, 401)
        err = json.loads(body)["error"]
        self.assertEqual(err.get("code"), "invalid_gateway_token")
        self.assertIn("suggestion", err)

    def test_07_cors_rejects_untrusted_origin(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.gateway_port}/v1/messages",
            method="OPTIONS",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=10)
        self.assertEqual(caught.exception.code, 403)
        self.assertIsNone(caught.exception.headers.get("Access-Control-Allow-Origin"))

    def test_08_rejects_non_object_json_and_simple_cross_origin_posts(self):
        for payload in ([], None, "text", 1):
            status, _ = self._req("POST", "/v1/messages", body=json.dumps(payload).encode(), headers=self._auth())
            self.assertEqual(status, 400)
        status, _ = self._req("POST", "/v1/messages", body=b'{}',
                             headers=self._auth({"Origin": "https://untrusted.example"}))
        self.assertEqual(status, 403)

    def test_09_does_not_retry_server_errors(self):
        MockCCSwitch.request_count = 0
        status, _ = self._req("POST", "/v1/messages", headers=self._auth(),
                             body=json.dumps({"model": "fail-502", "metadata": {}, "messages": []}).encode())
        self.assertEqual(status, 502)
        self.assertEqual(MockCCSwitch.request_count, 1)

    def test_10_retry_preserves_thinking_and_tools(self):
        payload = {"model": "new-model", "metadata": {}, "thinking": {"type": "enabled"},
                   "tools": [{"name": "run", "input_schema": {}, "strict": True}],
                   "tool_choice": {"type": "tool", "name": "run"},
                   "messages": [{"role": "user", "content": "hello"}]}
        status, _ = self._req("POST", "/v1/messages", headers=self._auth(), body=json.dumps(payload).encode())
        self.assertEqual(status, 200)
        self.assertEqual(MockCCSwitch.last_payload["thinking"], payload["thinking"])
        self.assertEqual(MockCCSwitch.last_payload["tools"], payload["tools"])
        self.assertEqual(MockCCSwitch.last_payload["tool_choice"], payload["tool_choice"])

    def test_11_first_sse_event_arrives_before_upstream_finishes(self):
        MockCCSwitch.release_stream.clear()
        conn = http.client.HTTPConnection("127.0.0.1", self.gateway_port, timeout=2)
        try:
            conn.request("POST", "/v1/messages", headers=self._auth(),
                         body=json.dumps({"model": "delayed-stream", "stream": True, "messages": []}).encode())
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.readline(), b"event: message_start\n")
        finally:
            MockCCSwitch.release_stream.set()
            conn.close()

    def test_12_count_tokens_routes_alias_and_preserves_beta_headers(self):
        status, body = self._req("POST", "/v1/messages/count_tokens", headers=self._auth({"anthropic-beta": "test-beta"}),
                                 body=json.dumps({"model": "claude-sonnet-5--ccswitch--mock", "messages": []}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["input_tokens"], 7)
        self.assertEqual(MockCCSwitch.last_payload["model"], "claude-sonnet-5")
        self.assertEqual(MockCCSwitch.last_headers["anthropic-beta"], "test-beta")

    def test_13_unicode_filename_download(self):
        boundary = "unicode-file"
        mp = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="\u6d4b\u8bd5.txt"\r\n'
              f'Content-Type: text/plain\r\n\r\nhello\r\n--{boundary}--\r\n').encode()
        status, body = self._req("POST", "/v1/files", headers=self._auth({"Content-Type": f"multipart/form-data; boundary={boundary}"}), body=mp)
        self.assertEqual(status, 200)
        fid = json.loads(body)["id"]
        try:
            status, body = self._req("GET", f"/v1/files/{fid}/content", headers=self._auth())
            self.assertEqual(status, 200)
            self.assertEqual(body, b"hello")
        finally:
            self._req("DELETE", f"/v1/files/{fid}", headers=self._auth())


if __name__ == "__main__":
    unittest.main(verbosity=2)
