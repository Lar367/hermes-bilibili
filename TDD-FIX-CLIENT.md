# CLIENT fixes — offline TDD record

Scope: only `hermes_bilibili/client.py`, `hermes_bilibili/media.py`, `tests/test_client.py`, `tests/test_media.py`, `tests/test_client_fixes.py`, and this file. No live Bilibili requests, real credentials/profile reads, staging or commits.

## Fixed

- CLIENT-01: constructor uses permanent `Store.identity_get`; verified nav calls `identity_bind`, not a 24-hour cache. Regression starts with a permanent identity and no cache; real-Store supplemental check advances 86401 seconds and retains the manual-pause barrier.
- CLIENT-02: nav identity changes fail closed before replacing `_account`; new account must pass `require_active` and binding before returning. A changed nav result never relabels the request budget as the new account.
- CLIENT-03: verification binds durable id/operation_id/digest/state/account/kind/target/text/remote_id. Only matching `unverified`, `unknown`, or `confirmed` operations may read back. Caller `sending` snapshots are rejected. Existing fixture now persists `unverified` before verification. Parent owns `service.py` and has changed it to pass the persisted accepted record.
- CLIENT-04 / media dispatch: shared `BiliClient.dispatch()` reserves, sleeps a real float, validates the reservation request_id, and releases its acquired guard in finally. API risk handling, subtitle response handling and media response handling occur before guard release. Failed preflight never releases a guard it did not acquire. Subtitles and public media remain cookieless. Frame extraction calls `client.download_public`, whose downloader accepts a dispatch context callback and response callback.
- CLIENT-05: WBI signature and GET wire query both use `urlencode(..., quote_via=quote, safe='~')`; POST remains form encoded. A real Node crypto/encodeURIComponent process agrees on a space/Chinese/plus/tilde vector and exact emitted query bytes.
- CLIENT-07 minimal hardening: API transport injects monotonic clock and checks a 20-second deadline before/after bounded read1 chunks; media also uses read1 rather than buffer-filling read. Successful owned collages are limited to 20 files / 50 MB in the requested output directory; only exact plugin collage filenames are considered, and symlink matches are refused.
- CLIENT-08 minimal hardening: both ffprobe and ffmpeg force `-f mov` before input. Real generated local MP4 still produces a PNG collage. This is container restriction, not an OS sandbox.

## RED → GREEN evidence

Each behavior change was preceded by an executed failing regression:

1. Permanent-identity pause: nav incorrectly ran (FAIL) → identity_get → GREEN.
2. Permanent binding/new-mid pause: missing binding and successful paused nav (2 FAIL) → fail-closed account flow → GREEN.
3. Forged remote id/digest/state: network attempted before rejecting (FAIL) → durable evidence binding → GREEN. User tightened caller state contract; newly added `sending` mismatch went RED before adding state equality.
4. Dispatch: missing request_id (KeyError) and subtitle CDN dispatched while paused (FAIL) → shared context → GREEN.
5. WBI Node vector: Python `ee1b1a73dac7fe76264a5cc0002bbc92` versus Node `0d8face6b119b118154269c6e7c2831f` (FAIL) → RFC3986 on signer and GET → GREEN.
6. Client media entrypoint absent (AttributeError) → guarded downloader bridge → GREEN.
7. Deadline clock constructor absent (TypeError) → monotonic chunk checks → GREEN.
8. Retention helper absent (AttributeError) → bounded owned-file retention → GREEN.
9. Real ffmpeg commands lacked `-f` (FAIL) → force mov → real MP4/PNG GREEN.
10. Media fixture forbids buffer-filling read and initially fails → read1 → GREEN.

Real Store integration initially exposed missing `require_active` while the other agent was implementing core; once available, it exposed old tests' no-op sleep against newly enforced spacing. Fixtures now advance their injected Store clock by the requested delay, rather than bypassing production checks. It also exposed ownership mismatch from releasing after rejected preflight; release now occurs only after successful guard acquisition.

## Verified commands and latest results

- `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_client*.py' -q`: 33 tests, OK.
- `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p test_media.py -q`: 6 tests, OK; includes real ffmpeg and Node is exercised by client fixes.
- `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -q`: 133 tests at that run, FAILED: one E2E fixture still has real Store plus `sleep=lambda _:None` and cannot satisfy new dispatch spacing; one plugin test cannot import yaml in the selected Python. These files are outside this agent's permitted write scope. Parent must update E2E fake clock and use/install appropriate test environment dependency. No production guard bypass was added to satisfy legacy fixtures.

## Not fixed / honest limits

- CLIENT-06 endpoints unchanged: legacy subtitle/playurl compatibility and signed primary/fallback behavior need a separately authorized live-service contract check. No claim of live API success.
- Deadline is checked between reads; a single blocked socket/open/DNS call is not forcibly interrupted at the total deadline. urllib's socket timeout remains the per-block upper bound. No real local slow-server test was performed.
- Retention bound is per supplied output directory, not a global aggregate across arbitrary descendant directories; concurrent extraction and same-UID path replacement remain possible. OS CPU/memory/file-access sandbox is deferred. File protocol whitelist + forced mov is not filesystem isolation.
- Direct standalone `media.download_public` intentionally permits omitted dispatch for isolated use; production frame path always goes through BiliClient. Treat the low-level function as a bounded transport helper, not the policy entrypoint.
- No real credentials, platform behavior, malicious decoder exhaustion, DNS-private-address rebinding, or same-UID TOCTOU were validated.
- Skills were loaded for TDD/debugging. No skill memory files were written because this task explicitly restricts writes to six project files.
