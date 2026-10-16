# Accessible AI agent guide

## Purpose

This Windows application turns a deliberate screen capture or phone upload into an answer typed into a selected desktop textbox. The normal pipeline is Claude Opus 5.5 → Codex GPT-6 Luna review → Codex GPT-6 Sol fallback. See `README.md` for setup and operation.

## Code map

- `accessible_ai/__main__.py`: CLI options, startup, desktop integration, and provider selection.
- `accessible_ai/pipeline.py`: model IDs, Codex CLI subprocess, review verdict, and fallback.
- `accessible_ai/claude.py`: isolated Claude Code CLI invocation and image read.
- `accessible_ai/server.py`: image normalization, WebSocket/HTTP service, and generation job.
- `accessible_ai/state.py`: generation and typing state machine.
- `accessible_ai/desktop.py`, `typing_worker.py`, `clipboard.py`, `indicator.py`: Windows hooks, output pacing, paste, and progress dot.
- `accessible_ai/web/index.html`: phone upload page.
- `scripts/host_cloudflare.py`, `scripts/processes.py`, `deploy/`: optional public hosting and Worker proxy.
- `tests/`: unit, browser, proxy, hosted, and native Windows tests.

## Development workflow

1. Make focused changes with tests near the relevant module. Preserve the state machine and keyboard-hook safety behavior.
2. Start with `python -m unittest tests.test_pipeline -v` for model-flow changes, then run `python -m unittest discover -s tests -v`.
3. Run `python tests/e2e_browser.py` for phone-page or WebSocket changes, and `node --test deploy/worker.test.mjs` for Worker changes. Browser E2E uses `--demo` and spends no model usage.
4. The normal app and `tests/e2e_hosted.py` make real model requests. Avoid them for routine tests; disclose when live-provider verification is needed.
5. Do not start desktop-hook or native typing tests against the user's real applications. The native tests open disposable windows and can take focus.

## Safety and operations

- Images and candidate answers contain user data. Do not log their bytes or text, persist them, or put them in test fixtures from real users. Temporary files must be removed after each request.
- Never print, commit, or copy API tokens or CLI authentication files. Provider subprocesses should remain confined to temporary directories, and Codex should remain read-only.
- The phone page and `/ws` have **no login or pairing**. Anyone who reaches the endpoint can submit an image and see the current answer. Do not describe it as authenticated or private.
- Do not run `scripts/host_cloudflare.py` as a test: it can redeploy the live Worker at `manu.wiphk.com`. Use isolated demo/browser/Worker tests instead. Do not deploy or change DNS without explicit user direction.
- Hosting depends on a locally running Windows app and a temporary Quick Tunnel; the static page being online does not mean the backend is available.
- Do not commit `.runtime/`, virtual environments, `node_modules/`, generated caches, credentials, or legacy handoff notes.
