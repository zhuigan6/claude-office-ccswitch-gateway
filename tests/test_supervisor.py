# -*- coding: utf-8 -*-
"""test_supervisor.py —— 守护进程单实例锁回归测试（重点：残留死锁接管，不误杀活进程）"""
import importlib.util
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("supervisor_mod", os.path.join(REPO, "supervisor.py"))
supervisor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(supervisor)


class TestPidAlive(unittest.TestCase):
    def test_self_is_alive(self):
        self.assertTrue(supervisor._pid_alive(os.getpid()))

    def test_dead_pid_not_alive(self):
        # 极大且几乎不可能存在的 pid；即使存在也只会让本测试偶发失败，可接受
        self.assertFalse(supervisor._pid_alive(2147483000))
        self.assertFalse(supervisor._pid_alive(0))
        self.assertFalse(supervisor._pid_alive(-1))


class TestLockTakeover(unittest.TestCase):
    def setUp(self):
        self.old_dir = supervisor.DATA_DIR
        self.tmp = tempfile.mkdtemp(prefix="sup-lock-")
        supervisor.DATA_DIR = self.tmp
        supervisor.LOCK_FILE = os.path.join(self.tmp, "supervisor.lock")

    def tearDown(self):
        supervisor.DATA_DIR = self.old_dir
        supervisor.LOCK_FILE = os.path.join(self.old_dir, "supervisor.lock")

    def test_stale_lock_is_taken_over(self):
        # 回归：旧守护被强杀后残留锁文件，新守护必须接管而不是退出
        with open(supervisor.LOCK_FILE, "w", encoding="utf-8") as fh:
            fh.write("2147483000")  # 死 pid
        self.assertTrue(supervisor.acquire_lock())
        with open(supervisor.LOCK_FILE, "r", encoding="utf-8") as fh:
            self.assertEqual(int(fh.read()), os.getpid())
        os.remove(supervisor.LOCK_FILE)

    def test_live_lock_is_respected(self):
        with open(supervisor.LOCK_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))  # 活着的自己 -> 视为已有实例
        self.assertFalse(supervisor.acquire_lock())
        os.remove(supervisor.LOCK_FILE)

    def test_garbage_lock_is_taken_over(self):
        with open(supervisor.LOCK_FILE, "w", encoding="utf-8") as fh:
            fh.write("not-a-pid")
        self.assertTrue(supervisor.acquire_lock())
        os.remove(supervisor.LOCK_FILE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
