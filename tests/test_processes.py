import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from scripts.processes import start_managed, stop


class ManagedProcessTests(unittest.TestCase):
    def test_stop_closes_job_even_after_parent_exit_and_is_idempotent(self):
        job = Mock()
        process = Mock(_owned_job=job, poll=Mock(return_value=0))
        stop(process)
        stop(process)
        job.close.assert_called_once()
        process.terminate.assert_not_called()

    def test_assignment_failure_closes_job_and_reaps_suspended_child(self):
        process = Mock(poll=Mock(return_value=None))
        job = Mock()
        job.assign.side_effect = OSError("assignment failed")
        with patch("scripts.processes.WINDOWS", True), patch("scripts.processes.WindowsJob", return_value=job), patch("scripts.processes.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(OSError, "assignment failed"):
                start_managed(["harmless"])
        job.close.assert_called_once()
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=8)
        job.resume.assert_not_called()

    def test_spawn_failure_closes_job(self):
        job = Mock()
        with patch("scripts.processes.WINDOWS", True), patch("scripts.processes.WindowsJob", return_value=job), patch("scripts.processes.subprocess.Popen", side_effect=OSError("spawn failed")):
            with self.assertRaisesRegex(OSError, "spawn failed"):
                start_managed(["harmless"])
        job.close.assert_called_once()

    def test_resume_failure_closes_job_and_reaps_child(self):
        job = Mock()
        job.resume.side_effect = OSError("resume failed")
        process = Mock(poll=Mock(return_value=None))
        with patch("scripts.processes.WINDOWS", True), patch("scripts.processes.WindowsJob", return_value=job), patch("scripts.processes.subprocess.Popen", return_value=process) as spawn:
            with self.assertRaisesRegex(OSError, "resume failed"):
                start_managed(["harmless"], creationflags=0x08000000)
        self.assertEqual(spawn.call_args.kwargs["creationflags"], 0x08000004)
        job.assign.assert_called_once_with(process)
        job.close.assert_called_once()
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=8)

    def test_non_windows_fallback_terminates_and_reaps(self):
        process = Mock(poll=Mock(return_value=None), _owned_job=None)
        process.wait.side_effect = [subprocess.TimeoutExpired("harmless", 8), 0]
        with patch("scripts.processes.WINDOWS", False), patch("scripts.processes.subprocess.Popen", return_value=process):
            self.assertIs(start_managed(["harmless"]), process)
        stop(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    @unittest.skipUnless(os.name == "nt", "Requires native Windows Job Objects")
    def test_native_job_kills_child_after_parent_has_exited(self):
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        script = (
            "import subprocess, sys; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000); "
            "print(child.pid, flush=True)"
        )
        parent = start_managed([sys.executable, "-c", script], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
        child_handle = None
        try:
            output, errors = parent.communicate(timeout=10)
            self.assertEqual(parent.returncode, 0, errors)
            child_handle = api.OpenProcess(0x00100001, False, int(output.strip()))
            self.assertTrue(child_handle, "Descendant must still exist after parent exit")
            self.assertEqual(api.WaitForSingleObject(child_handle, 0), 258)
            stop(parent)
            self.assertEqual(api.WaitForSingleObject(child_handle, 5000), 0,
                             "Closing the exited parent's job must terminate its child")
            stop(parent)
        finally:
            stop(parent)
            if child_handle:
                if api.WaitForSingleObject(child_handle, 0) == 258:
                    api.TerminateProcess(child_handle, 1)
                    api.WaitForSingleObject(child_handle, 5000)
                api.CloseHandle(child_handle)
            parent.stdout.close()
            parent.stderr.close()
