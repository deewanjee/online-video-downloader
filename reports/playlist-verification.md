# YouTube playlist import (1.3.0)

Adds a dedicated playlist list/selection/queue flow without changing single-video extraction into an implicit playlist transfer. A video URL with `list=` entered in the main form opens the playlist panel. The server canonicalizes the playlist URL, uses yt-dlp flat metadata listing, and consumes lazy pages while the downloader and YouTube request gate are still active.

The list has a 1,000-entry bound; larger lists are rejected explicitly. Known private/deleted, invalid and live/upcoming entries are excluded, duplicate IDs are removed, and counts of skipped or source-unlisted entries are visible. Logged playlist page errors prevent an incomplete preview from being offered as a complete import. Ordinary source cooldown and optional local authentication are retained. Mix/radio lists and channels are not playlist imports.

A bounded thirty-minute preview token holds the canonical entries. Queue requests can select only IDs from that preview; omitted selection means all available entries. Repeated token submission returns the existing batch. Atomic batch persistence happens before worker submission. Playlist jobs retain source order/position and title. Up to two download workers run; failures remain per video. Ordinary bulk submissions stay limited to 50 links/admission slots; playlists can fill 1,000 active/queued slots with a shared history capacity of 1,100. Existing jobs reduce available space. Interrupted jobs retain the existing individual Retry/recovery behavior.

## Checks

- **117 backend tests passed locally and in the fresh Docker image as the unprivileged app user with container networking disabled**, including playlist URL normalization, lazy pagination under the source gate, skip/deduplication accounting, a 60-video import beyond the ordinary bulk limit, selection/order, independent failures, idempotent resubmission, expiry, capacity/storage rollback, oversize handling, empty/private lists, page-error handling and rate-limit cooldown.
- Chromium exercised the real dashboard and FastAPI queue using a local playlist/source adapter and real AV1/Opus media. It verified main-input routing, list rendering and literal unsafe title text, selection and select-all, a selected batch followed by an entire playlist, per-video failure without blocking remaining jobs, native playback, Save original and stale-input invalidation. No JavaScript errors occurred.
- JavaScript syntax and Git whitespace checks passed.
- A live request to the single-video playlist sample included in yt-dlp's upstream extractor tests (`PLt5yu3-wZAlSLRHmI1qNm0wjyVNWw1pCU`) could not reach YouTube: the cloud proxy rejected the tunnel with **HTTP 403**. No live YouTube playlist transfer is claimed. Windows laptop source access remains to be checked after updating.

The browser source adapter does not prove access to private, removed, region-restricted or rate-limited videos. Files are saved individually in the server's data directory and exposed through the existing authenticated file/player endpoints; this feature does not create a playlist ZIP.
