# StreamVault

A self-hosted online video downloader with a responsive dashboard, live download queue, quality selection, audio extraction, search, and dark mode. Built with FastAPI, yt-dlp, FFmpeg, and a lightweight browser interface.

## Windows quick start

Install Python 3.12, FFmpeg, and Deno as described below. Extract the complete project ZIP, then double-click **Start StreamVault.bat** in the folder containing this README. On first launch it prepares the Python environment if needed; if a working `.venv` is already in the parent folder, it reuses it. The dashboard opens when ready. Keep the console window open, and press **Ctrl+C** to stop. If port 8000 is already occupied by an older copy, stop that copy first.

To update an existing installation, stop the app, download the latest main-branch ZIP from GitHub, and copy its project files into the existing folder. Keep your **data/** folder, **.venv/** folder and any **.env** settings. The ZIP does not include these local files. Existing dependencies are retained by the launcher; run `python -m pip install --upgrade "yt-dlp[default]"` with your virtual environment's Python if source-site changes require an extractor update. A previous version kept history only in memory; only downloads created with the persistent-history version are recoverable from its database.

## Run on your computer

Requires **Python 3.12+**, **FFmpeg on PATH**, and **Node.js 22+ or Deno 2.3+** for full YouTube support. Install FFmpeg with your OS package manager (for example `brew install ffmpeg` on macOS or `winget install Gyan.FFmpeg` on Windows). The app enables detected Node and Deno runtimes; its dependency lock includes yt-dlp's companion EJS scripts.

On Windows, install the JavaScript runtime with `winget install --id DenoLand.Deno -e --source winget`. Close and reopen PowerShell afterward, then verify `deno --version`. Node.js LTS is also supported. Official runtime/dependency guidance: https://github.com/yt-dlp/yt-dlp/wiki/EJS.

```bash
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.lock
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` on your computer. Paste a public video URL, analyze it, choose an output format and maximum resolution, and add it to the queue. After conversion, click **Save file** to download to your device.

Supported URL families: YouTube, Dailymotion, Facebook, TikTok, Instagram, Threads, and X/Twitter. Actual extraction depends on yt-dlp and the source. Login-required, private, DRM-protected, geographic, age-restricted, and live content are not guaranteed. There is no DRM or authentication bypass. Use content you own or have permission to download.

Output formats: **MP4, WebM, MKV, MP3, M4A, WAV**. The video quality setting is a maximum, not an upscaler. MP4 works on most devices; conversion to some containers can be slow. Audio quality is configured at 192 kbps where applicable.

## Server operation

Run the same app on a server with Python, FFmpeg, and a supported JavaScript runtime. A single-owner HTTP Basic login protects all routes when **STREAMVAULT_PASSWORD** is set; **STREAMVAULT_USERNAME** defaults to `admin`. Public mode (`STREAMVAULT_PUBLIC=true`) rejects requests until a password is configured. Always put online instances behind **HTTPS**, because Basic authentication depends on transport encryption. The app is a shared owner workspace, not a multi-tenant service. Add proxy request limits and enforce outbound network filtering (block private/link-local addresses), CPU/time limits, and disk quotas before Internet deployment. URL validation allows supported HTTPS site names, but extractor-generated redirects and media requests also need network-level filtering. Browser mutations are restricted to the current origin, and repeated failed logins are throttled.

`DOWNLOAD_DIR` optionally changes storage (default `data/`). Two downloads run concurrently, with up to ten active/queued jobs and one hundred history entries. A 2 GiB source-file limit is applied when yt-dlp can determine the size; enforce a filesystem quota for a hard limit. New downloads are refused by the worker when less than 256 MiB is free. Completed files remain until removed. Removing a history entry deletes its file. Metadata is saved in **data/history.sqlite3** and survives restarts. Interrupted jobs become failed and can be restarted with **Retry**. Back up the whole data directory while the app is stopped to retain both history and media. Use a **single Uvicorn worker**; workers share disk metadata but do not share the active queue. Active downloads cannot currently be cancelled.

No external API keys are required. Source websites and media CDN destinations must be reachable. Website changes can require updating yt-dlp (`pip install --upgrade "yt-dlp[default]"`); run the tests after updates. Fonts load from Google Fonts with a local sans-serif fallback.

If analysis succeeds but download returns HTTP 403, the media server rejected access. Check terminal warnings, ensure a supported JavaScript runtime and EJS package are installed, update `yt-dlp[default]`, restart the app, and retry a public video. A 403 alone does not establish which dependency or access restriction caused it, and these steps do not guarantee access to a restricted source. `/api/health` reports detected JavaScript runtimes and EJS package presence. Extractor warnings are visible in the server terminal.

## Development and validation

```bash
python -m pytest -q
node --check app/static/app.js
```

Tests cover URL rejection, API responses, format normalization, actual yt-dlp downloads of a generated local video, FFmpeg audio conversion, file delivery, deletion, history restoration, retry, low-disk errors, login protection, and cross-site request rejection. Local fixtures do not prove current access to every external platform. A user-confirmed Windows YouTube download played correctly with both picture and audio; that confirms that video on that computer, not every platform or source.

API docs are available at `/docs`. Main routes: `/api/analyze`, `/api/download`, `/api/jobs`, `/api/jobs/{id}/file`, `/api/jobs/{id}/retry`, and `/api/health`. Error messages deliberately avoid exposing extractor details or credentials.

## Docker alternative

```bash
docker compose up --build -d
```

The compose file binds to localhost and retains media in a named volume. FFmpeg and Node.js are bundled in the image. `docker compose down` stops the app; add `-v` only if you intend to delete saved media. Both Compose templates pass configuration validation, the image builds successfully, all 24 tests pass inside the image as the unprivileged app user, and container startup/dashboard readiness checks pass. The managed cloud build used the configured proxy route and a trusted CA bundle; TLS and package-signature verification stayed enabled. On ordinary networks, no custom CA configuration is needed. Managed TLS proxies can supply a trusted CA bundle with `docker build --secret id=build_ca,src=/path/to/trusted-ca-bundle.pem .`; the bundle is mounted only for build-time package downloads, not copied into the image. Online domain, HTTPS certificate issuance, and hosted source access still require validation on the actual server.

## Future online hosting

The optional **compose.online.yaml** template runs the app behind Caddy with automatic HTTPS and password protection. It needs a Linux server with Docker Compose, a real domain pointing to the server, and ports 80/443 reachable. It has not been deployed to a server during this task.

1. Copy `.env.example` to `.env`. Set `STREAMVAULT_DOMAIN`, your username, and a strong unique `STREAMVAULT_PASSWORD` locally on the server. Never commit `.env` or send credentials in chat.
2. Apply the outbound network restrictions, request limits and disk quotas described above for your hosting environment.
3. Run `docker compose -f compose.online.yaml up --build -d`.
4. Visit your HTTPS domain and sign in through the browser prompt. Verify analysis, an authorized test download, file delivery and history after a restart.

The app port is exposed only to the internal Docker network. This template trusts proxy headers from that internal network; do not publish the backend port directly. HTTPS certificates and live downloads require external connectivity. Hosting configuration is a template, not evidence of a published online website.
