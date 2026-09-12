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
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from ctypes import wintypes

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


def _expand_path(value: str) -> str:
    value = os.path.expandvars(os.path.expanduser(value))
    return os.path.abspath(value if os.path.isabs(value) else os.path.join(HERE, value))


DATA_DIR = _expand_path(os.getenv("EDGE_DATA_DIR") or os.path.join(HERE, "runtime"))
PORT = int(os.getenv("EDGE_PORT", "8790"))
HEALTH_URL = f"http://127.0.0.1:{PORT}/healthz"
CHECK_INTERVAL = 5
FAILURE_THRESHOLD = 3
LOCK_FILE = os.path.join(DATA_DIR, "supervisor.lock")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _log(*a):
    line = "[supervisor] %s %s" % (time.strftime("%H:%M:%S"), " ".join(str(item) for item in a))
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, "supervisor.log")
        if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 * 1024:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            print(line, file=stream, flush=True)
        except (OSError, ValueError):
            pass


def _pid_alive(pid: int) -> bool:
    """只读探活。注意不能用 os.kill(pid, 0)：Windows 上那会调 TerminateProcess 误杀活进程。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                status = wintypes.DWORD()
                return bool(k32.GetExitCodeProcess(handle, ctypes.byref(status))) and status.value == 259
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_lock() -> bool:
    """单实例：锁文件 + 存活 pid 检查。残留的死锁文件自动接管。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(LOCK_FILE):
        old_pid = 0
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as fh:
                old_pid = int(fh.read().strip() or 0)
        except (ValueError, OSError):
            old_pid = 0
        if old_pid and _pid_alive(old_pid):
            _log(f"another supervisor is running (pid {old_pid}), exiting")
            return False
        try:  # 死锁残留：接管
            os.remove(LOCK_FILE)
        except OSError:
            pass
    try:
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
        creationflags=CREATE_NO_WINDOW, check=False, timeout=5,
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
    # A port number does not establish process ownership. Never kill another listener.
    try:
        occupied = listener_pids()
    except (OSError, subprocess.TimeoutExpired):
        _log("listener check failed; postponing restart")
        return False
    if occupied:
        _log("port is occupied; leaving existing listeners untouched:", sorted(occupied))
        return False
    return True


def start_gateway():
    os.makedirs(DATA_DIR, exist_ok=True)
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe"):
        pythonw = exe[:-10] + "pythonw.exe"  # 无窗口运行
        if os.path.exists(pythonw):
            exe = pythonw
    env = os.environ.copy()
    env.setdefault("EDGE_DATA_DIR", DATA_DIR)
    with open(os.path.join(DATA_DIR, "edge-sup.out"), "ab", buffering=0) as out, \
            open(os.path.join(DATA_DIR, "edge-sup.err"), "ab", buffering=0) as err:
        return subprocess.Popen(
            [exe, GATEWAY],
            cwd=HERE, env=env, stdin=subprocess.DEVNULL,
            stdout=out, stderr=err,
            creationflags=CREATE_NO_WINDOW,
        )


def stop_child(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


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
                if not stop_stale_listeners():
                    time.sleep(CHECK_INTERVAL)
                    continue
                child = start_gateway()
                _log(f"gateway started pid={child.pid}")
                time.sleep(5)
                continue
            failures += 1
            if failures < FAILURE_THRESHOLD:
                time.sleep(CHECK_INTERVAL)
                continue
            _log(f"health check failed {failures} times, restarting gateway")
            stop_child(child)
            if not stop_stale_listeners():
                time.sleep(CHECK_INTERVAL)
                continue
            child = start_gateway()
            _log(f"gateway restarted pid={child.pid}")
            failures = 0
            time.sleep(5)
    except KeyboardInterrupt:
        _log("supervisor stopped by user")
        return 0
    finally:
        stop_child(child)
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
