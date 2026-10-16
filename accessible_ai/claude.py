import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile


DEFAULT_PROMPT = "Answer the question or task visible in this image. If it is unclear, explain what you can see and what information is missing. Return only the answer in plain text, suitable for typing into a textbox."


def find_claude():
    executable = shutil.which("claude.exe") or shutil.which("claude")
    if not executable:
        raise RuntimeError("Claude Code was not found on PATH. Install it and run 'claude auth login'.")
    if Path(executable).suffix.lower() in {".cmd", ".ps1", ".bat"}:
        package = Path(executable).parent / "node_modules/@anthropic-ai/claude-code"
        native = package / "bin/claude.exe"
        if native.is_file():
            return [str(native)]
        entry = next((package / filename for filename in ("cli.js", "cli-wrapper.cjs") if (package / filename).is_file()), None)
        node = shutil.which("node")
        if not node or not entry:
            raise RuntimeError("Cannot resolve the Claude npm installation. Install the native Claude CLI or fix its node_modules directory.")
        return [node, str(entry)]
    return [executable]


class Claude:
    def __init__(self, model=None, timeout=180, demo=False):
        self.model, self.timeout, self.demo = model, timeout, demo

    async def generate(self, image, prompt):
        if self.demo:
            return "This is a demo response. Each keypress types the next character."
        command = find_claude()
        with tempfile.TemporaryDirectory(prefix="accessible-ai-") as directory:
            image_path = Path(directory) / "input.png"
            image_path.write_bytes(image)
            command += [
                "-p", "--output-format", "json", "--tools", "Read",
                "--allowedTools", "Read", "--strict-mcp-config",
                "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
                "--settings", '{"disableAllHooks":true}', "--disable-slash-commands",
                "--no-session-persistence", "--max-turns", "4",
            ]
            if self.model:
                command += ["--model", self.model]
            instruction = f"Read the image at {image_path.as_posix()} using the Read tool.\n{prompt or DEFAULT_PROMPT}"
            environment = os.environ.copy()
            environment.pop("CLAUDECODE", None)
            process = await asyncio.create_subprocess_exec(
                *command, cwd=directory, env=environment,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(instruction.encode("utf-8")), self.timeout)
            except (TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    process.kill()
                await process.communicate()
                raise
            output = stdout.decode("utf-8", errors="replace").strip()
            if process.returncode:
                detail = output or stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"Claude Code failed: {detail[:1500]}")
            try:
                result = json.loads(output)
            except json.JSONDecodeError as error:
                raise RuntimeError("Claude Code returned invalid JSON.") from error
            if result.get("is_error"):
                raise RuntimeError(str(result.get("result") or result.get("errors") or "Claude could not complete the request."))
            text = result.get("result", "")
            if not isinstance(text, str) or not text.strip():
                raise RuntimeError("Claude returned no answer. Check authentication and model access.")
            return text.strip()
