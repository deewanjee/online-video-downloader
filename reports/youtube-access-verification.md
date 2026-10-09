# YouTube source access handling

The user's terminal warnings establish two distinct source responses: HTTP 429 rate limiting and a sign-in-to-confirm-not-a-bot challenge. They do not establish that the videos are private or age-restricted. Missing Visitor Data was also reported after the failed webpage request.

The app now observes HTTP 429 warnings as well as terminal exceptions. An observed rate limit prevents new YouTube source calls for ten minutes, including analysis, queue insertion and Retry; HTTP responses carry Retry-After. The cooldown is per running process and does not promise that YouTube will allow access after ten minutes. YouTube extraction is serialized and extractor requests are spaced by one second.

Tests inject a 429 warning followed by a bot challenge, verify that subsequent requests never reach the extractor, and verify that cooldown expiry and non-YouTube analysis work. Separate checks distinguish bot verification from private access, report a busy analysis request, validate optional cookie-file configuration, and ensure health responses do not disclose cookie paths. Cookie configuration is opt-in, desktop-only, and applies only to YouTube. No account cookies were imported and no authenticated live download was tested.

This change does not claim universal download support or bypass source restrictions. Public source access remains dependent on the platform, source IP, session and extractor requirements.

Validation: all 53 backend tests passed locally and inside the refreshed Docker image as the unprivileged app user. JavaScript syntax validation passed. Live authenticated source access remains unverified.
