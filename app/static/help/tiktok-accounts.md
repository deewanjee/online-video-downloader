# TikTok accounts and platform workspaces

List public account videos, review availability and download a selection.

## Choose a platform

1. Open Platform workspaces in the sidebar. Each supported platform has its own panel and single-video URL form. Per-platform counters retain successful download totals.
2. YouTube and Dailymotion offer links to their supported in-portal search. YouTube playlist tools remain available. Account import is currently implemented for TikTok only; other platform panels do not claim profile enumeration.
3. View completed videos opens the library with an exact source filter. You can switch All platforms or another source in the library.

## Load a TikTok account

1. Open Platform workspaces → TikTok. The three steps are Find account, Review videos and Download selection.
2. Use the single Account username or TikTok link input. Paste @username, a public profile URL, a direct public video URL, or a vt.tiktok.com / vm.tiktok.com share link. Choose Find videos. No account ID or user ID is required.
3. When given a video link, the source metadata identifies its owner automatically. The numeric uploader ID is not treated as a username. Missing or mismatching public identity is never guessed.
4. Profile lookup and listing are metadata-only. The review appears while sequential access checks continue; only checked Available video posts can be selected.
5. A confirmed account has a compact summary. Account details & copy options expands the public fields the source actually supplied. Missing fields and unconfirmed identities do not generate a large empty panel. Source counters do not guarantee that every historical post is downloadable.

## Understand public, private and access status

1. Account privacy is shown separately as Public account, Private account or Not verified. Public/private requires an explicit source flag or an unambiguous account-private response; confirming the account username alone does not prove its privacy.
2. Each returned video shows Public video, Private video, Access restricted, Accessible (privacy not provided), or Not verified. A usable media listing proves access at check time but does not establish a public privacy setting without source metadata.
3. Use Public, Private or Access restricted filters in the account review. Private/restricted entries remain disabled. Counts describe returned entries only; TikTok may hide private posts entirely.
4. Account listing errors, HTTP 403/429, network failures and sign-in prompts do not establish that an account is private. The supplied-video access line can be available while account privacy or the full listing remains unverified.
5. Single-video analysis shows the same privacy/access distinction. Explicit private/restricted metadata disables Add to queue; an unknown source response explains that privacy could not be verified.

## If the list cannot be loaded

1. The next-step panel distinguishes a source block from an unavailable account listing. A failure is not evidence that the account has zero videos.
2. Try a public video link changes the same input into the fallback path. Paste a real public post/share link; the system resolves its account automatically. It does not guarantee access to a blocked account API.
3. Open supplied TikTok link lets you inspect the requested public link in your browser. Download one video instead focuses the existing single-video form, prefilling a supplied public video/share link when possible.
4. The account-list API uses the supported browser request dependency within its shared session. If the backend is unavailable, stop and restart with the Windows launcher so locked dependencies can be checked. This transport does not grant private access or guarantee source responses.
5. An empty or non-JSON account API response is different from a private-account response. The video lookup and account list share a temporary source session. This can preserve source-issued cookies; it does not grant access to private posts or guarantee every listing response.
6. For HTTP 429 or explicit IP blocks, wait before retrying. Repeated clicks do not remove source restrictions. Private, removed, omitted and blocked posts are not guaranteed to list or download.

## Review and select

1. Use caption/video-ID search, availability/history filters and pagination. Selections remain across pages within the current profile batch.
2. Select available on page applies to the visible page. Select all available matches includes all verified matching entries in the current batch. Clear selection starts over.
3. Already downloaded and In queue entries are protected. Retained lifetime history works after library removal. Explicit Allow repeat downloads permits another completed copy; active copies remain protected.
4. Choose a format and maximum quality, then Download selected. Confirm the download folder when requested. Queueing waits for account checks to finish or stop. A successful submission does not consume the entire profile preview: select another group from the same checked list later.

## Stop, retry and larger accounts

1. Stop requests cancellation after the current source request. Checked entries stay reviewable; unverified entries remain disabled. Check unverified again resumes checks for unknown entries without relisting the account.
2. The source is read in batches of up to 1,000 returned entries. Next profile batch requests the next source window; previous batches can be loaded again. Changing a batch clears that batch selection, after confirmation. The supported offset limit is 100,000 posts.
3. A preview remains available for 30 minutes after checking ends; app restart clears previews. Checks have a 30-minute run limit. One account metadata task runs at a time to limit source requests. TikTok HTTP 429 or explicit IP blocking pauses new account tasks for ten minutes; access is not guaranteed when that pause ends.
4. Reconnect resumes polling if the browser loses contact repeatedly. This does not create another download or account task.

## Understand limitations

1. TikTok may require authentication/verification or block its public account endpoint while individual public video downloads still work. A private/blocked account cannot be enumerated by this portal. No hidden/private videos are exposed.
2. Only entries actually returned by the source can be reviewed. An empty response is not proof that no public posts exist. The app does not promise that a source returns every historical post.
3. Public video previews use the official TikTok player after a click. Embedding depends on TikTok/browser permissions. Saved files use the existing local player. Source restrictions are not bypassed.
4. Use the individual TikTok video form when account listing is blocked. Account checks and downloads have separate progress; selected downloads appear in Work queue, Completed and the retained Activity log.
