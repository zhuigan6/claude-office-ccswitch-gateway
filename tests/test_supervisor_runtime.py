"""Exercise the real windowless Windows supervisor with an isolated child server."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == "nt", "Windows pythonw lifecycle regression")
class WindowlessLifecycle(unittest.TestCase):
    def test_restarts_owned_child(self):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.exists():
            self.skipTest("pythonw.exe not available")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "gateway").mkdir()
            source = Path(__file__).resolve().parents[1] / "supervisor.py"
            (root / "supervisor.py").write_bytes(source.read_bytes())
            (root / "gateway" / "office_edge.py").write_text(
                "import os\nfrom http.server import BaseHTTPRequestHandler,HTTPServer\n"
                "from pathlib import Path\n"
                "class H(BaseHTTPRequestHandler):\n"
                " def log_message(self,*args): pass\n"
                " def do_GET(self):\n"
                "  body=b'{\"status\":\"ok\"}'\n"
                "  self.send_response(200)\n"
                "  self.send_header('Content-Length',str(len(body)))\n"
                "  self.end_headers()\n"
                "  self.wfile.write(body)\n"
                "srv=HTTPServer(('127.0.0.1',int(os.environ['EDGE_PORT'])),H)\n"
                "Path(os.environ['EDGE_DATA_DIR'],'child.pid').write_text(str(os.getpid()))\n"
                "srv.serve_forever()\n", encoding="utf-8")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, EDGE_PORT=str(port), EDGE_DATA_DIR=str(root))
            proc = subprocess.Popen([str(pythonw), str(root / "supervisor.py")], env=env,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            child = None
            def wait_child(previous=None):
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    self.assertIsNone(proc.poll(), "supervisor exited unexpectedly")
                    try:
                        pid = int((root / "child.pid").read_text())
                        if pid != previous:
                            return pid
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.2)
                self.fail("windowless supervisor did not start/restart its child")
            def stop(pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
            try:
                child = wait_child()
                first = child
                stop(first)
                child = wait_child(first)
                self.assertNotEqual(child, first)
                self.assertIn("restarted pid=", (root / "supervisor.log").read_text())
            finally:
                proc.terminate()
                proc.wait(timeout=10)
                if child:
                    stop(child)
