# Laptop/browser reliability review

The review fixes confirmed application failures while retaining source access restrictions. This remains a browser dashboard backed by the local Python/FFmpeg server; no Android/Termux setup is required for laptop use. It does not guarantee downloading private, removed, DRM-protected, region-restricted or bot-blocked videos.

| Failure | Result after the fix |
| --- | --- |
| Link edited while analysis is pending | Old browser requests are cancelled and stale responses cannot select the wrong video. |
| Repeated polls on a slow connection | History refreshes run serially instead of overlapping and overwriting newer state. |
| Gateway returns HTML, network disconnects, or request hangs | Readable HTTP/connection/timeout messages replace raw JSON errors and unbounded waiting. |
| Source only provides a resolution above the selected maximum | Download the available source and downscale; tested with a known 480p source capped at 360p. |
| Executor/setup fails before work begins | Persist a failed, retriable job instead of leaving it queued indefinitely. |
| History write fails | Return a controlled 503 without publishing a new queued job or changing an existing failed job. |
| Retry follows an interrupted/failed attempt | Clear stale completion metadata, partial files and generated previews; finish failure cleanup before exposing Retry. |
| Transfer produces empty/unrecognizable media | Fail verification instead of marking the output complete. Missing FFprobe is reported before queueing. |
| An original file disappears | History marks it failed and offers Retry; file delivery is confined to its job directory. |
| H.264 video uses a browser-incompatible 4:4:4 pixel format | Build a YUV420p preview, keep the original; actual Chromium playback and seeking passed. |
| One old file needs a slow preview | Per-job locks allow unrelated files' thumbnail/stream work to proceed. |
| Optional preview fails | The original remains completed and downloadable, with a visible preview-unavailable notice. |

67 backend tests passed locally and in the fresh Docker image as the unprivileged user. Tests include all six real output-format conversions, range requests, persistence/retry, authentication, cooldown handling, media verification, storage/worker failures, stale retry cleanup and concurrent thumbnail requests. A Starlette test-client deprecation warning remains; it does not fail the suite.

Chromium browser checks use an actual FastAPI server with generated local media. They verify real H.264 4:4:4 preview playback, seeking, player cleanup, stale analysis cancellation, correct new-link selection, HTML gateway failures, visible preview failure notices, original-file saving, search, dark theme and non-overlapping polling, with no JavaScript errors. Native Windows execution, native Edge, Firefox and Safari were not exercised by these cloud checks.

Public Threads and TikTok samples also completed actual app analysis and MP4 downloads with audio at 720p. Each returned a JPEG thumbnail (200) and a byte-range video stream (206). See [browser-review-live-check.json](browser-review-live-check.json). Downloaded test media was discarded. These samples do not establish access to every video on either platform.

The pinned yt-dlp version (2026.8.19) was checked against PyPI and was the latest stable version returned during this review. No account cookies were imported and no authenticated YouTube download was verified. Existing HTTP 429 cooldowns and optional local-session configuration remain in place; no restriction bypass is claimed.
