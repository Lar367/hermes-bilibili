# Client / media TDD evidence

## Scope and execution

Only `hermes_bilibili/client.py`, `hermes_bilibili/media.py`, `tests/test_client.py`, `tests/test_media.py`, and this report were edited by the client task. All HTTP responses are explicitly offline fixtures. No Bilibili account, credentials, live read, or live write was accessed. Python stdlib unittest is the runner. ffmpeg/ffprobe were actually present and executed locally against a generated two-second test-pattern MP4.

Commands below were actually run from `/home/admin/projects/hermes-bilibili`:

- `python -m unittest discover -s tests -p test_client.py -v`
- `python -m unittest discover -s tests -p test_client.py -q`
- `python -m unittest discover -s tests -p test_media.py -v`
- `python -m unittest discover -s tests -p test_media.py -q`
- `python -m unittest discover -s tests -v`

## Observed RED → GREEN development slices

| Slice | Actual observed RED | Actual observed GREEN |
|---|---|---|
| Restore search BV validator | Existing 6 tests: search `NameError: _bvid is not defined` | 6 client tests OK after strict BV validator |
| Video read + bounded normalization + account-scoped metadata cache | 7 tests: `video required` failure | 7 client tests OK |
| Dynamic comment reads + validation | 8 tests: `comments required` failure | 8 client tests OK |
| Subtitle metadata + public cookieless JSON + cue bounds + absence | 9 tests: `subtitles required` failure | 9 client tests OK |
| Comment send bound to stored claimed draft/account/CSRF | 10 tests: `comment send required` failure | 10 client tests OK |
| Owner-only mutual-follow DM | 12 tests: `DM send required` and verification still missing | DM passes; verification remains the sole failure |
| Actual comment GET readback | `verification required` failure | 12 client tests OK; author, text, target and reply-id mismatch all reject |
| Media URL policy | 1 media test: `media module required` failure | 1 media test OK |
| Cookieless bounded public download | 2 media tests: `bounded downloader required` failure | 2 media tests OK |
| Local ffmpeg collage + cleanup + plugin-data boundary | 3 media tests: `frames helper required` failure | 3 media tests OK; real PNG signature checked |
| Reject `..` path escape before metadata/network | Strengthened test failed: metadata called once | 3 media tests OK after normalized absolute containment |
| Frames bridge and structured outcome errors | `frames bridge required`, missing `APIError.definitive` | 17 client tests OK |
| Persistent credential-identity budget + real core DM cache TTL | Expected numeric mid budget was anonymous; real core rejected too-long device cache TTL | 19 client tests OK; identity SHA256 key contains no credential |
| HTTP definitive vs ambiguous outcome | 21 tests: HTTP400 definitive flag false | 21 client tests OK |

Regression/edge tests also cover code=0 without remote id, no automatic retries, account-switch refusal, upstream exception redaction, hostile subtitle URLs, inverted subtitle timestamps, nonfinite download deadlines, paused known-account nav after client restart, and real core approve → claim → send → exact readback → confirmed. These additional regression tests passed on first execution; they are not falsely counted as new RED/GREEN slices.

## Latest scoped verification

```
python -m unittest discover -s tests -p test_client.py -q
Ran 22 tests in 0.098s
OK

python -m unittest discover -s tests -p test_media.py -q
Ran 4 tests in 0.169s
OK
```

No skips in these scoped runs. The ffmpeg test generates a real source MP4 with lavfi, injects those bytes as an offline download, runs real local ffprobe and ffmpeg, checks the PNG signature, checks that only the collage remains, and exercises timeout cleanup.

Full-suite runs were also made during concurrent integration. The first reached 65 tests with one unrelated `test_cycle_sidecar_symlink_is_rejected` failure and ResourceWarnings from other tests' SQLite connections. A later `python -m unittest discover -s tests -q` reached 72 tests with two core-agent in-progress issues: `test_operation_identifiers_are_unique_even_on_uuid_collision` did not yet reject collision, and `test_persisted_json_corruption_fails_closed_with_safe_policy_error` fixture UPDATE was rejected by SQLite's malformed-JSON constraint. Client/media remained passing. The client task did not modify cycle/core ownership files. Parent must use its final integration run as authoritative because other agents were adding tests concurrently.

## Frozen public interfaces delivered

- `APIError(PolicyError)`: safe text; `code`, `status`, `definitive`, `ambiguous`. A documented nonzero API business code or explicit rejecting HTTP4xx is definitive. Transport failure, malformed response, server5xx and success-without-id remain ambiguous. Parent service can use `getattr(exc, 'definitive', False)` when choosing failed vs unknown. No fallback alternate PolicyError class remains.
- `Response(status, headers, body).json()` rejects malformed/non-object/nonfinite JSON.
- `HTTPTransport.request(...)`: HTTPS exact allowlist; no redirects/proxies; bounded body; 20s timeout; Cookie rejected on non-API hosts.
- `BiliClient.account/search/video/comments/subtitles/send_comment/send_dm/verify` and canonical `wbi_sign` implemented. Textual upstream material is marked `untrusted: True`.
- `BiliClient.frames(bvid, output_dir: Path) -> dict`: delegates to `media.extract_frames`; returns `sheet` and `path` for the one PNG collage, `bvid`, `duration`, `layout='3x3'`, `untrusted=True`.
- `media.extract_frames(client,bvid,output_dir)`: output must be under `Path(client.store.path).parent`, with symlink and traversal rejection; caller should pass plugin-data `/frames`. Public CDN namespaces limited to HTTPS subdomains of bilivideo.com / bilivideo.cn. Source URL/cookies are never passed to ffmpeg or returned to tools. Single durl file only; <=20MB; <=600s metadata and probed duration; <=4096 dimensions; 30s download deadline checks with <=20s socket timeout, ffprobe15s, ffmpeg40s; 3x3 PNG only; originals and partial output cleaned even on failure. Missing tools/unsupported streams return honest MediaError. No third-party Python packages.

## Safety/integration details and limitations

Every send reloads the stored operation, binds all approval payload fields, requires sending state, verifies current authenticated numeric mid, and reserves write with operation_id. Real Store enforces one attempt and current account pause. DM requires configured owner != authenticated account, share=False, <=300 chars, relation attribute exactly6, CSRF and stable deterministic plugin-data/account device UUID (cached with the core's <=86400 TTL). No group sends. A success code alone never yields verified=True.

Comment verification supports operation state still `sending` immediately after POST, and later terminal readback, by GET `/x/v2/reply/detail` matching exact account/text/aid/type/rpid. DM returns verified=False with explicit unsupported reason.

Known session identities persist only SHA256(SESSDATA) -> numeric mid in ordinary core cache (24h). New/expired identities initially use anonymous read budget; authenticated writes always revalidate account and core claim/reservation pause. This is not a permanent account registry: after identity-cache expiry one nav can occur before an account-specific pause is known; write remains fail-closed. Full permanent credential-identity pause semantics would need a frozen dedicated core interface, not hidden DB mutation.

QR interactive login remains explicitly deferred per the optional contract. Live endpoint availability and real-account write behavior are unverified by design, not represented by fixtures as live evidence.
