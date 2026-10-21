# Playlist metadata waiting queue (1.3.2)

The reported message comes from the local YouTube request gate: playlist analysis previously used a non-blocking metadata request, so an active transfer caused an immediate SourceBusy response. It does not establish a bad playlist or YouTube HTTP 429.

The dashboard now queues playlist metadata in a separate single-worker executor and polls a load-status endpoint. The YouTube source gate serves blocking requests in FIFO order, allowing the metadata load to wait for a slot without being overtaken indefinitely by later downloads. At most ten metadata loads are retained/queued; finished entries can be evicted, and no event/future internals are exposed in API responses. The existing direct analyze API remains non-waiting for compatibility.

Cancel and input changes request cancellation. A waiting source ticket is removed without acquiring a slot or contacting YouTube. Cancellation of an already active metadata request is checked between entries and after extraction; an in-flight HTTP request may finish first. Metadata cancellation does not stop an existing video transfer. App shutdown signals metadata cancellation and cancels pending metadata futures. Source cooldown and the one/two-transfer setting remain unchanged. The source gate is held while lazy playlist pages are consumed.

Validation:

- 138 backend tests pass locally and in the fresh Docker image as its unprivileged app user. New checks exercise a held YouTube slot followed by automatic metadata completion, cancellation without source contact, FIFO ordering ahead of a later download, background failures/status responses, queue bounds, worker submission failure, cooldown checks and shutdown release.
- Chromium verified the busy-source waiting message, cancellation without source extraction, preservation of the active transfer, automatic loading after releasing the source slot, three completed original files, and native AV1/Opus playback. It reported no JavaScript errors. Metadata/transfer used a local source adapter with real generated media.
- JavaScript syntax and Git whitespace checks pass.

These checks use local source adapters; live access to the supplied YouTube playlist is not established. This change waits for source capacity rather than bypassing rate limits or authentication.
