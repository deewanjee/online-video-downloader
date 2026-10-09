# Verification record

Public samples were chosen from the installed yt-dlp extractor examples. Media files were discarded after checking. Source availability can change; one passing sample does not establish universal platform support.

| Platform | Public sample | Actual result |
| --- | --- | --- |
| TikTok | https://www.tiktok.com/@patroxofficial/video/6742501081818877190 | App analyze + MP4 worker completed; video and audio; output height 720. |
| Facebook | https://www.facebook.com/watch/?v=647537299265662 | App analyze + MP4 worker completed; video and audio; output height 640. |
| X/Twitter | https://twitter.com/SouthamptonFC/status/1347577658079641604 | App analyze + MP4 worker completed; video and audio; output height 720. |
| Instagram | https://www.instagram.com/reel/Chunk8-jurw/ | App analyze + MP4 worker completed; video only, no audio track in the fetched file; output height 720. |
| YouTube | https://www.youtube.com/watch?v=YE7VzlLtp-4 | Cloud proxy rejected the connection with HTTP 403; no cloud download verified. User separately confirmed a Windows YouTube download with picture and audio. |
| Dailymotion | https://www.dailymotion.com/video/x89eyek and https://www.dailymotion.com/video/x5kesuj | Metadata available, but HLS requests returned HTTP 403; no working download verified. curl-cffi installed, latest pinned stable yt-dlp checked. |
| Threads | No public video sample verified | No dedicated Threads handler in the pinned yt-dlp version. Experimental; profile-page access is not a video download test. |

The initial Facebook and X examples failed; alternate public samples passed. See platform-check.json and platform-additional-check.json for both outcomes. app-platform-check.json records the real app worker results, including the final resolution cap. The Instagram samples returned video-only sources; the test does not establish whether the original website playback has audio.

Tests use the managed operating-system CA bundle with TLS verification enabled. Native URL validation still limits the app to the documented platform families. A backend source rejection is independent of dashboard browser compatibility.

Chromium desktop/mobile dashboard checks cover page readiness, disabled localStorage, theme toggle, history search, file saving, Retry UI, and retained history/file delivery after restarting the desktop launcher. Native Edge, Firefox, Safari and physical phones have not been verified. Firefox/WebKit test-engine downloads were rejected by cloud network policy; their download domains are saved in the cloud configuration draft, not applied to the running environment.

The automated backend suite covers six actual media output conversions, video-only sources with unknown resolution, persistent history and recovery, retries, access control, same-origin protection, storage limits and certificate verification. Local media fixtures test implementation, not external platform access.
