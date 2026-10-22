# Automatic recovery

Understand end-of-queue retries and failures that need your action.

## How recovery works

1. Automatic recovery is enabled by default. Temporary network failures and server errors are scheduled after the first-pass download queue.
2. Recovery uses up to three automatic attempts with increasing waits. Generic Video unavailable gets one recovery recheck; it can also reflect genuine source unavailability.
3. YouTube HTTP 429 follows the shared source cooldown. The portal does not send repeated source requests during the wait. A cooldown ending does not guarantee the source will accept the next request.

## Use Recovery center

1. Open Recovery center to see waiting attempts, reason, scheduled time and next action.
2. Pause automatic recovery to stop scheduled retry attempts. Resume it to reconsider eligible failed entries; completed files are never re-downloaded by this process.
3. After the limit is reached, the card stays Needs attention with a next step. Manual Retry starts a new attempt budget.

## What cannot recover automatically

1. Private/age-restricted access, DRM, region restrictions, explicit removal, disk/folder permission issues and invalid formats need user action. Recovery does not bypass them.
2. Keep the app console open for background work. On startup, enabled recovery reconsiders eligible interrupted/temporary failures, subject to the same limits and destination checks.
3. Removing a queued recovery item cancels it. Your original format and recorded folder are retained.
