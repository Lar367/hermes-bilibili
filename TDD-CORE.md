# Core TDD execution record

Scope: `hermes_bilibili/core.py`, `tests/test_core.py`, this file only. Frozen contract: `ARCHITECTURE.md` public core interfaces. All runs use the real stdlib SQLite implementation, injected clock, temporary profile directory under `/home/admin/.hermes/cache/scratch`; no network or live account calls.

Command (each RED/GREEN below ran the complete core suite):

```sh
TMPDIR=/home/admin/.hermes/cache/scratch python -m unittest discover -s tests -p test_core.py -v
```

| Slice | Observed RED | Observed GREEN |
|---|---|---|
| Resume baseline: operation lookup and terminal transitions | 6 tests, 1 failure: `finish missing` | 6 tests OK; unknown cannot reset, idempotent results, verification promotion |
| Atomic daily share claims | 7 tests, 1 failure: 8 concurrent claims succeeded instead of 3 | 7 tests OK; 8 SQLite connections/worker threads, exactly 3 unique operations |
| Seven-day target dedup | 8 tests, 1 failure: duplicate unknown target was accepted | 8 tests OK; account isolation, seven-day boundary, definitive failure releases business quota |
| Global request spacing | 9 tests, 1 failure: `request reservations missing` | 9 tests OK; persisted global slots, 30-second queue ceiling, closed-store fail-closed |
| Account-bound read budgets | 10 tests, 1 failure: hourly over-budget request was accepted | 10 tests OK; local calendar daily limit, rolling hour, zero budget, account separation |
| Write authorization and single attempt | 11 tests, 1 failure: unclaimed write accepted | 11 tests OK; claimed operation/account/kind binding, write switch, no duplicate reservation; failed requests retain budget |
| Write/DM spacing | 12 tests, 1 failure: immediate second write accepted | 12 tests OK; 120-second write gap and stricter configured DM gap persist across connections |
| Persistent risk pause | 13 tests, 1 failure: `persistent risk pause missing` | 13 tests OK; 300/600/1200/1800 backoff, longer Retry-After, manual-only resume, account isolation, claim/reservation blocked |

Implementation deliberately does not assert that `confirmed` is proof of a remote write: only the service's readback is authorized to call that transition. Core performs no network requests. An ambiguous or crashed `sending` operation remains non-sendable, counts against quota, and blocks target duplicates for the contract's seven-day interval.
