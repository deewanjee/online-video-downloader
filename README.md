# StreamVault

A self-hosted online video downloader with a responsive dashboard, live download queue, quality selection, audio extraction, search, and dark mode. Built with FastAPI, yt-dlp, FFmpeg, and a lightweight browser interface.

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

Run the same app on a server with Python and FFmpeg. For a private online instance, bind behind an **authenticated HTTPS reverse proxy**. The app intentionally defaults to localhost in these instructions and is **not a hardened public multi-user SaaS**. Do not expose it anonymously: it has no built-in user authentication or tenant isolation. Add proxy rate limits and enforce outbound network filtering (block private/link-local addresses), CPU/time limits, and disk quotas before Internet deployment. URL validation allows supported HTTPS site names, but extractor-generated redirects and media requests also need network-level filtering.

`DOWNLOAD_DIR` optionally changes storage (default `data/`). Two downloads run concurrently, with up to ten active/queued jobs and one hundred history entries. A 2 GiB source-file limit is applied when yt-dlp can determine the size; enforce a filesystem quota for a hard limit. Completed files remain until removed. Removing a history entry deletes its file. The queue/history is in memory and resets on server restart; retained files in `data/` can be cleaned manually while the server is stopped. Use a **single Uvicorn worker**; multi-process workers do not share job state. Active downloads cannot currently be cancelled.

No external API keys are required. Source websites and media CDN destinations must be reachable. Website changes can require updating yt-dlp (`pip install --upgrade "yt-dlp[default]"`); run the tests after updates. Fonts load from Google Fonts with a local sans-serif fallback.

If analysis succeeds but download returns HTTP 403, the media server rejected access. Check terminal warnings, ensure a supported JavaScript runtime and EJS package are installed, update `yt-dlp[default]`, restart the app, and retry a public video. A 403 alone does not establish which dependency or access restriction caused it, and these steps do not guarantee access to a restricted source. `/api/health` reports detected JavaScript runtimes and EJS package presence. Extractor warnings are visible in the server terminal.

## Development and validation

```bash
python -m pytest -q
node --check app/static/app.js
```

Tests cover URL rejection, API responses, format normalization, actual yt-dlp downloads of a generated local video, FFmpeg audio conversion, file delivery, and deletion. Local fixtures do not prove current access to every external platform.

API docs are available at `/docs`. Main routes: `/api/analyze`, `/api/download`, `/api/jobs`, `/api/jobs/{id}/file`, and `/api/health`. Error messages deliberately avoid exposing extractor details or credentials.

## Docker alternative

```bash
docker compose up --build -d
```

The compose file binds to localhost and retains media in a named volume. FFmpeg and Node.js are bundled in the image. `docker compose down` stops the app; add `-v` only if you intend to delete saved media. Docker configuration is provided but requires Docker and has not been validated in this cloud machine.
