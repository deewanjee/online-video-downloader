# TikTok accounts and platform workspaces

List public account videos, review availability and download a selection.

## Choose a platform

1. Open Platform workspaces in the sidebar. Each supported platform has its own panel and single-video URL form. Per-platform counters retain successful download totals.
2. YouTube and Dailymotion offer links to their supported in-portal search. YouTube playlist tools remain available. Account import is currently implemented for TikTok only; other platform panels do not claim profile enumeration.
3. View completed videos opens the library with an exact source filter. You can switch All platforms or another source in the library.

## Load a TikTok account

1. Choose TikTok and enter @username, username, or https://www.tiktok.com/@username. Use the account username, not a display name or individual video URL.
2. If account-ID lookup fails, expand Optional: assist account lookup with a public video. Paste a direct public video URL from the same username. The video metadata must confirm that uploader and a supported public account ID; this does not bypass account access restrictions.
3. Choose Load & check account. Listing is metadata-only: it does not download media files. Availability checks run sequentially in the background; the progress indicator shows how many entries have been classified.
4. The list becomes visible while checks continue. Only Available - checked video entries are selectable; unknown, private, removed, live and non-video posts cannot be queued. Availability can change after checking.

## Review and select

1. Use caption/video-ID search, availability/history filters and pagination. Selections remain across pages within the current profile batch.
2. Select available on page applies to the visible page. Select all available matches includes all verified matching entries in the current batch. Clear selection starts over.
3. Already downloaded and In queue entries are protected. Retained lifetime history works after library removal. Explicit Allow repeat downloads permits another completed copy; active copies remain protected.
4. Choose a format and maximum quality, then Download selected. Confirm the download folder when requested. Queueing waits for account checks to finish or stop. A successful submission does not consume the entire profile preview: select another group from the same checked list later.

## Stop, retry and larger accounts

1. Stop requests cancellation after the current source request. Checked entries stay reviewable; unverified entries remain disabled. Check unverified again resumes checks for unknown entries without relisting the account.
2. The source is read in batches of up to 1,000 returned entries. Next profile batch requests the next source window; previous batches can be loaded again. Changing a batch clears that batch selection, after confirmation. The supported offset limit is 100,000 posts.
3. A preview remains available for 30 minutes after checking ends; app restart clears previews. Checks have a 30-minute run limit. One account metadata task runs at a time to limit source requests. TikTok HTTP 429 or explicit IP blocking pauses new account tasks for ten minutes; access is not guaranteed when that pause ends.
4. Retry status reconnects polling if the browser loses contact repeatedly. This does not create another download or account task.

## Understand limitations

1. TikTok may require authentication/verification or block its public account endpoint while individual public video downloads still work. A private/blocked account cannot be enumerated by this portal. No hidden/private videos are exposed.
2. Only entries actually returned by the source can be reviewed. An empty response is not proof that no public posts exist. The app does not promise that a source returns every historical post.
3. Public video previews use the official TikTok player after a click. Embedding depends on TikTok/browser permissions. Saved files use the existing local player. Source restrictions are not bypassed.
4. Use the individual TikTok video form when account listing is blocked. Account checks and downloads have separate progress; selected downloads appear in Work queue, Completed and the retained Activity log.
