# -*- coding: utf-8 -*-
"""
supervisor.py —— 网关守护进程（纯标准库）

职责：
  1) 单实例锁：防止重复启动多个守护；
  2) 应用层健康探测：每 5 秒真正请求 /healthz（不是只看端口/进程死活）；
  3) 连续 3 次失败 → 清理僵死监听进程并重启网关（识别“端口在但服务假死”）；
  4) 网关用 pythonw（无窗口）拉起，stdout/stderr 落盘到 runtime/ 供排障。

由计划任务或 HKCU Run 在用户登录时启动（见 install.ps1）。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
GATEWAY = os.path.join(HERE, "gateway", "office_edge.py")


def _load_env_file(path=None):
    """极简 .env 加载（不覆盖已存在的环境变量）。"""
    p = path or os.path.join(HERE, ".env")
    try:
        with open(p, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


_load_env_file()

DATA_DIR = os.path.abspath(os.getenv("EDGE_DATA_DIR") or os.path.join(HERE, "runtime"))
PORT = int(os.getenv("EDGE_PORT", "8790"))
HEALTH_URL = f"http://127.0.0.1:{PORT}/healthz"
CHECK_INTERVAL = 5
FAILURE_THRESHOLD = 3
LOCK_FILE = os.path.join(DATA_DIR, "supervisor.lock")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _log(*a):
    print("[supervisor]", time.strftime("%H:%M:%S"), *a, flush=True)


def acquire_lock() -> bool:
    """单实例：锁文件 + 存活 pid 检查（跨平台，无需管理员权限）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE, "r", encoding="utf-8") as fh:
                    old_pid = int(fh.read().strip() or 0)
                os.kill(old_pid, 0)  # 进程仍活着（Windows 上对其他进程会抛 PermissionError，同样视为活着）
                _log(f"another supervisor is running (pid {old_pid}), exiting")
                return False
            except (ValueError, ProcessLookupError):
                pass  # 残留锁文件，接管
            except PermissionError:
                _log("another supervisor is running, exiting")
                return False
            except OSError:
                pass
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return True
    except FileExistsError:
        _log("lock race lost, exiting")
        return False


def is_healthy() -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本地探测绝不走系统代理
    try:
        with opener.open(HEALTH_URL, timeout=3) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read(1024).decode("utf-8", "ignore"))
            return data.get("status") == "ok"
    except Exception:
        return False


def listener_pids() -> set:
    if os.name != "nt":  # 非 Windows：交给子进程管理即可
        return set()
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True, text=True, errors="replace",
        creationflags=CREATE_NO_WINDOW, check=False,
    )
    suffix = f":{PORT}"
    pids = set()
    for line in result.stdout.splitlines():
        cols = line.split()
        # Windows netstat 区域设置可能是“本地地址”列名不同，但列结构固定：Proto/Local/Foreign/State/PID
        if len(cols) >= 5 and cols[1].endswith(suffix) and "LISTEN" in cols[3].upper():
            try:
                pids.add(int(cols[4]))
            except ValueError:
                pass
    return pids


def stop_stale_listeners():
    for pid in listener_pids():
        _log(f"killing stale listener pid={pid}")
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW, check=False,
        )


def start_gateway():
    os.makedirs(DATA_DIR, exist_ok=True)
    out = open(os.path.join(DATA_DIR, "edge-sup.out"), "ab", buffering=0)
    err = open(os.path.join(DATA_DIR, "edge-sup.err"), "ab", buffering=0)
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        pythonw = exe[:-10] + "pythonw.exe"  # 无窗口运行
        if os.path.exists(pythonw):
            exe = pythonw
    env = os.environ.copy()
    env.setdefault("EDGE_DATA_DIR", DATA_DIR)
    return subprocess.Popen(
        [exe, GATEWAY],
        cwd=HERE, env=env, stdin=subprocess.DEVNULL,
        stdout=out, stderr=err,
        creationflags=CREATE_NO_WINDOW,
    )


def main():
    if not acquire_lock():
        return 1
    _log(f"watching http://127.0.0.1:{PORT}/healthz (interval={CHECK_INTERVAL}s, threshold={FAILURE_THRESHOLD})")
    child = None
    failures = 0
    try:
        while True:
            if is_healthy():
                failures = 0
                time.sleep(CHECK_INTERVAL)
                continue
            if child is None:
                stop_stale_listeners()
                child = start_gateway()
                _log(f"gateway started pid={child.pid}")
                time.sleep(5)
                continue
            failures += 1
            if failures < FAILURE_THRESHOLD:
                time.sleep(CHECK_INTERVAL)
                continue
            _log(f"health check failed {failures} times, restarting gateway")
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
            stop_stale_listeners()
            child = start_gateway()
            _log(f"gateway restarted pid={child.pid}")
            failures = 0
            time.sleep(5)
    except KeyboardInterrupt:
        _log("supervisor stopped by user")
        return 0
    finally:
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
