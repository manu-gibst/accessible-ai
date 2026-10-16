import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from accessible_ai.pipeline import Codex, ReviewedAnswer, find_codex


class CodexDiscoveryTests(unittest.TestCase):
    def test_npm_shim_resolves_javascript_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "node_modules/@openai/codex/bin/codex.js"
            entry.parent.mkdir(parents=True)
            entry.touch()
            with patch("accessible_ai.pipeline.shutil.which", side_effect=lambda name: {
                "codex": str(Path(directory) / "codex.cmd"), "node": "node.exe"
            }.get(name)):
                self.assertEqual(find_codex(), ["node.exe", str(entry)])


class CodexTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_and_model_passed_to_codex(self):
        process = Mock(returncode=0)

        async def communicate(data=None):
            command = spawn.call_args.args
            Path(command[command.index("--output-last-message") + 1]).write_text("The answer", encoding="utf-8")
            return b"", b""

        process.communicate = AsyncMock(side_effect=communicate)
        with patch("accessible_ai.pipeline.find_codex", return_value=["codex.exe"]), patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)
        ) as spawn:
            self.assertEqual(await Codex().ask(b"image", "Solve this", "small-model"), "The answer")
            command = spawn.call_args.args
            self.assertIn("--image", command)
            self.assertEqual(command[command.index("--model") + 1], "small-model")
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            self.assertEqual(process.communicate.call_args.args[0], b"Solve this")

    async def test_timeout_kills_child(self):
        process = Mock(returncode=None)

        async def communicate(data=None):
            if data:
                await asyncio.sleep(5)
            return b"", b""

        process.communicate = AsyncMock(side_effect=communicate)
        with patch("accessible_ai.pipeline.find_codex", return_value=["codex.exe"]), patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)
        ):
            with self.assertRaises(TimeoutError):
                await Codex(timeout=0.01).ask(b"image", "Solve this", "small-model")
            process.kill.assert_called_once()


class ReviewedAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_good_review_returns_original(self):
        pipeline = ReviewedAnswer()
        pipeline.claude.generate = AsyncMock(return_value="First answer")
        pipeline.codex.ask = AsyncMock(return_value="GOOD")
        self.assertEqual(await pipeline.generate(b"image", "Solve this"), "First answer")
        pipeline.codex.ask.assert_awaited_once()
        self.assertIn("First answer", pipeline.codex.ask.call_args.args[1])

    async def test_bad_review_generates_new_answer_from_original_question(self):
        pipeline = ReviewedAnswer()
        pipeline.claude.generate = AsyncMock(return_value="Wrong answer")
        pipeline.codex.ask = AsyncMock(side_effect=["BAD", "Correct answer"])
        self.assertEqual(await pipeline.generate(b"image", "Solve this"), "Correct answer")
        self.assertEqual(pipeline.codex.ask.await_args_list[0].args[2], pipeline.review_model)
        self.assertEqual(pipeline.codex.ask.await_args_list[1].args[2], pipeline.fallback_model)
        self.assertNotIn("Wrong answer", pipeline.codex.ask.await_args_list[1].args[1])

    async def test_reviewer_failure_uses_fallback(self):
        pipeline = ReviewedAnswer()
        pipeline.claude.generate = AsyncMock(return_value="First answer")
        pipeline.codex.ask = AsyncMock(side_effect=[RuntimeError("review failed"), "Fallback answer"])
        self.assertEqual(await pipeline.generate(b"image", "Solve this"), "Fallback answer")

    async def test_claude_failure_uses_fallback(self):
        pipeline = ReviewedAnswer()
        pipeline.claude.generate = AsyncMock(side_effect=RuntimeError("Claude unavailable"))
        pipeline.codex.ask = AsyncMock(return_value="Fallback answer")
        self.assertEqual(await pipeline.generate(b"image", "Solve this"), "Fallback answer")
        pipeline.codex.ask.assert_awaited_once()
        self.assertEqual(pipeline.codex.ask.call_args.args[2], pipeline.fallback_model)

    async def test_ambiguous_verdict_uses_fallback(self):
        pipeline = ReviewedAnswer()
        pipeline.claude.generate = AsyncMock(return_value="First answer")
        pipeline.codex.ask = AsyncMock(side_effect=["GOOD, but maybe not", "Fallback answer"])
        self.assertEqual(await pipeline.generate(b"image", "Solve this"), "Fallback answer")
