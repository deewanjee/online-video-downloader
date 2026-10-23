# Instagram accounts

Find and review Instagram video posts and reels from a username.

## Find an account

1. Open Platforms → Instagram. Paste an @username or an HTTPS instagram.com/username/ profile link and choose Find videos. No user ID or helper video is required.
2. Start with 30 posts. The portal reads the source profile, confirms its identity and requests the returned feed in a single session. Only Instagram-provided entries can be shown; an empty or blocked listing is not proof that no posts exist.
3. Review the account name, avatar, source post count and explicit public/private status when provided. Unknown privacy stays Not verified. Account privacy and individual video access are separate.

## Review and download

1. Single-video posts and reels are checked before selection. Photo and carousel/mixed posts are identified but cannot be queued as a single video in this importer.
2. Use filters and search to find available posts or previous downloads. Select available on page or all available matches; selections stay across pages in the current batch.
3. Choose format and maximum quality, then Download selected. Your existing download folder, queue, local playback, recovery and permanent activity log apply. Completed posts show Already downloaded. Active copies cannot be queued twice; previous completions require explicit Allow repeat downloads.
4. Equivalent /p/, /reel/ and /tv/ source links match the same retained duplicate record, including after library removal. Source preview uses Instagram’s official embed after a click; embedding can be disabled by the source.
5. Load next batch reads another source window. Stop cancels after the current request; checked entries remain reviewable. Check unverified again only retries unchecked entries. Previews expire after 30 minutes.

## If Instagram requires login

1. HTTP 401 means Instagram requires a login session for this request. HTTP 403, 429 and verification responses do not establish private status. Repeated submissions do not remove source limits.
2. Optional local desktop session: use an authorized Netscape-format Instagram cookie file stored on your own laptop. Never share passwords, cookies or session tokens in chat or upload them to GitHub.
3. Stop the portal. In PowerShell, use $env:AAASOFT_INSTAGRAM_COOKIE_FILE = "C:/Users/YourName/instagram-cookies.txt" with your actual local file path, then run the normal Windows launcher. The variable applies only to Instagram account checks and downloads. The portal does not provide a cookie-upload endpoint.
4. This option is rejected in STREAMVAULT_PUBLIC=true mode. Use an account whose permissions you are authorized to use. Private account enumeration remains unsupported. Sessions do not guarantee listing or downloading and may expire.
5. To disable the session, stop the portal, run Remove-Item Env:AAASOFT_INSTAGRAM_COOKIE_FILE -ErrorAction SilentlyContinue, then restart. Source restrictions, removed media, login challenges and endpoint changes can still prevent access.
