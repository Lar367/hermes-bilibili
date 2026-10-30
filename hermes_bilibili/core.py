"""Profile-scoped, fail-closed policy and SQLite state; no network I/O."""
from dataclasses import dataclass, fields, asdict
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3
import stat
import threading
import time
import json
import hashlib
import uuid
from datetime import datetime


class PolicyError(Exception):
    """Safe public policy or persistence error."""


@dataclass(frozen=True)
class Settings:
    owner_mid: str = ''
    writes_enabled: bool = False
    read_daily: int = 3000
    read_hourly: int = 600
    write_daily: int = 30
    write_hourly: int = 12
    min_gap_seconds: float = 1.5
    write_gap_seconds: float = 120
    dm_gap_seconds: float = 60
    share_daily: int = 3
    min_score: int = 13
    timezone: str = 'Asia/Shanghai'
    preferences: tuple = ()
    dislikes: tuple = ()
    windows: tuple = (('20:00', '23:00'),)
    skip_chance: float = 0.3
    max_candidates: int = 5
    persona_max_chars: int = 6000
    cache_ttl_seconds: int = 600

    @classmethod
    def from_mapping(cls, mapping):
        if not isinstance(mapping, dict) or set(mapping) - {f.name for f in fields(cls)}:
            raise PolicyError('Unknown or invalid settings')
        return cls(**mapping)

    def __post_init__(self):
        if type(self.writes_enabled) is not bool or type(self.owner_mid) is not str or (self.owner_mid and not re.fullmatch(r'[0-9]+', self.owner_mid)):
            raise PolicyError('Invalid account or write switch')
        for name, ceiling in {'read_daily': 3000, 'read_hourly': 600, 'write_daily': 30, 'write_hourly': 12, 'share_daily': 3, 'min_score': 20, 'max_candidates': 5, 'persona_max_chars': 6000, 'cache_ttl_seconds': 86400}.items():
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= ceiling:
                raise PolicyError('Invalid bounded setting: ' + name)
        for name, lower, upper in [('min_gap_seconds', 1.5, 86400), ('write_gap_seconds', 120, 86400), ('dm_gap_seconds', 60, 86400), ('skip_chance', 0, 1)]:
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise PolicyError('Invalid numeric setting: ' + name)
        try:
            if type(self.timezone) is not str:
                raise ValueError()
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise PolicyError('Invalid timezone') from None
        for name in ('preferences', 'dislikes'):
            value = getattr(self, name)
            if type(value) not in (list, tuple) or len(value) > 100 or any(type(x) is not str or len(x) > 500 for x in value):
                raise PolicyError('Invalid preferences')
            object.__setattr__(self, name, tuple(value))
        if type(self.windows) not in (list, tuple) or not 1 <= len(self.windows) <= 24:
            raise PolicyError('Invalid windows')
        windows = []
        for window in self.windows:
            if isinstance(window, dict) and set(window) == {'start', 'end'}:
                pair = (window['start'], window['end'])
            elif type(window) is tuple and len(window) == 2:
                pair = window
            else:
                raise PolicyError('Invalid window')
            if any(type(x) is not str or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', x) for x in pair) or pair[0] == pair[1]:
                raise PolicyError('Invalid window time')
            windows.append(pair)
        object.__setattr__(self, 'windows', tuple(windows))

    def as_dict(self):
        value = asdict(self)
        value['preferences'] = list(self.preferences)
        value['dislikes'] = list(self.dislikes)
        value['windows'] = [{'start': start, 'end': end} for start, end in self.windows]
        return value


class Reservation(float):
    """Backward-compatible wait seconds carrying the durable request identity."""
    request_id: int
    def __new__(cls, delay, request_id):
        value = super().__new__(cls, delay)
        value.request_id = request_id
        return value


class Store:
    def __init__(self, path, *, clock=time.time):
        self.path = Path(os.path.abspath(os.fspath(path)))
        self.clock = clock
        self._lock = threading.RLock()
        self._db = None
        try:
            self._check_path()
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            self._check_path()
            os.chmod(self.path.parent, 0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise PolicyError('State must be a regular file')
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            self._db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute('PRAGMA busy_timeout=10000')
            if self._db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise PolicyError('State integrity check failed')
            self._db.execute('PRAGMA journal_mode=WAL')
            self._schema_preflight(self._db)
            with self._transaction() as db:
                db.execute('CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS operation_ids (id TEXT PRIMARY KEY, draft_id TEXT UNIQUE NOT NULL)')
                for row in db.execute('SELECT id FROM drafts').fetchall():
                    existing = self._get(db, row['id'])
                    if existing.get('operation_id'):
                        db.execute('INSERT INTO operation_ids VALUES(?,?) ON CONFLICT(draft_id) DO UPDATE SET id=excluded.id',
                                   (existing['operation_id'], existing['id']))
                db.execute('CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, account TEXT NOT NULL, slot REAL NOT NULL, write_request INTEGER NOT NULL, dm INTEGER NOT NULL, operation_id TEXT UNIQUE)')
                db.execute('CREATE TABLE IF NOT EXISTS pauses (account TEXT PRIMARY KEY, until_time REAL NOT NULL, manual INTEGER NOT NULL, strikes INTEGER NOT NULL, reason TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS memories (sequence INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS account_settings (account TEXT PRIMARY KEY, timezone TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS dispatch_guard (singleton INTEGER PRIMARY KEY CHECK(singleton=1), operation_id TEXT UNIQUE NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS approvals (draft_id TEXT PRIMARY KEY, digest TEXT NOT NULL, expires_at REAL NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS identities (key TEXT PRIMARY KEY, mid TEXT NOT NULL)')
                for row in db.execute("SELECT key,value FROM cache WHERE key LIKE 'identity:%'").fetchall():
                    self._identity_key(row['key'])
                    mid = self._identity_mid(self._load_json(row['value']))
                    existing = db.execute('SELECT mid FROM identities WHERE key=?', (row['key'],)).fetchone()
                    if existing is not None and existing['mid'] != mid:
                        raise PolicyError('Stored identity association changed')
                    db.execute('INSERT OR IGNORE INTO identities VALUES(?,?)', (row['key'], mid))
                    db.execute('DELETE FROM cache WHERE key=?', (row['key'],))
                if 'dispatched' not in {r['name'] for r in db.execute('PRAGMA table_info(requests)')}:
                    db.execute('ALTER TABLE requests ADD COLUMN dispatched INTEGER NOT NULL DEFAULT 1')
                if 'request_id' not in {r['name'] for r in db.execute('PRAGMA table_info(dispatch_guard)')}:
                    db.execute('ALTER TABLE dispatch_guard ADD COLUMN request_id INTEGER')
                db.execute('PRAGMA user_version=1')
        except PolicyError:
            self.close()
            raise
        except (OSError, sqlite3.Error):
            self.close()
            raise PolicyError('State storage unavailable or corrupt') from None

    @staticmethod
    def _schema_preflight(db):
        if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
            raise PolicyError('Unsupported state schema version; preserve database for upgrade')
        required = {
            'drafts': {'id', 'payload'}, 'operation_ids': {'id', 'draft_id'},
            'requests': {'id', 'account', 'slot', 'write_request', 'dm', 'operation_id'},
            'pauses': {'account', 'until_time', 'manual', 'strikes', 'reason'},
            'cache': {'key', 'value', 'expires_at'}, 'memories': {'sequence', 'kind', 'payload'},
            'account_settings': {'account', 'timezone'}, 'dispatch_guard': {'singleton', 'operation_id'},
            'approvals': {'draft_id', 'digest', 'expires_at'},
            'identities': {'key', 'mid'},
        }
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, columns in required.items():
            if table in tables and not columns <= {r['name'] for r in db.execute('PRAGMA table_info(' + table + ')')}:
                raise PolicyError('Unsupported state schema: missing required columns in ' + table)

    def _check_path(self):
        for part in (self.path, *self.path.parents):
            if part.is_symlink():
                raise PolicyError('Symlink state paths are forbidden')
        for suffix in ('-wal', '-shm', '-journal'):
            if Path(str(self.path) + suffix).is_symlink():
                raise PolicyError('Symlink state sidecars are forbidden')

    @contextmanager
    def _transaction(self):
        with self._lock:
            try:
                self._check_path()
                if self._db is None:
                    raise PolicyError('State storage is closed')
                self._db.execute('BEGIN IMMEDIATE')
                self._validate_storage(self._db)
                yield self._db
                self._db.execute('COMMIT')
            except sqlite3.Error:
                if self._db is not None and self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                raise PolicyError('State storage unavailable or corrupt') from None
            except BaseException:
                if self._db is not None and self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                raise

    @staticmethod
    def _timestamp(value):
        if type(value) not in (int, float) or not math.isfinite(value) or not -62135596800 <= value <= 253402300799:
            raise PolicyError('Stored timestamp corrupt')
        return value

    def _validate_storage(self, db):
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, columns in (('cache', ('expires_at',)), ('pauses', ('until_time',)), ('requests', ('slot',))):
            if table not in tables:
                continue
            for row in db.execute('SELECT * FROM ' + table):
                for column in columns:
                    self._timestamp(row[column])
                if table == 'pauses':
                    if type(row['manual']) is not int or row['manual'] not in (0, 1) or type(row['strikes']) is not int or not 0 <= row['strikes'] <= 32:
                        raise PolicyError('Stored pause state corrupt')
                if table == 'requests':
                    if any(type(row[k]) is not int or row[k] not in (0, 1) for k in ('write_request', 'dm', *(['dispatched'] if 'dispatched' in row.keys() else []))):
                        raise PolicyError('Stored request state corrupt')
                if table in ('pauses', 'requests'):
                    self._account(row['account'])
        if 'account_settings' in tables:
            for row in db.execute('SELECT * FROM account_settings'):
                self._account(row['account'])
                try:
                    if type(row['timezone']) is not str:
                        raise ValueError()
                    ZoneInfo(row['timezone'])
                except (ValueError, ZoneInfoNotFoundError):
                    raise PolicyError('Stored timezone corrupt') from None

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None

    @staticmethod
    def _account(account, *, anonymous=True):
        if type(account) is not str or not ((anonymous and account == 'anonymous') or re.fullmatch(r'[0-9]+', account)):
            raise PolicyError('Invalid account')

    def _pause(self, db, account):
        row = db.execute('SELECT * FROM pauses WHERE account=?', (account,)).fetchone()
        result = {'until': 0, 'manual': False, 'strikes': 0}
        if row:
            result.update(until=row['until_time'], manual=bool(row['manual']), strikes=row['strikes'])
        result['active'] = result['manual'] or self.clock() < result['until']
        return result

    def _require_active(self, db, account):
        if self._pause(db, account)['active']:
            raise PolicyError('Account paused; operator action or cooldown required')

    def require_active(self, account):
        self._account(account)
        with self._transaction() as db:
            self._require_active(db, account)

    def pause(self, account, *, reason, retry_after=0, manual=False):
        self._account(account)
        self._text(reason, 500)
        if type(manual) is not bool or type(retry_after) not in (int, float) or not math.isfinite(retry_after) or retry_after < 0:
            raise PolicyError('Invalid pause parameters')
        with self._transaction() as db:
            old = self._pause(db, account)
            strikes = min(old['strikes'] + 1, 32)
            delay = max(min(300 * 2 ** min(strikes - 1, 3), 1800), retry_after)
            until = max(old['until'], self.clock() + delay)
            db.execute('INSERT OR REPLACE INTO pauses VALUES(?,?,?,?,?)',
                       (account, until, int(manual or old['manual']), strikes, reason))

    def resume(self, account):
        self._account(account)
        with self._transaction() as db:
            db.execute('UPDATE pauses SET manual=0 WHERE account=?', (account,))

    def status(self, account=''):
        if account:
            self._account(account)
        with self._transaction() as db:
            drafts = [self._get(db, row['id']) for row in db.execute('SELECT id FROM drafts').fetchall()]
            drafts = [d for d in drafts if not account or d['account'] == account]
            pause_accounts = [row['account'] for row in db.execute('SELECT account FROM pauses').fetchall() if not account or row['account'] == account]
            pauses = {mid: self._pause(db, mid) for mid in pause_accounts}
            pause = self._pause(db, account) if account else {
                'until': max((p['until'] for p in pauses.values()), default=0),
                'manual': any(p['manual'] for p in pauses.values()),
                'active': any(p['active'] for p in pauses.values()),
                'strikes': max((p['strikes'] for p in pauses.values()), default=0)}
            counts, operations = {}, {}
            for draft in drafts:
                counts[draft['state']] = counts.get(draft['state'], 0) + 1
                if 'operation_id' in draft:
                    operations[draft['state']] = operations.get(draft['state'], 0) + 1
            requests = db.execute('SELECT account,slot,write_request FROM requests').fetchall()
            zones = {row['account']: row['timezone'] for row in db.execute('SELECT * FROM account_settings').fetchall()}
            accounts = {r['account'] for r in requests} | {d['account'] for d in drafts} | set(pause_accounts)
            if account:
                accounts = {account}
            now, usage = self.clock(), {}
            for mid in sorted(accounts):
                zone_name = zones.get(mid, 'Asia/Shanghai')
                zone = ZoneInfo(zone_name)
                day = datetime.fromtimestamp(now, zone).date()
                entry = dict(timezone=zone_name, read_daily=0, read_hourly=0, write_daily=0, write_hourly=0, share_daily=0)
                for request in requests:
                    if request['account'] != mid:
                        continue
                    prefix = 'write' if request['write_request'] else 'read'
                    entry[prefix + '_daily'] += datetime.fromtimestamp(request['slot'], zone).date() == day
                    entry[prefix + '_hourly'] += request['slot'] > now - 3600
                entry['share_daily'] = sum(d['account'] == mid and d['share'] and d['state'] in ('sending', 'confirmed', 'unverified', 'unknown') and datetime.fromtimestamp(self._share_time(db, d), zone).date() == day for d in drafts)
                usage[mid] = entry
            return {'draft_count': len(drafts), 'draft_states': counts, 'operation_counts': operations,
                    'write_in_flight': db.execute('SELECT 1 FROM dispatch_guard').fetchone() is not None,
                    'pause': pause, 'pauses': pauses, 'usage': usage}


    @staticmethod
    def _json(value):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        except (ValueError, TypeError):
            raise PolicyError('Invalid JSON value') from None

    @staticmethod
    def _load_json(payload):
        try:
            value = json.loads(payload)
            Store._json(value)  # Reject NaN/Infinity accepted by Python's decoder.
            return value
        except (ValueError, TypeError, RecursionError):
            raise PolicyError('Stored JSON state corrupt') from None

    @staticmethod
    def _digest(draft):
        bound = {key: draft[key] for key in ('account', 'kind', 'target', 'text', 'reason', 'score', 'share')}
        return hashlib.sha256(Store._json(bound).encode()).hexdigest()

    @staticmethod
    def _text(value, maximum, minimum=1):
        if type(value) is not str or not minimum <= len(value) <= maximum or any((ord(c) < 32 and c != '\n') or 127 <= ord(c) <= 159 for c in value):
            raise PolicyError('Invalid text')

    def _get(self, db, identifier):
        row = db.execute('SELECT payload FROM drafts WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise PolicyError('Draft not found')
        try:
            draft = self._load_json(row['payload'])
            if draft['digest'] != self._digest(draft):
                raise PolicyError('Draft integrity check failed')
            if draft['id'] != identifier or draft['state'] not in ('draft', 'approved', 'rejected', 'sending', 'confirmed', 'unverified', 'unknown', 'failed'):
                raise PolicyError('Draft state corrupt')
            timestamps = ['created_at', 'expires_at']
            if draft['state'] == 'approved':
                timestamps.append('approval_expires_at')
            if draft['state'] in ('sending', 'confirmed', 'unverified', 'unknown', 'failed'):
                timestamps.extend(['approval_expires_at', 'claimed_at'])
                if type(draft.get('operation_id')) is not str or not draft['operation_id']:
                    raise PolicyError('Operation state corrupt')
            if any(type(draft.get(key)) not in (int, float) or not math.isfinite(draft[key]) for key in timestamps):
                raise PolicyError('Draft timestamps corrupt')
            return draft
        except (ValueError, TypeError, KeyError):
            raise PolicyError('Draft state corrupt') from None

    def _save(self, db, draft):
        db.execute('INSERT OR REPLACE INTO drafts(id,payload) VALUES(?,?)', (draft['id'], self._json(draft)))

    def create_draft(self, account, kind, target, text, *, reason, score, share=True):
        if type(account) is not str or not re.fullmatch(r'[0-9]+', account):
            raise PolicyError('Invalid account')
        if kind not in ('comment', 'dm') or type(target) is not str or not re.fullmatch(r'BV[0-9A-Za-z]{10}' if kind == 'comment' else r'[0-9]+', target):
            raise PolicyError('Invalid draft target')
        if type(share) is not bool or (kind == 'comment' and not share) or (kind == 'dm' and share):
            raise PolicyError('Invalid sharing mode')
        self._text(text, 1000 if kind == 'comment' else 300)
        self._text(reason, 2000, 8 if share else 1)
        if type(score) is not int or not 0 <= score <= 20:
            raise PolicyError('Invalid draft score')
        now = self.clock()
        draft = dict(id=str(uuid.uuid4()), account=account, kind=kind, target=target, text=text,
                     reason=reason, score=score, share=share, state='draft', created_at=now, expires_at=now+1800)
        draft['digest'] = self._digest(draft)
        with self._transaction() as db:
            db.execute('INSERT INTO drafts(id,payload) VALUES(?,?)',(draft['id'],self._json(draft)))
        return draft

    def get_draft(self, id):
        with self._transaction() as db:
            return self._get(db, id)

    def _require_approval(self, db, draft):
        row = db.execute('SELECT digest,expires_at FROM approvals WHERE draft_id=?', (draft['id'],)).fetchone()
        if row is None or row['digest'] != draft['digest'] or row['expires_at'] != draft.get('approval_expires_at'):
            raise PolicyError('Approval event missing or inconsistent; create a new draft')
        self._timestamp(row['expires_at'])

    def approve_draft(self, id, digest):
        with self._transaction() as db:
            draft = self._get(db, id)
            now = self.clock()
            if digest != draft['digest'] or now >= draft['expires_at'] or draft['state'] not in ('draft', 'approved'):
                raise PolicyError('Draft approval denied')
            if draft['state'] == 'approved':
                if now >= draft['approval_expires_at']:
                    raise PolicyError('Approval expired; create a new draft')
                self._require_approval(db, draft)
                return draft
            draft.update(state='approved', approval_expires_at=min(now+600, draft['expires_at']))
            db.execute('INSERT INTO approvals VALUES(?,?,?)', (draft['id'], draft['digest'], draft['approval_expires_at']))
            self._save(db, draft)
            return draft

    def reject_draft(self, id):
        with self._transaction() as db:
            draft = self._get(db, id)
            if draft['state'] not in ('draft', 'approved'):
                raise PolicyError('Draft is no longer rejectable')
            draft['state'] = 'rejected'
            self._save(db, draft)
            return draft


    def _budget_timezone(self, db, account, settings):
        if not isinstance(settings, Settings):
            raise PolicyError('Invalid settings')
        previous = db.execute('SELECT timezone FROM account_settings WHERE account=?', (account,)).fetchone()
        if previous is not None and previous['timezone'] != settings.timezone:
            raise PolicyError('Timezone is fixed for accounts with budget history')
        if previous is None:
            history = db.execute('SELECT 1 FROM requests WHERE account=?', (account,)).fetchone()
            claimed = any(self._get(db, r['id'])['account'] == account and 'claimed_at' in self._get(db, r['id']) for r in db.execute('SELECT id FROM drafts').fetchall())
            if history or claimed:
                raise PolicyError('Timezone missing for historical usage; operator migration required')
            db.execute('INSERT INTO account_settings VALUES(?,?)', (account, settings.timezone))

    def reserve_request(self, account, settings, *, write=False, operation_id=None, dm=False):
        if not isinstance(settings, Settings) or type(account) is not str or not (account == 'anonymous' or re.fullmatch(r'[0-9]+', account)):
            raise PolicyError('Invalid reservation account or settings')
        if type(write) is not bool or type(dm) is not bool:
            raise PolicyError('Invalid reservation mode')
        now = self.clock()
        draft = None
        with self._transaction() as db:
            self._require_active(db, account)
            self._budget_timezone(db, account, settings)
            if not write and (operation_id is not None or dm):
                raise PolicyError('Read reservations cannot authorize writes')
            if write:
                if db.execute('SELECT 1 FROM dispatch_guard').fetchone():
                    raise PolicyError('Write request in flight; no automatic takeover')
                if not settings.writes_enabled or not settings.owner_mid or not operation_id:
                    raise PolicyError('Write reservation requires an approved operation')
                draft = self._operation(db, operation_id)
                if draft['account'] != account or draft['state'] != 'sending' or dm != (draft['kind'] == 'dm'):
                    raise PolicyError('Operation is not sendable by this account')
                self._require_approval(db, draft)
                if now >= min(draft['expires_at'], draft['approval_expires_at']):
                    raise PolicyError('Draft or approval expired before write reservation')
                if draft['kind'] == 'dm' and (draft['target'] != settings.owner_mid or account == settings.owner_mid):
                    raise PolicyError('DM restricted to configured owner')
                if db.execute('SELECT 1 FROM requests WHERE operation_id=?', (operation_id,)).fetchone():
                    raise PolicyError('Operation has already attempted a request')
            last = db.execute('SELECT MAX(slot) FROM requests').fetchone()[0]
            slot = max(now, last + settings.min_gap_seconds if last is not None else now)
            if write:
                last_write = db.execute('SELECT MAX(slot) FROM requests WHERE write_request=1').fetchone()[0]
                if last_write is not None:
                    slot = max(slot, last_write + settings.write_gap_seconds)
                if dm:
                    last_dm = db.execute('SELECT MAX(slot) FROM requests WHERE dm=1').fetchone()[0]
                    if last_dm is not None:
                        slot = max(slot, last_dm + settings.dm_gap_seconds)
            if slot - now > 30:
                raise PolicyError('Request queue exceeds safe waiting limit')
            if draft is not None:
                if slot >= min(draft['expires_at'], draft['approval_expires_at']):
                    raise PolicyError('Approval expires before reserved dispatch time')
                self._check_share(db, draft, settings, slot)
            rows = db.execute('SELECT slot FROM requests WHERE account=? AND write_request=?', (account, int(write))).fetchall()
            zone = ZoneInfo(settings.timezone)
            day = datetime.fromtimestamp(slot, zone).date()
            daily = sum(datetime.fromtimestamp(row['slot'], zone).date() == day for row in rows)
            hourly = sum(row['slot'] > slot - 3600 for row in rows)
            prefix = 'write' if write else 'read'
            if daily >= getattr(settings, prefix + '_daily') or hourly >= getattr(settings, prefix + '_hourly'):
                raise PolicyError('Request budget exhausted')
            db.execute('INSERT OR REPLACE INTO account_settings VALUES(?,?)', (account, settings.timezone))
            cursor = db.execute('INSERT INTO requests(account,slot,write_request,dm,operation_id,dispatched) VALUES(?,?,?,?,?,0)',
                       (account, slot, int(write), int(dm), operation_id))
            return Reservation(slot - now, cursor.lastrowid)

    def validate_dispatch(self, account, settings, *, write=False, operation_id=None, dm=False, request_id=None):
        """Recheck authorization after waiting, immediately before network I/O."""
        self._account(account)
        if type(write) is not bool or type(dm) is not bool or (request_id is not None and (type(request_id) is not int or request_id <= 0)):
            raise PolicyError('Invalid dispatch mode or request identifier')
        with self._transaction() as db:
            self._require_active(db, account)
            self._budget_timezone(db, account, settings)
            if db.execute('SELECT 1 FROM dispatch_guard').fetchone():
                raise PolicyError('Request in flight; no automatic takeover')
            if not write and request_id is None:
                if operation_id is not None or dm:
                    raise PolicyError('Invalid read dispatch')
                return  # Compatibility active-check only; real clients supply request_id.
            if request_id is None:
                reservation = db.execute('SELECT * FROM requests WHERE operation_id=?', (operation_id,)).fetchone()
            else:
                reservation = db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
            if reservation is None or reservation['account'] != account or reservation['write_request'] != int(write) or reservation['dm'] != int(dm) or reservation['operation_id'] != operation_id:
                raise PolicyError('Dispatch requires a matching reserved request')
            if reservation['dispatched']:
                raise PolicyError('Request has already been dispatched; never replay')
            draft = None
            if write:
                draft = self._operation(db, operation_id)
                self._require_approval(db, draft)
                if db.execute('SELECT 1 FROM dispatch_guard').fetchone():
                    raise PolicyError('Write request in flight; no automatic takeover')
                if (not settings.writes_enabled or not settings.owner_mid or
                        draft['account'] != account or draft['state'] != 'sending' or
                        dm != (draft['kind'] == 'dm')):
                    raise PolicyError('Operation is not authorized for dispatch')
                if self.clock() >= min(draft['expires_at'], draft['approval_expires_at']):
                    raise PolicyError('Draft or approval expired before dispatch')
                if draft['kind'] == 'dm' and (draft['target'] != settings.owner_mid or account == settings.owner_mid):
                    raise PolicyError('DM restricted to configured owner')
                if 'dispatched_at' in draft:
                    raise PolicyError('Operation has already been dispatched; never replay')
            dispatched_at = self.clock()
            if dispatched_at < reservation['slot']:
                raise PolicyError('Dispatch spacing: reserved slot has not arrived')
            spacing = db.execute('SELECT slot,write_request,dm FROM requests WHERE id<>?', (reservation['id'],)).fetchall()
            if any(abs(r['slot'] - dispatched_at) < settings.min_gap_seconds or
                   (write and r['write_request'] and abs(r['slot'] - dispatched_at) < settings.write_gap_seconds) or
                   (dm and r['dm'] and abs(r['slot'] - dispatched_at) < settings.dm_gap_seconds) for r in spacing):
                raise PolicyError('Dispatch spacing conflict; request cancelled without retry')
            zone = ZoneInfo(settings.timezone)
            day = datetime.fromtimestamp(dispatched_at, zone).date()
            others = db.execute('SELECT slot FROM requests WHERE account=? AND write_request=? AND id<>?', (account, int(write), reservation['id'])).fetchall()
            prefix = 'write' if write else 'read'
            if (sum(datetime.fromtimestamp(r['slot'], zone).date() == day for r in others) >= getattr(settings, prefix + '_daily') or
                    sum(r['slot'] > dispatched_at - 3600 for r in others) >= getattr(settings, prefix + '_hourly')):
                raise PolicyError('Request budget exhausted at dispatch')
            if draft is not None:
                self._check_share(db, draft, settings, dispatched_at)
                draft['dispatched_at'] = dispatched_at
                self._save(db, draft)
            db.execute('UPDATE requests SET slot=?,dispatched=1 WHERE id=?', (dispatched_at, reservation['id']))
            db.execute('INSERT INTO dispatch_guard(singleton,operation_id,request_id) VALUES(1,?,?)',
                       (operation_id if write else 'read:' + str(reservation['id']), reservation['id']))

    def finish_dispatch(self, operation_id=None, *, request_id=None):
        """Release only this operation's in-flight guard after transport returns."""
        with self._transaction() as db:
            row = db.execute('SELECT * FROM dispatch_guard WHERE singleton=1').fetchone()
            if row is None or (request_id is None and (operation_id is None or row['operation_id'] != operation_id)) or (request_id is not None and (type(request_id) is not int or row['request_id'] != request_id)) or (operation_id is not None and row['operation_id'] != operation_id):
                raise PolicyError('In-flight dispatch ownership mismatch')
            if row['request_id'] is not None:
                db.execute('UPDATE requests SET slot=MAX(slot,?) WHERE id=?', (self.clock(), row['request_id']))
            else:
                db.execute('UPDATE requests SET slot=MAX(slot,?) WHERE operation_id=?', (self.clock(), operation_id))
            db.execute('DELETE FROM dispatch_guard WHERE singleton=1')

    @staticmethod
    def _no_credentials(value):
        pending = [value]
        while pending:
            item = pending.pop()
            if type(item) is dict:
                if any(str(key).lower() in ('sessdata', 'bili_jct', 'buvid3', 'cookie', 'cookies', 'authorization', 'password', 'csrf', 'access_token', 'refresh_token') for key in item):
                    raise PolicyError('Credentials are forbidden in persisted content')
                pending.extend(item.values())
            elif type(item) in (list, tuple):
                pending.extend(item)

    @staticmethod
    def _identity_key(key):
        if type(key) is not str or not re.fullmatch(r'identity:[0-9a-f]{64}', key):
            raise PolicyError('Invalid identity key')

    @staticmethod
    def _identity_mid(value):
        if type(value) is not dict or 'mid' not in value:
            raise PolicyError('Stored identity corrupt')
        Store._account(value['mid'], anonymous=False)
        return value['mid']

    def _identity_get(self, db, key):
        row = db.execute('SELECT mid FROM identities WHERE key=?', (key,)).fetchone()
        legacy = db.execute('SELECT value FROM cache WHERE key=?', (key,)).fetchone()
        if legacy is not None:
            mid = self._identity_mid(self._load_json(legacy['value']))
            if row is not None and row['mid'] != mid:
                raise PolicyError('Stored identity association changed')
            db.execute('INSERT OR IGNORE INTO identities VALUES(?,?)', (key, mid))
            db.execute('DELETE FROM cache WHERE key=?', (key,))
            return {'mid': mid}
        if row is not None:
            self._account(row['mid'], anonymous=False)
            return {'mid': row['mid']}
        return None

    def identity_get(self, key):
        self._identity_key(key)
        with self._transaction() as db:
            return self._identity_get(db, key)

    def identity_bind(self, key, mid):
        self._identity_key(key)
        self._account(mid, anonymous=False)
        with self._transaction() as db:
            existing = self._identity_get(db, key)
            if existing is not None and existing['mid'] != mid:
                raise PolicyError('Identity association changed; refusing account switch')
            self._require_active(db, mid)
            db.execute('INSERT OR IGNORE INTO identities VALUES(?,?)', (key, mid))

    def cache_put(self, key, value, ttl):
        self._text(key, 1000)
        if type(ttl) not in (int, float) or not math.isfinite(ttl) or not 0 <= ttl <= 86400:
            raise PolicyError('Invalid cache lifetime')
        payload = self._json(value)
        self._no_credentials(json.loads(payload))
        if len(payload.encode()) > 2_000_000:
            raise PolicyError('Cache entry too large')
        with self._transaction() as db:
            db.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)', (key, payload, self.clock() + ttl))

    def cache_get(self, key):
        self._text(key, 1000)
        if key.startswith('identity:'):
            return self.identity_get(key)
        with self._transaction() as db:
            row = db.execute('SELECT value,expires_at FROM cache WHERE key=?', (key,)).fetchone()
            if row is None:
                return None
            if self.clock() >= row['expires_at']:
                db.execute('DELETE FROM cache WHERE key=?', (key,))
                return None
            try:
                return self._load_json(row['value'])
            except (TypeError, ValueError):
                raise PolicyError('Cache state corrupt') from None

    def remember(self, kind, text, *, metadata=None):
        if kind not in ('public', 'private', 'feedback'):
            raise PolicyError('Invalid memory kind')
        self._text(text, 12000)
        metadata = {} if metadata is None else metadata
        encoded = self._json(metadata)
        self._no_credentials(json.loads(encoded))
        if len(encoded.encode()) > 12000:
            raise PolicyError('Memory metadata too large')
        memory = dict(id=str(uuid.uuid4()), kind=kind, text=text, metadata=json.loads(encoded), created_at=self.clock())
        with self._transaction() as db:
            db.execute('INSERT INTO memories(kind,payload) VALUES(?,?)', (kind, self._json(memory)))
        return memory

    def memories(self, *, limit=20, kind=None):
        if type(limit) is not int or not 0 <= limit <= 100 or (kind is not None and kind not in ('public', 'private', 'feedback')):
            raise PolicyError('Invalid memory query')
        with self._transaction() as db:
            rows = db.execute('SELECT payload FROM memories WHERE (? IS NULL OR kind=?) ORDER BY sequence DESC LIMIT ?',
                              (kind, kind, limit)).fetchall()
            try:
                memories = [self._load_json(row['payload']) for row in rows]
                if any(type(m) is not dict or not {'id', 'kind', 'text', 'metadata', 'created_at'} <= m.keys() for m in memories):
                    raise PolicyError('Memory state corrupt')
                return memories
            except (ValueError, TypeError):
                raise PolicyError('Memory state corrupt') from None

    def _operation(self, db, identifier):
        if type(identifier) is not str or not identifier:
            raise PolicyError('Invalid operation identifier')
        for row in db.execute('SELECT id FROM drafts').fetchall():
            draft = self._get(db, row['id'])
            if draft.get('operation_id') == identifier:
                return draft
        raise PolicyError('Operation not found')

    def operation(self, id):
        with self._transaction() as db:
            return self._operation(db, id)

    def finish_send(self, operation_id, state, *, remote_id='', error=''):
        if state not in ('confirmed', 'unverified', 'unknown', 'failed'):
            raise PolicyError('Invalid operation result')
        self._text(remote_id, 200, 0)
        self._text(error, 2000, 0)
        with self._transaction() as db:
            draft = self._operation(db, operation_id)
            if draft['state'] == state:
                return draft
            if draft['state'] != 'sending' and not (draft['state'] in ('unknown', 'unverified') and state == 'confirmed'):
                raise PolicyError('Terminal operation cannot be replayed or reset')
            draft.update(state=state, remote_id=remote_id or draft.get('remote_id', ''),
                         error=error, finished_at=self.clock())
            self._save(db, draft)
            return draft

    @staticmethod
    def _share_time(db, draft):
        reservation = db.execute('SELECT slot FROM requests WHERE operation_id=?',
                                 (draft.get('operation_id'),)).fetchone()
        return reservation['slot'] if reservation else draft['claimed_at']

    def _check_share(self, db, draft, settings, at):
        if not draft['share']:
            return
        if draft['score'] < settings.min_score:
            raise PolicyError('Share score is below threshold')
        zone = ZoneInfo(settings.timezone)
        day = datetime.fromtimestamp(at, zone).date()
        used = 0
        for row in db.execute('SELECT id FROM drafts').fetchall():
            previous = self._get(db, row['id'])
            if (previous['id'] != draft['id'] and previous['account'] == draft['account'] and
                    previous['share'] and previous['state'] in ('sending', 'confirmed', 'unverified', 'unknown')):
                sent_at = self._share_time(db, previous)
                if previous['target'] == draft['target'] and sent_at > at - 7 * 86400:
                    raise PolicyError('Target already shared or delivery is unresolved')
                used += datetime.fromtimestamp(sent_at, zone).date() == day
        if used >= settings.share_daily:
            raise PolicyError('Daily sharing limit reached')

    def claim_send(self, id, account, settings):
        self._account(account, anonymous=False)
        with self._transaction() as db:
            self._require_active(db, account)
            draft = self._get(db, id)
            now = self.clock()
            if not isinstance(settings, Settings) or not settings.writes_enabled or not settings.owner_mid:
                raise PolicyError('Writes disabled or owner not configured')
            self._budget_timezone(db, account, settings)
            if draft['account'] != account or draft['state'] != 'approved' or now >= min(draft['expires_at'], draft.get('approval_expires_at', 0)):
                raise PolicyError('Draft is not approved for this account or has expired')
            self._require_approval(db, draft)
            if draft['kind'] == 'dm' and (draft['share'] or draft['target'] != settings.owner_mid or account == settings.owner_mid):
                raise PolicyError('DM restricted to configured owner')
            self._check_share(db, draft, settings, now)
            db.execute('INSERT OR REPLACE INTO account_settings VALUES(?,?)', (account, settings.timezone))
            draft.update(state='sending', operation_id=str(uuid.uuid4()), claimed_at=now)
            db.execute('INSERT INTO operation_ids VALUES(?,?)', (draft['operation_id'], draft['id']))
            self._save(db, draft)
            return draft
