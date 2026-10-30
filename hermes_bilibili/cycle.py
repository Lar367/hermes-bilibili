"""Durable daily admission; never confuse admission with completed work."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import random as random_module
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo


class CyclePlanner:
    def __init__(self, path, *, clock=time.time, random=random_module.random):
        self.path = Path(path)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError('cycle storage must not be a symlink')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self.clock, self.random = clock, random
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cycles (day TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL, due REAL NOT NULL, ends REAL NOT NULL, skip INTEGER NOT NULL, state TEXT NOT NULL, result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, run_id TEXT, created_at REAL NOT NULL, payload TEXT NOT NULL)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        for candidate in (self.path, *self.path.parents, *(Path(str(self.path) + suffix) for suffix in ('-wal','-shm','-journal'))):
            if candidate.is_symlink():
                raise ValueError('cycle storage and sidecars must not be symlinks')
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            yield db
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def start(self, settings):
        now = self.clock()
        local = datetime.fromtimestamp(now, ZoneInfo(settings['timezone']))
        day = local.date().isoformat()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            running = db.execute("SELECT * FROM cycles WHERE state='running' ORDER BY day LIMIT 1").fetchone()
            if running is not None:
                db.execute('COMMIT')
                return {**dict(running), 'claimed':False, 'skip':bool(running['skip']), 'review_required':True}
            row = db.execute('SELECT * FROM cycles WHERE day=?', (day,)).fetchone()
            previous_day = (local.date() - timedelta(days=1)).isoformat()
            previous = db.execute('SELECT * FROM cycles WHERE day=? AND ends>?', (previous_day, now)).fetchone()
            if row is None and previous is not None:
                row = previous
                day = row['day']
            if row is None:
                windows = []
                midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
                for window in settings['windows']:
                    sh, sm = map(int, window['start'].split(':'))
                    eh, em = map(int, window['end'].split(':'))
                    start = midnight + timedelta(hours=sh, minutes=sm)
                    end = midnight + timedelta(hours=eh, minutes=em)
                    if end <= start:
                        end += timedelta(days=1)
                        previous_start = start - timedelta(days=1)
                        previous_end = end - timedelta(days=1)
                        if previous_start.timestamp() <= now < previous_end.timestamp():
                            windows.append((now, previous_end.timestamp()))
                    if end.timestamp() > now:
                        windows.append((max(now, start.timestamp()), end.timestamp()))
                if windows:
                    begin, end = sorted(windows)[0]
                    due = begin + self.random() * (end - begin)
                    skip = int(self.random() < settings['skip_chance'])
                    state = 'planned'
                else:
                    due = end = now
                    skip, state = 0, 'missed'
                db.execute('INSERT INTO cycles VALUES (?,?,?,?,?,?,NULL)', (day, str(uuid.uuid4()), due, end, skip, state))
                row = db.execute('SELECT * FROM cycles WHERE day=?', (day,)).fetchone()
            data = dict(row)
            claimed = False
            if data['state'] == 'planned' and now >= data['ends']:
                db.execute("UPDATE cycles SET state='missed' WHERE day=?", (day,))
                data['state'] = 'missed'
            elif data['state'] == 'planned' and now >= data['due']:
                db.execute("UPDATE cycles SET state='running' WHERE day=?", (day,))
                data['state'], claimed = 'running', True
            db.execute('COMMIT')
        data['claimed'] = claimed
        data['skip'] = bool(data['skip'])
        return data

    def complete(self, run_id, summary, evidence):
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 12000:
            raise ValueError('nonempty bounded summary required')
        if not isinstance(evidence, list) or len(evidence) > 20 or any(not isinstance(x, str) or not x or len(x) > 100 for x in evidence):
            raise ValueError('bounded evidence identifiers required')
        result = json.dumps({'summary': summary, 'evidence': evidence}, ensure_ascii=False)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM cycles WHERE run_id=?', (run_id,)).fetchone()
            if not row or row['state'] != 'running':
                db.execute('ROLLBACK')
                raise ValueError('cycle is not running; no completion or replay permitted')
            if not row['skip'] and not evidence:
                db.execute('ROLLBACK')
                raise ValueError('reading cycle requires evidence; skipped cycle may have none')
            db.execute("UPDATE cycles SET state='complete', result=? WHERE run_id=?", (result, run_id))
            db.execute('COMMIT')
        return {'run_id': run_id, 'state': 'complete', 'summary': summary, 'evidence': evidence}

    def record_evidence(self, identifier, payload):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if not isinstance(identifier,str) or not identifier.startswith('evidence:') or len(identifier)>100 or len(encoded)>2000:
            raise ValueError('invalid evidence')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            running=db.execute("SELECT run_id FROM cycles WHERE state='running' ORDER BY day LIMIT 1").fetchone()
            db.execute('INSERT INTO evidence VALUES(?,?,?,?)',(identifier,running['run_id'] if running else None,self.clock(),encoded))
            db.execute('COMMIT')

    def validate_evidence(self, run_id, identifiers):
        with self._db() as db:
            for identifier in identifiers:
                row=db.execute('SELECT run_id,created_at FROM evidence WHERE id=?',(identifier,)).fetchone()
                if row is None or row['run_id']!=run_id or self.clock()-row['created_at']>86400:
                    return False
        return True

    def abort(self, run_id, reason):
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:
            raise ValueError('operator abort reason required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            updated=db.execute("UPDATE cycles SET state='aborted',result=? WHERE run_id=? AND state='running'",(json.dumps({'operator_reason':reason},ensure_ascii=False),run_id))
            if updated.rowcount!=1:
                raise ValueError('only a running cycle can be aborted')
            db.execute('COMMIT')
        return {'run_id':run_id,'state':'aborted','reason':reason}

    def status(self):
        with self._db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM cycles ORDER BY day DESC LIMIT 7')]
