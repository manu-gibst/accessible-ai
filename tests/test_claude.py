import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from accessible_ai.claude import Claude, find_claude


class DiscoveryTests(unittest.TestCase):
    def test_npm_shim_resolves_bundled_native_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
            native.parent.mkdir(parents=True)
            native.touch()
            with patch("shutil.which", side_effect=lambda name: str(Path(directory) / "claude.cmd") if name == "claude" else None):
                self.assertEqual(find_claude(), [str(native)])

    def test_legacy_npm_shim_resolves_javascript_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "node_modules/@anthropic-ai/claude-code/cli.js"
            entry.parent.mkdir(parents=True)
            entry.touch()
            def which(name):
                return {"claude": str(Path(directory) / "claude.cmd"), "node": "node.exe"}.get(name)
            with patch("shutil.which", side_effect=which):
                self.assertEqual(find_claude(), ["node.exe", str(entry)])


class ClaudeTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_uses_image_read_and_plain_result(self):
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(json.dumps({"result": "The answer", "is_error": False}).encode(), b""))
        with patch("accessible_ai.claude.find_claude", return_value=["claude.exe"]), patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            self.assertEqual(await Claude().generate(b"image", "Solve it"), "The answer")
            command = spawn.call_args.args
            self.assertEqual(command[command.index("--tools") + 1], "Read")
            self.assertIn("Read the image at", process.communicate.call_args.args[0].decode())

    async def test_cli_error_is_not_treated_as_answer(self):
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(b'{"is_error":true,"result":"Login required"}', b""))
        with patch("accessible_ai.claude.find_claude", return_value=["claude.exe"]), patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            with self.assertRaisesRegex(RuntimeError, "Login required"):
                await Claude().generate(b"image", "Solve it")

    async def test_timeout_kills_and_reaps_child(self):
        process = Mock(returncode=None)
        async def communicate(data=None):
            if data:
                await asyncio.sleep(5)
            return b"", b""
        process.communicate = AsyncMock(side_effect=communicate)
        with patch("accessible_ai.claude.find_claude", return_value=["claude.exe"]), patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            with self.assertRaises(TimeoutError):
                await Claude(timeout=0.01).generate(b"image", "Solve it")
            process.kill.assert_called_once()
