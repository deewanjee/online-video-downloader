# Playlist API Not Found (1.3.1)

The reported UI displays the FastAPI default `Not Found` response. The playlist routes exist in the current application; the supplied watch URL canonicalizes to `https://www.youtube.com/playlist?list=PLOpqmtbg1STVxAqdA-ndhhR4Q4U876aPx`. This symptom is consistent with an older running Python server serving newly copied static UI files, or a partial/wrong-folder update. Source failures from the current playlist handler produce specific 400/422/429 errors rather than that default route 404. The user's running process/version has not been inspected remotely.

The launcher previously detected only the StreamVault application name and silently reopened any existing version. It now checks the version from the running server against the installed package version and gives stop/restart instructions on mismatch. Protected health endpoints produce a separate warning because the version cannot be checked without owner login. No process is killed automatically. The dashboard reports UI/server version mismatch, missing playlist routes explain how to restart/update, and the main HTML response disables caching.

Validation:

- 129 backend tests pass locally and in the fresh Docker image as its unprivileged user, covering the exact supplied URL with mocked playlist metadata, health capability/version reporting, old/missing/matching launcher versions, protected servers, and real loopback health requests unaffected by system proxy configuration, in addition to existing download and playlist tests.
- Chromium reproduced an old-server `404 {"detail":"Not Found"}` response and confirmed the actionable message, stale-version badge, preserved input and enabled retry. Restoring the playlist API allowed the same UI to load the playlist fixture. No JavaScript errors occurred.
- JavaScript syntax and whitespace checks pass.

The browser check uses a local source adapter; the supplied playlist's live YouTube availability is not established by this check. The cloud proxy previously blocked live YouTube requests with HTTP 403. A server restart/update is required on the user's laptop to load changed Python routes.
