# StreamVault

A self-hosted online video downloader with a responsive dashboard, live download queue, quality selection, audio extraction, search, dark mode, local video thumbnails, and an in-app media player. Built with FastAPI, yt-dlp, FFmpeg, and a lightweight browser interface.

![Media studio dashboard with generated test videos](reports/studio-preview.png)

## Windows quick start

Install Python 3.12, FFmpeg, and Deno as described below. Extract the complete project ZIP, then double-click **Start StreamVault.bat** in the folder containing this README. On first launch it prepares the Python environment if needed; if a working `.venv` is already in the parent folder, it reuses it. The dashboard opens when ready. Keep the console window open, and press **Ctrl+C** to stop. If port 8000 is already occupied by an older copy, stop that copy first.

To update an existing installation, stop the app, download the latest main-branch ZIP from GitHub, and copy its project files into the existing folder. Keep your **data/** folder, **.venv/** folder and any **.env** settings. The ZIP does not include these local files. Existing dependencies are retained by the launcher; run `python -m pip install --upgrade "yt-dlp[default,curl-cffi]"` with your virtual environment's Python if source-site changes require an extractor update. A previous version kept history only in memory; only downloads created with the persistent-history version are recoverable from its database.

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

Open `http://127.0.0.1:8000` on your computer. Paste a public video URL, analyze it, choose an output format and maximum resolution, and add it to the queue. After processing, click **Play** to watch or listen inside the dashboard, or **Save original** to download to your device. The library displays local thumbnails for completed videos. The player supports native controls, seeking, volume and fullscreen where the browser provides them; Escape or the close button stops playback.

Supported URL families: YouTube, Dailymotion, Facebook, TikTok, Instagram, Threads, and X/Twitter. Actual extraction depends on yt-dlp and the source. Login-required, private, DRM-protected, geographic, age-restricted, and live content are not guaranteed. There is no DRM or authentication bypass. Use content you own or have permission to download.

Output formats: **MP4, WebM, MKV, MP3, M4A, WAV**. The video quality setting is a maximum, not an upscaler. When only a higher-resolution source exists, the app downloads it and scales it down to the selected maximum. Sources with missing resolution metadata are probed and scaled down after download when necessary. Video-only sources can be saved; the history row labels files with no audio track. MP4 works on most devices; conversion to some containers can be slow. Audio quality is configured at 192 kbps where applicable.

## Bulk download and playback

Open **Bulk download** below the main link box. Paste up to 50 individual video URLs, one per line; select a format, maximum quality and **YouTube transfers** (1 gentler, or 2 parallel), then choose **Queue all videos**. Exact duplicate links within the batch are removed. Batch validation, capacity checks and history persistence are atomic: invalid or oversized batches add no jobs. Use the separate YouTube playlist panel for playlist URLs; channel URLs are not supported. The current workspace's YouTube transfer limit changes with this selection and resets to one on restart. Source HTTP 429 and access restrictions still apply.

Download stages distinguish fetching, transfer, merging, conversion, resizing and verification. **Ready to save** counts finished originals, independently of optional previews. Press **Play** to try original playback, with compatible preview preparation and progress when needed. Closing the player stops playback/polling; an already requested preview can continue in its separate worker. A preview failure never deletes the original. On restart, completed originals remain available and interrupted preview tasks can be requested again.

Validation details: [reports/bulk-preview-verification.md](reports/bulk-preview-verification.md).

## YouTube playlists

Open **YouTube playlist** below Bulk download, paste a playlist URL such as `https://www.youtube.com/playlist?list=PLAYLIST_ID`, and choose **Load playlist**. A watch URL containing `list=` also works; pasting it in the main video box opens the playlist panel automatically. Review the title, available videos, skipped/hidden entry counts and selection. Choose format, quality and one or two YouTube transfers, then **Queue entire playlist** or queue only selected videos. MP3 and the existing video/audio formats are supported.

Up to **1,000 playlist entries** can be loaded per playlist. Larger lists are rejected with a clear message instead of silently downloading only the first portion; split them into smaller saved playlists. Dynamic YouTube Mix/radio lists and channels are excluded. Exact duplicate video IDs, known private/deleted entries, live/upcoming videos and invalid entries are skipped. If YouTube reports additional entries that it does not list, their count is shown; availability can still change between listing and downloading. Playlist page errors prevent offering a partial import. Source authentication and rate limits still apply.

Each selected video is an independent job, retaining the playlist title and original position. One failure does not stop the remaining jobs; inspect **My library**, **Play**, **Save original**, or retry individual failures. Media is saved on the machine running StreamVault under `data/<job-id>/`; browser saving remains individual, not a combined ZIP. Download concurrency stays bounded at two, regardless of playlist length. Playlist analysis lists metadata without downloading each video's media.

Preview tokens last thirty minutes, are bounded to ten cached previews, and reset on app restart. Repeated submission of the same preview returns the existing batch rather than duplicating it. Load the playlist again to make a new import. Queued job metadata is persisted atomically; after a restart, interrupted jobs use the existing per-video Retry/recovery behavior rather than automatically restarting a whole playlist.

Validation: [reports/playlist-verification.md](reports/playlist-verification.md).

If the playlist panel shows plain **Not Found**, the running server may still have the old Python routes while serving newly copied UI files. Stop the original server console with **Ctrl+C**, launch **Start StreamVault.bat** from the updated folder, and refresh with **Ctrl+F5**. The launcher now refuses to silently reuse a running server whose version differs from the installed files. A password-protected server cannot expose its version before login; the launcher prints restart guidance instead. The dashboard also flags a UI/server version mismatch, and a missing playlist API gives actionable restart/update instructions. Check the running version in PowerShell with `(Invoke-RestMethod http://127.0.0.1:8000/api/health).version` (owner authentication is required when enabled). See [reports/playlist-not-found.md](reports/playlist-not-found.md).

## Server operation

Run the same app on a server with Python, FFmpeg, and a supported JavaScript runtime. A single-owner HTTP Basic login protects all routes when **STREAMVAULT_PASSWORD** is set; **STREAMVAULT_USERNAME** defaults to `admin`. Public mode (`STREAMVAULT_PUBLIC=true`) rejects requests until a password is configured. Always put online instances behind **HTTPS**, because Basic authentication depends on transport encryption. The app is a shared owner workspace, not a multi-tenant service. Add proxy request limits and enforce outbound network filtering (block private/link-local addresses), CPU/time limits, and disk quotas before Internet deployment. URL validation allows supported HTTPS site names, but extractor-generated redirects and media requests also need network-level filtering. Browser mutations are restricted to the current origin, and repeated failed logins are throttled.

`DOWNLOAD_DIR` optionally changes storage (default `data/`). Two download workers run concurrently. Ordinary single/bulk submissions retain a fifty-job active/queued admission limit; playlist imports can fill up to 1,000 active/queued slots. History holds up to 1,100 entries, so existing downloads can reduce available playlist capacity. Larger batches are rejected atomically; no jobs are silently dropped. A 2 GiB source-file limit is applied when yt-dlp can determine the size; enforce a filesystem quota for a hard limit. New downloads are refused by the worker when less than 256 MiB is free. Completed files remain until removed. Removing a history entry deletes its file. Video posters and compatible browser previews are stored in each job's **_preview/** directory. The player first tries original MP4/WebM files directly, including AV1/Opus where the browser supports them. Unsupported files fall back to an on-demand H.264/AAC, YUV420p MP4 preview up to 720p, handled by a separate single preview worker. Use **Use compatible preview** if original playback is choppy. Originals become completed and downloadable before this optional encoding; preview work does not occupy download workers. Preview encoding uses two CPU threads, reports progress, and has a thirty-minute time limit. Stopping the app terminates active preview child processes. Preview preparation can add processing time and disk usage. A preview failure does not discard a successful original download; the dashboard offers Save original instead. Older completed jobs can prepare posters/previews on first access. A source thumbnail is shown during analysis when available, with a visual fallback when unavailable. Metadata is saved in **data/history.sqlite3** and survives restarts. Interrupted jobs become failed and can be restarted with **Retry**. For an interrupted merge/finalization, Retry first checks for a valid full-duration merged file and can finish it without re-downloading. Separate stream components and truncated files are excluded. Other retries start a clean attempt and remove stale partial media/previews from that job. Missing original files are marked failed with a Retry option when history is refreshed. Empty or unrecognizable media is not marked completed; both FFmpeg and FFprobe are required. Back up the whole data directory while the app is stopped to retain both history and media. Use a **single Uvicorn worker**; workers share disk metadata but do not share the active queue. Active downloads cannot currently be cancelled.

The `curl-cffi` dependency enables source handlers that require browser-style HTTP requests. It is not a UI browser test. On a managed network whose trusted proxy CA is already installed in the operating system, `STREAMVAULT_SYSTEM_CERTS=1` selects that trust store for yt-dlp. Supply the approved CA bundle using `CURL_CA_BUNDLE` when needed. Certificate verification remains enabled; do not use this setting to trust an unknown certificate. Ordinary laptop networks normally need no override.

No external API keys are required. Source websites and media CDN destinations must be reachable. Website changes can require updating yt-dlp (`pip install --upgrade "yt-dlp[default,curl-cffi]"`); run the tests after updates. Fonts load from Google Fonts with a local sans-serif fallback.

If analysis succeeds but download returns HTTP 403, the media server rejected access. Check terminal warnings, ensure a supported JavaScript runtime and EJS package are installed, update `yt-dlp[default,curl-cffi]`, restart the app, and retry a public video. A 403 alone does not establish which dependency or access restriction caused it, and these steps do not guarantee access to a restricted source. `/api/health` reports detected JavaScript runtimes and EJS package presence. Extractor warnings are visible in the server terminal.

## YouTube HTTP 429 and bot verification

A YouTube `HTTP 429` webpage warning can be recoverable using yt-dlp's normal fallback. StreamVault records warnings without raising from the logger. A successful extraction is accepted without cooldown. StreamVault defaults to one YouTube transfer, allows at most two when selected in Bulk download, spaces extractor requests by one second, and stops new YouTube source requests for ten minutes only when extraction ultimately fails with a rate limit, or a bot challenge following a 429 warning. Analysis and queued/retried downloads check the cooldown; blocked attempts return a clear message and `Retry-After` rather than contacting YouTube again. The browser displays a live Retry-After countdown and tells you when the local wait expires, without automatically retrying the source. Other platforms remain available. The cooldown applies to the running process; it does not establish when YouTube will lift its own restriction. Browser verification and authenticated sessions may still be required afterwards. Repeated retries can prolong source restrictions.

`Sign in to confirm you're not a bot` is reported separately from private/age-restricted access. A missing Visitor Data / PO-token warning following HTTP 429 does not mean an arbitrary Visitor Data value will fix the problem. Do not invent tokens or assume an extractor update bypasses verification. Use the official yt-dlp documentation for current source requirements.

For an authorized YouTube session on your **own laptop**, the optional `STREAMVAULT_COOKIE_FILE` environment variable points to a locally exported Netscape-format cookie file. It is off by default, applies only to YouTube, and is rejected in `STREAMVAULT_PUBLIC=true` mode. The application has no cookie-upload endpoint and does not include cookie contents or paths in its health response. Cookie files represent access to your account: keep them local, do not upload them to chat/GitHub, and do not enable this option on a shared server. yt-dlp may update that local cookie file during use.

Official export guidance: https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies and https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp. Once a valid cookie file exists locally, stop the app and launch from PowerShell:

```powershell
$env:STREAMVAULT_COOKIE_FILE = "$env:LOCALAPPDATA\StreamVault\youtube-cookies.txt"
& ".\Start StreamVault.bat"
```

Use your actual local file location. To disable session access, stop the app, run `Remove-Item Env:STREAMVAULT_COOKIE_FILE -ErrorAction SilentlyContinue`, and restart. Cookies do not guarantee downloads, bypass DRM, restore deleted media, or grant access your account does not have. No private/login session has been tested in the cloud.

Browser reliability fixes and current live sample checks are documented in [reports/browser-review.md](reports/browser-review.md). Analysis results are invalidated when the link changes, history polling is serialized, and connection/time-out/gateway errors have readable messages.

## Development and validation

```bash
python -m pytest -q
node --check app/static/app.js
```

Tests cover URL rejection, API responses, format normalization, actual yt-dlp downloads of a generated local video, FFmpeg audio conversion, file delivery, deletion, history restoration, retry, low-disk errors, login protection, and cross-site request rejection. Local fixtures do not prove current access to every external platform. A user-confirmed Windows YouTube download played correctly with both picture and audio; that confirms that video on that computer, not every platform or source.

API docs are available at `/docs`. Main routes: `/api/analyze`, `/api/download`, `/api/jobs`, `/api/jobs/{id}/file`, `/api/jobs/{id}/retry`, `/api/jobs/{id}/thumbnail`, `/api/jobs/{id}/stream`, `/api/jobs/{id}/preview` (POST to prepare, GET to poll), `/api/download/bulk`, `/api/playlist/analyze`, `/api/playlist/download`, and `/api/health`. Streaming supports HTTP byte ranges for seeking and remains protected by the same owner login. Error messages deliberately avoid exposing extractor details or credentials.

## Docker alternative

```bash
docker compose up --build -d
```

The compose file binds to localhost and retains media in a named volume. FFmpeg and Node.js are bundled in the image. `docker compose down` stops the app; add `-v` only if you intend to delete saved media. Both Compose templates pass configuration validation, the image builds successfully, all 129 tests pass inside the image as the unprivileged app user, and container startup/dashboard readiness checks pass. The managed cloud build used the configured proxy route and a trusted CA bundle; TLS and package-signature verification stayed enabled. On ordinary networks, no custom CA configuration is needed. Managed TLS proxies can supply a trusted CA bundle with `docker build --secret id=build_ca,src=/path/to/trusted-ca-bundle.pem .`; the bundle is mounted only for build-time package downloads, not copied into the image. Online domain, HTTPS certificate issuance, and hosted source access still require validation on the actual server.

## Future online hosting

The optional **compose.online.yaml** template runs the app behind Caddy with automatic HTTPS and password protection. It needs a Linux server with Docker Compose, a real domain pointing to the server, and ports 80/443 reachable. It has not been deployed to a server during this task.

1. Copy `.env.example` to `.env`. Set `STREAMVAULT_DOMAIN`, your username, and a strong unique `STREAMVAULT_PASSWORD` locally on the server. Never commit `.env` or send credentials in chat.
2. Apply the outbound network restrictions, request limits and disk quotas described above for your hosting environment.
3. Run `docker compose -f compose.online.yaml up --build -d`.
4. Visit your HTTPS domain and sign in through the browser prompt. Verify analysis, an authorized test download, file delivery and history after a restart.

The app port is exposed only to the internal Docker network. This template trusts proxy headers from that internal network; do not publish the backend port directly. HTTPS certificates and live downloads require external connectivity. Hosting configuration is a template, not evidence of a published online website.

## Threads videos

Threads direct post URLs (`https://www.threads.com/@username/post/POST_ID`) and share URLs (`https://www.threads.com/share/SHARE_ID/`) are supported by the app's own extractor. It reads the public server-rendered post data and supports a single video in the post, its carousel, or its quoted attachment. It uses the public crawler page representation without account cookies or login tokens. It does not select unrelated recommended videos. Posts containing multiple videos are rejected with a specific message; profile pages and login-only posts are not supported. Source page changes can require an integration update. See [reports/threads-check.json](reports/threads-check.json) for the supplied share-link download test.

## Platform and browser verification

| Platform | Current evidence |
| --- | --- |
| YouTube | One Windows download was confirmed by the user with working picture and audio; other links can fail for source-specific reasons. |
| TikTok, Facebook, X/Twitter | Public samples completed the actual app analysis and MP4 worker pipeline; FFprobe confirmed video and audio. |
| Instagram | A public Reel completed the app pipeline; the fetched source file contained video without an audio track. |
| Dailymotion | Two public samples failed: metadata loaded, but media manifest requests returned HTTP 403. Working downloads remain unverified here. |
| Threads | A custom public-page handler resolves share links and extracts direct or quoted single videos. The supplied share-link sample completed the actual MP4 worker with audio at 720p. |

Detailed public sample URLs, byte counts and stream checks are recorded in [reports/verification.md](reports/verification.md) and the adjacent JSON reports. These are sample results, not promises for every video.

The URL allowlist currently covers the named platform families above; this app does not accept every website worldwide. Adding a platform requires an actual extractor/integration, public sample URLs and download/conversion checks, as well as updating the allowed domains. Private access, age verification, DRM, regional restrictions, removed videos, rate limits and media-server HTTP 403 errors can prevent extraction or transfer even when another video on the same platform works. Browser choice does not change the backend's source access.

Modern Chrome and Edge use Chromium, Firefox uses Gecko, and Safari uses WebKit. Chromium desktop/mobile dashboard checks have passed. Native Edge, Firefox, Safari and phone-browser testing remains separate; the cloud network blocked download of the Firefox/WebKit test engines. Theme persistence now tolerates browsers that disable localStorage, so unavailable storage does not stop the dashboard from starting. Internet Explorer and obsolete browser versions are not supported.

For each platform/browser combination, analyze a public video, download MP4 at 720p, save the file, and play both picture and sound. Test search, Retry, and retained history after restart. Record the URL, browser/version, result and terminal warnings for any failure. An accepted domain or a platform tile alone is not evidence of a successful download.
