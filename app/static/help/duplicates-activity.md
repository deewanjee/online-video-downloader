# Duplicate protection, Completed and activity log

Avoid repeated downloads and keep an exportable record of source links.

## Protection by default

1. Already queued/running source videos cannot be added twice, including through another search result, single link, batch or playlist. Short/tracked YouTube links are matched by source identity.
2. Previously completed videos are skipped by default even after removal from the library, using the retained lifetime record. Detection matches recognized source identity, not visual similarity or the same video reuploaded under a different ID/platform.
3. Different formats or quality choices for the same source also count as repeats. Enable Allow repeat downloads for this session when you intentionally need another format/copy. Completed-card Again asks for explicit confirmation; active copies stay protected.

## Completed archive

1. The dashboard Work queue and library Work queue automatically exclude completed videos. Open Completed from the sidebar or library tabs to play and manage saved files. All videos remains available.
2. Discover defaults to To download. Completed search results leave that tab and appear under Downloaded; these tabs cover the current search page. Completed library covers all retained saved entries.
3. Completed originals stay in their existing folder; switching tabs does not copy or move media files. Removing a library entry keeps lifetime counts and its activity log.

## Review a playlist

1. Every returned playlist row shows its current source availability plus Already downloaded or In queue where known. Previously downloaded/active entries are disabled and unchecked by default.
2. Enable Allow repeat downloads if you deliberately want to select completed entries again. Private/removed entries remain disabled. Live access checks still describe source access, not download history.
3. The backend checks history again when you submit, so a video completed after listing is still skipped. The queue response reports how many duplicates were excluded.

## Activity and exports

1. Open Activity log to search by source link, title or message and filter events. It records accepted queue work, status changes, completions, failures, duplicate prevention and library removal. The log stays when library entries are removed.
2. Export CSV or JSON for the full retained workspace log, including source URLs. CSV timestamps use UTC; the portal shows local browser time. Spreadsheet-like text in CSV is escaped to prevent formula execution.
3. Existing library records are imported once. Links deleted before this update cannot be reconstructed from older hash-only lifetime counts. This is a shared owner workspace log, not individual accounts in a multi-user service.
4. Protect and back up data/history.sqlite3. Clearing that database removes its history and duplicate memory. Credentials, cookies and proxy settings are not part of these exports.
