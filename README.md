# Accessible AI

A Windows app that turns a screenshot or phone photo into a reviewed AI response, then types it into your selected textbox at your pace.

For contributors and coding agents, see [AGENTS.md](AGENTS.md). The app is local-first; the optional public phone page has **no access control**.

## Setup (PowerShell)

Requires Windows 10/11, Python 3.11+, and installed, authenticated Claude Code and Codex CLIs. It uses those CLI logins and their model access; it does not require separate API integrations. Both providers' usage counts toward their respective accounts.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
claude auth login
codex login
.\.venv\Scripts\python -m accessible_ai
```

Run from a normal terminal on your Windows desktop. The app prints its URLs. Use `127.0.0.1` on the computer; open the URL with your computer's LAN IP on your phone, on the same Wi-Fi. If Windows Firewall prompts, allow the app on your private network. Guest Wi-Fi isolation or a VPN can prevent phone access. There is no pairing or login: anyone who can reach the site can submit images and view responses while the desktop app is connected.

## Using it

1. **Screenshot:** hold the pointer within six pixels of the primary screen's **top-right corner** for 0.45 seconds. It captures that screen once and sends the image to the AI pipeline. Leave the corner before triggering again.
2. **Phone:** open the website URL. Choose **Take a photo** or **Choose a picture**, optionally add instructions, and tap **Get answer**. Images and response updates travel through a WebSocket. The camera button uses the phone browser's file/camera picker and works without a live camera stream. Choose JPEG/PNG/WebP if your phone defaults to HEIC.
3. A 7-pixel `#b70000` dot appears near the top-right of the laptop screen while the answer is generated and reviewed. It disappears when generation ends and hides during screenshots.
4. Wait until the page shows the answer is ready. Click your destination textbox on the computer, then hold the pointer in the **bottom-left corner** for 0.45 seconds. Hovering preserves keyboard focus.
5. Each fresh press of a letter, number, punctuation, Space, Backspace, Tab, or Enter queues the next response character instead of that physical key. Holding a key doesn't advance repeatedly. Shift alone doesn't advance. Unicode characters are supported. You can press quickly; queued characters are sent to the destination at a steady pace. **Ctrl+V pastes the entire remaining answer immediately**, replacing your clipboard contents with that text.
6. **Escape pauses** and suppresses that Escape press. A navigation/function key or Ctrl/Alt/Windows shortcut other than Ctrl+V pauses and passes through normally. Revisit the bottom-left corner to resume. Changing the foreground window also pauses on the next typing key. After the final character, extra typing keys are ignored while the pointer remains in the bottom-left corner; move it away to resume normal typing. If it was already away, the pointer moves to the screen center as before.
7. Press **Ctrl+C in the app's terminal** to stop it.

Only one response is active. New requests are rejected during generation or typing. Submitting another image while ready or paused replaces the previous answer. The first version checks the foreground window, so selecting a different textbox within the same window does not pause it. Newlines are sent as Enter and tabs as Tab: destination apps may submit a form or change focus. Prefer a multiline text editor for responses with these characters. Windows blocks input into some elevated or protected apps.

## Options

```powershell
# Select pipeline models and a longer corner dwell
.\.venv\Scripts\python -m accessible_ai --model claude-opus-5-5 --review-model gpt-6-luna --fallback-model gpt-6-sol --dwell 0.7

# Custom default instructions, used for screenshots and phone uploads without instructions
.\.venv\Scripts\python -m accessible_ai --prompt "Read the image and answer briefly in English."
.\.venv\Scripts\python -m accessible_ai --prompt-file instructions.txt

# Try the phone flow without calling Claude or installing keyboard hooks
.\.venv\Scripts\python -m accessible_ai --demo --no-desktop

# Restrict access to this computer
.\.venv\Scripts\python -m accessible_ai --host 127.0.0.1

# Optional HTTPS/WSS with a certificate trusted by your phone
.\.venv\Scripts\python -m accessible_ai --cert certificate.pem --key private-key.pem
```

Default port: `8765`. Each AI stage has a `180`-second timeout (`--timeout`); an image can take up to three stages. The phone interface can also run without desktop control on other operating systems with `--no-desktop`. Model access depends on your CLI accounts; override an unavailable model with the corresponding option.

HTTP/WS is intended for a trusted local network. For encrypted traffic, provide a trusted certificate with `--cert` and `--key`. No public hosting or relay is required. Live camera APIs are deliberately unnecessary; the capture file picker works on mobile browsers over local HTTP, subject to device/browser support.

## How it works

`desktop.py` installs a Windows low-level keyboard hook and monitors screen corners. It suppresses physical typing keys only while armed, ignores injected events, and queues response characters for a separate worker to send through Windows `SendInput` at a steady pace. It doesn't record physical key text. `indicator.py` displays a small, non-activating red dot during generation. `state.py` manages generation, ready, typing, pause, and completion states.

`server.py` serves the phone page and an unauthenticated WebSocket on the same port. Images are limited to 10 MB and 25 megapixels, rotated according to EXIF, stripped of metadata, and resized to at most 2000 × 2000 before submission. Images are never captured automatically outside the corner trigger or explicit phone upload.

