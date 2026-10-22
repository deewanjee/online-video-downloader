# Troubleshooting and server messages

Match a message with the right next step.

## Video unavailable / visionos client

1. The source reports that the requested video is unavailable. A response from one YouTube client alone cannot establish whether the video is deleted.
2. For a generic unavailable response, the app makes one supported alternate-client attempt within the same request gate, followed by at most one end-of-queue recovery recheck. Explicit private/removed/restricted errors do not trigger this fallback.
3. Open the exact video URL at YouTube. Check its availability for your account and region. Update yt-dlp with the project virtual environment if extraction fails across many public links.

## HTTP 429 / bot verification

1. YouTube is limiting requests or asking for verification. Let the shared cooldown expire and reduce parallel transfers. Do not repeatedly press Retry.
2. Authorized optional cookie-file configuration is described in the project README. Do not upload passwords or cookies into help forms or chat. Cookies are never imported automatically.

## Merging / not converting / preparing preview

1. Merging audio and video is normal. Deleting component files after a successful merge is normal.
2. Not converting media file; already is in target format mp4 is a success/informational message, not a conversion error.
3. Preparing a compatible preview is separate from the saved original. Open Folder to use the original while the preview is prepared.

## Folder, connection and setup

1. If a drive is disconnected or a folder is read-only, reconnect it or fix permissions before Retry. Changing the default folder does not relocate an existing job.
2. If the portal says Restart to apply update, stop the old console with Ctrl+C, relaunch the updated batch file, and press Ctrl+F5.
3. Open System checks in the portal to see FFmpeg, FFprobe, JavaScript runtime and server-version status. Keep detailed terminal output for diagnosis, excluding credentials or private URLs.
