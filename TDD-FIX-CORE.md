# Core audit fixes — offline TDD record

Scope: `hermes_bilibili/core.py`, `tests/test_core_fixes.py` and this log only. No network, real accounts, credentials, configuration edits, staging or commits.

## Observed RED → GREEN cycles

All commands below actually ran using `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p test_core_fixes.py -v`.

1. **CORE-004 SQL semantic validation**: RED exposed the audited raw `TypeError` comparing clock to cache expiry `tomorrow`; GREEN validates finite, datetime-representable SQL timestamps, pause bool/strike ranges, request bool columns, accounts and persisted timezones. Corrupt storage fails with `PolicyError`. The test deletes its intentionally corrupt cache before proceeding to the independent pause cases because all operations conservatively validate storage.
2. **CORE-005 schema preflight**: RED `PolicyError not raised` for renamed required requests.operation_id; GREEN checks `PRAGMA user_version` and mandatory columns before use, rejects unknown versions or missing essential columns with explicit schema errors, migrates current unversioned layout to version 1 transactionally. Database is preserved; no delete/recreate recovery.
3. **CORE-003 approval provenance**: RED `PolicyError not raised` for an unapproved draft whose payload was changed to approved without changing its content digest; GREEN records approvals in an independent table and requires matching draft id, digest and expiry at claim, write reservation and write dispatch. Expiry mismatch and absent legacy event are denied. Initialization NEVER fabricates approval records. Legacy approved/sending/terminal payloads remain readable for audit; old approved but unsent drafts require replacement, not automatic approval. SHA256 detects accidental content changes; it is not authentication against an attacker with local database write access.
4. **CORE-002 fixed account budget timezone**: RED accepted a different timezone after use; GREEN pins account timezone on first reservation/claim and rejects conflicting read reservation, write reservation, claim and dispatch settings. Existing history without an accountable timezone fails closed and requires operator migration; history is never silently reinterpreted.
5. **CORE-001 unified dispatch identity**: RED reservation had no request_id; GREEN returns float-compatible `Reservation` carrying its unique requests.id. Both read and write dispatch atomically validate reservation ownership, actual spacing, pause, actual day/hour budgets, replay state and the persistent global guard. Actual dispatch/completion updates slot, including crossing midnight. Additional verification covers a delayed read failing actual-day budget, read/write guard sharing and wrong identifiers/modes. Conflicting late requests are conservatively cancelled, never silently retried/reordered.
6. **Permanent identity association API**: RED missing `identity_get`; GREEN adds `identity_get(key) -> {'mid': numeric_string} | None` and `identity_bind(key, mid) -> None`, for exact `identity:` + lowercase SHA256 keys. Associations have no TTL; both initialization and first lookup migrate legacy identity caches EVEN WHEN EXPIRED. `cache_get(identity-key)` delegates to durable lookup so expiry cannot erase the paused-account association. Binding refuses mid changes and checks the destination account's persisted pause before accepting. No session secret is persisted.
7. **Public account activity check**: RED missing `require_active` (also exposed by independently updated client); GREEN adds the public transaction-wrapped account pause check.
8. **Dispatch-marker corruption**: RED accepted SQL dispatched=2; GREEN validates the new request dispatched column as a strict 0/1 integer. Supplemental tests verify expired-cache migration at startup and occupied legacy guard/sending history migration without approval backfill.

## Compatibility and migration contract

- `reserve_request` parameters unchanged, return remains a float subclass: comparisons such as delay == 0/1.5 remain valid.
- `validate_dispatch(account, settings, *, write=False, operation_id=None, dm=False, request_id=None)` uses exact request ownership; new real HTTP clients must pass request_id for reads AND writes. Legacy write calls can resolve reservation by operation_id. Legacy no-ID reads remain active-check-only compatibility calls, and cannot bypass an occupied global guard.
- `finish_dispatch(operation_id=None, *, request_id=None)` releases only a matching guard and records completion slot. Call ONLY after validate_dispatch succeeded. An ownership error is not silently ignored by Store.
- Current unversioned schema is supported by additive tables and columns. Legacy requests are conservatively treated as already dispatched. A legacy occupied guard keeps its original owner and is not auto-cleared on upgrade/restart; only its matching operation can finish it. Old approvals are not backfilled.
- Early dispatch before reserved slot is denied. Injected sleep fixtures must advance their injected clocks; no real sleeping is needed.

## Verification and integration status

- `test_core_fixes.py`: 12 tests passed (final run).
- Existing `test_core.py`: 25 tests passed (including float equality, persistent spacing and concurrent claims).
- Existing `test_dispatch.py`: 9 tests passed after client acquired-flag fix was read back.
- `test_service.py`: 11 tests passed.
- Client has been read back and now calls identity_get/identity_bind, request_id on validate and finish, and public require_active.
- First concurrent full-suite execution: 125 tests ran, with 3 failures and 5 errors. Three dispatch failures were client unconditional-finally finish masking validate failures; two subsequent client errors also use fixed clocks with no-op sleeps. A missing public require_active exposed by that run has since been fixed with its own RED/GREEN. System python also lacks PyYAML, unrelated to this patch. Client/integration agents own their respective fixes; final verification is to be rerun after their updates.
- Latest full-suite run while parallel agents were updating files: 130 tests, 1 failure (`test_e2e` uses real Store clock but no-op sleep, incompatible with deliberate early-slot rejection), 1 error (missing optional test dependency `yaml`). Client dispatcher now only calls finish after successful validate; all nine dispatch regressions pass. Parent must adjust the E2E fixture clock/sleep and use its prepared test dependency environment. No production weakening is needed.
- No online behavior is claimed. All new tests use real isolated SQLite and deterministic clocks.