`claude.py` launches `claude -p --output-format json` in a temporary directory, with only the `Read` tool and no MCP servers or hooks. `pipeline.py` asks Claude Opus 5.5 for an answer, asks Codex GPT-6 Luna to check that answer against the original image and instructions, then returns it only for an exact `GOOD` verdict. A `BAD`, ambiguous, or failed review triggers a fresh answer from Codex GPT-6 Sol. If Claude generation itself fails, Sol also answers directly. If fallback generation fails, the request reports an error rather than returning an unreviewed answer. Codex runs in read-only mode in its own temporary directory. Requests use existing CLI authentication and ephemeral sessions; temporary images are removed afterward. Images are sent to both providers. Their operational logs and retention policies may still apply. See [Claude's programmatic usage](https://code.claude.com/docs/en/headless) and [Codex non-interactive use](https://developers.openai.com/codex/non-interactive/).

## Checks

```powershell
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python tests/e2e_browser.py
```

Tests cover corner dwell, typing state and resumption, focus changes, physical key suppression, held-key repeats, injected keys, final pointer movement, HTTP serving, direct WebSocket upload/status, malformed images, pipeline verdict/fallback, and CLI errors/timeouts. They use mocked keyboard output and model responses, so they do not install hooks or spend provider usage. Actual OS input and phone camera behavior need a manual desktop/phone check.

The optional Windows tests `python -m tests.e2e_indicator` and `python tests/e2e_native_typing.py` briefly open test-only desktop windows. The native typing test can temporarily take keyboard focus, then closes its window and restores the previous focus. These windows never appear during normal app use.

The browser E2E script starts and stops its own demo server and headless browser. It checks both image inputs, repeat uploads, direct connection, reconnect/reload, validation, and mobile layouts. It tries installed Edge, then Chrome, then Playwright Chromium. If none is available, install one with `.\.venv\Scripts\python -m playwright install chromium`. This test uses a fixed demo answer rather than live providers.

## Hosting at manu.wiphk.com

The phone page is deployed as the Cloudflare Worker `accessible-ai-manu`. Its static page can stay online when your PC is off. Image processing and typing require the desktop app and its connector to be running on the PC. The `/ws` Worker route forwards to that connector. It has no access control, so anyone who reaches `manu.wiphk.com` can use the connected desktop app and see its current answer.

The available tokens were denied named Tunnel creation. This version therefore uses a temporary Cloudflare Quick Tunnel and refreshes the Worker's upstream when hosting starts. Quick Tunnels have changing addresses and no uptime guarantee; this is prototype hosting. A stable named Tunnel is the follow-up once an appropriate Tunnel Edit permission is available. R2 is not used.

Start hosting from a normal PowerShell window at the repository root:

```powershell
npm ci --prefix deploy
python scripts/host_cloudflare.py
```

The launcher uses Claude Opus 5.5, Codex GPT-6 Luna for review, and Codex GPT-6 Sol for fallback. Pass `--model`, `--review-model`, or `--fallback-model` to override them. Restart the launcher after updating the app; an already-running process keeps its previous code.

If using the virtual environment from setup, substitute `.\.venv\Scripts\python` for `python`. The launcher requires `CLOUDFLARE_API_TOKEN` in its environment with Workers and DNS permissions. Never paste credentials into source, arguments, logs, or chat. It checks that the hostname is unused or already belongs to this Worker; it refuses to replace another service. It downloads the official Windows cloudflared release into ignored `.runtime/` and verifies the published SHA-256 digest on first installation.

Hosting starts the desktop app on loopback port 8765, waits for the correct app page, starts cloudflared, waits for a registered connection and a successful public origin probe, then deploys the Worker with the current origin. A failed probe stops startup instead of reporting success. Open `https://manu.wiphk.com/` on your phone. API credentials are removed from the desktop/connector child environments. The launcher keeps monitoring process liveness; it does not guarantee continuous connector health or uptime.

If Windows cannot resolve a newly generated connector hostname, the health probe retries through Cloudflare DNS-over-HTTPS and connects to a public IPv4 address while preserving TLS certificate verification, the original server name, and the HTTP Host header. This fallback applies only to the generated HTTPS `trycloudflare.com` origin, not arbitrary hosts or certificate failures. No system DNS settings are changed. Timeout messages now include the last HTTP/DNS/app-page error; a registered tunnel is no longer reported generically as a blocked port. The DNS query format follows the [Cloudflare DNS-over-HTTPS documentation](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/).

Keep the launcher and PC running. Ctrl+C stops its local child processes, including their process trees on Windows. It leaves the static Worker deployed. On the next run, a new connector is generated, and the Worker is updated. A shutdown or interrupted agent session can stop the local backend even while the static page remains available.

The launcher **changes the live Worker on each start**, including when invoked with `--demo`. Do not use it as an unattended overnight test. Run the following demo-only proxy check instead; it creates a temporary connector and a local Worker, never calls either AI provider, and does not deploy or change the production Worker:

```powershell
python tests/e2e_proxy.py
node --test deploy/worker.test.mjs
```

The proxy test still needs working outbound connectivity, DNS, cloudflared, Node.js, and the installed deployment dependencies. Its setup failure is not a passing browser test. Public deployed acceptance is separate: `python tests/e2e_hosted.py` submits a **real AI image request**. Run it only when provider usage is authorized. `--host-ip` can use an independently verified Cloudflare address for diagnostics without disabling certificate validation; success with that option does not prove ordinary DNS works.
