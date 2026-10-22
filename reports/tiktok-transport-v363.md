# TikTok creator-list browser request transport — 3.6.3

## Trigger and change

The user's Windows installation still received empty/non-JSON creator-list responses after the shared-session fix. The supplied video remained accessible. Thus session continuity alone did not resolve their environment, and the listing cannot be claimed fixed on that laptop.

The registered TikTokProfileIE now requests the exact creator/item_list JSON endpoint with yt-dlp's supported browser-compatible transport and require_impersonation=True. Profile-page extraction already supports this request mechanism upstream. Other endpoints/extractors are unchanged. The resolved public profile URL is supplied as Referer; pagination query arguments and the shared cookie jar are preserved.

curl_cffi is already included in the dependency lock and Windows dependency checks. An unavailable transport produces a specific repair instruction instead of silently retrying the plain transport. No additional retry loop, proxy service, credential collection, disabled certificate verification or private-access bypass was added. Private/unknown visibility labels remain source-based.

## Verification

- **348 Python tests passed**, one existing Starlette/httpx deprecation warning.
- Added tests confirm browser-transport/required-backend options and Referer only for the intended account API, preserving unrelated JSON request behavior; dependency errors are actionable rather than mislabelled private.
- The existing same-cookie-jar, invalid-JSON, privacy, selection, history and download/playback regressions pass.
- Chromium regression passed the full account/selection/filter/copy/history/fixture-save/playback and responsive workflow; no JavaScript errors.
- JavaScript syntax and git whitespace checks passed.
- A bounded live probe with the new transport and resolved profile Referer confirmed the supplied video owner and returned **16 unique account entries across multiple source pages**. No full media file was downloaded. The app's existing system-trust option was used for the managed cloud proxy, with TLS verification enabled.

The Windows/network empty response was not reproduced in this cloud. This is a concrete request-path compatibility change with a successful bounded live listing; it does not prove that transport differences were the sole cause on the user's computer or that the entire account will download. Retest on that laptop is still required. Source challenges, account API availability and restrictions can change.

A Docker image/container verification was not repeated for this revision after the cloud backing-filesystem limit in the previous release. Completed Python/browser/live checks above are the evidence for this revision.

## Update

Stop the old console/server, preserve data/, .env, .venv and media folders, update the code, then restart with Start StreamVault.bat and refresh with Ctrl+F5. Use the actual direct video link under TikTok → Find videos. A real API attempt now logs Requesting account page with supported browser transport. If it still returns empty JSON on that laptop, do not treat that as private-account evidence or repeat requests indefinitely.
