# TikTok video-to-account session continuity — 3.6.2

## Evidence and fix

The supplied Windows log separates two failures: profile/embed extraction cannot find a secondary user ID; a direct public video resolves that ID, but creator/item_list returns an empty response and JSONDecodeError. This is not evidence that the account or every post is private. The later Windows Proactor connection-reset callback is a separate connection-teardown error; it does not establish the cause of the empty account JSON.

The seed video and account listing previously used separate YoutubeDL instances, discarding source-issued in-memory cookies between them. They now run inside the same open downloader/session. The actual owner must still match before using its account ID. Seed/list format probing is disabled, avoiding the temporary media format test in the supplied log. Per-video checks and actual download workers retain their behavior. No credential import, private-access bypass, proxy rotation or disabled TLS verification was added.

A successful recovered seed attempt clears its stale logger-error flag before listing. Listing errors still reject partial previews. Empty/invalid JSON receives the invalid_response issue code and an explicit account API explanation. Failed stage and exception type are recorded without exposing raw source URLs or cookies. Certificate errors are classified before upstream footer text can be mistaken for sign-in instructions.

Session continuity is a concrete application fix, not proof that discarded cookies were the sole cause of the user's empty response. TikTok may still refuse or change a later listing response.

## Verification

- **345 Python tests passed**, one existing Starlette/httpx deprecation warning.
- Cookie regression verifies the same downloader and synthetic source cookie across seed/list requests, disabled seed format probing and successful listing after a recovered seed log message.
- Empty-JSON regression verifies account_listing as the failed stage, preserved accessible-video metadata, unknown account privacy, no partial preview and no source-secret leakage. Certificate classification is covered.
- Chromium workflow regression passed account lookup, privacy labels/filters, clipboard, selection/pagination/history, fixture downloads/saved playback and responsive checks; no JavaScript errors.
- JavaScript syntax and git whitespace checks passed.
- Docker packaging was attempted but could not finish because the cloud Docker backing filesystem filled. Regenerable build cache was cleared to recover space. A current-version Docker build/container test is not claimed; local Python and Chromium results above are completed.

## Live check

A bounded live metadata check of the supplied @asifcomputers01/video/7683830394185108754 confirmed its owner and retrieved **16 unique account entries across multiple source pages** in a shared session. No full video was downloaded by this probe. This establishes a working bounded live enumeration, not a complete historical inventory or sixteen completed media downloads.

The cloud proxy required the application's existing system-trust option; TLS verification stayed enabled. An earlier probe without that cloud trust configuration failed with a certificate error, not a demonstrated TikTok IP block. That environment-specific result differs from the user's empty JSON log. No repeated rate/IP-block retries were made.

## Update

Stop the running server, preserve data/, .env, .venv and media folders when replacing project code, restart and refresh with Ctrl+F5. Retry the supplied direct video link under TikTok → Find videos. If the source returns empty JSON again, it will be reported specifically; repeated attempts cannot guarantee access.
