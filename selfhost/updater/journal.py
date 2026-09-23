"""Durable operation records independent of the application's database/container."""
from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


class Conflict(Exception):
    pass


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL, record TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_update ON operations ((1))
                    WHERE status IN ('queued', 'running');
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, request, blocked_reason=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT record FROM operations WHERE request_id=?', (request['request_id'],)).fetchone()
            if existing:
                record = json.loads(existing[0])
                if any(record[key] != request[key] for key in ('actor_id', 'release_id', 'tag')):
                    raise Conflict('This request identifier was already used for a different update.')
                return record
            if blocked_reason:
                raise Conflict(blocked_reason)
            if db.execute("SELECT 1 FROM operations WHERE status='recovery_required'").fetchone():
                raise Conflict('Un aggiornamento richiede una verifica di ripristino prima di un nuovo tentativo.')
            if db.execute("SELECT 1 FROM operations WHERE status IN ('queued', 'running')").fetchone():
                raise Conflict('An update is already in progress.')
            record = {
                **request, 'id': str(uuid4()), 'status': 'queued', 'stage': 'queued',
                'target_version': request['tag'], 'created_at': now(), 'updated_at': now(),
                'error': None, 'recovery': None,
            }
            db.execute('INSERT INTO operations VALUES (?, ?, ?, ?)', (record['id'], request['request_id'], 'queued', json.dumps(record)))
            return record

    def update(self, operation_id, **changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM operations WHERE id=?', (operation_id,)).fetchone()
            if not row:
                raise KeyError(operation_id)
            record = {**json.loads(row[0]), **changes, 'updated_at': now()}
            db.execute('UPDATE operations SET status=?, record=? WHERE id=?', (record['status'], json.dumps(record), operation_id))
            return record

    def records(self):
        with self.connect() as db:
            records = [json.loads(row[0]) for row in db.execute('SELECT record FROM operations ORDER BY rowid DESC')]
        return records

    def interrupt_running(self):
        for record in self.records():
            if record['status'] == 'running':
                self.update(record['id'], status='recovery_required', stage='recovery_required',
                            interrupted_at=now(), failed_stage=record['stage'],
                            error='Il servizio di aggiornamento è stato interrotto.',
                            recovery='Le migrazioni non vengono ripetute automaticamente. Se esiste una transazione in sospeso, usa recover-upgrade con il backup associato; altrimenti riavvia la versione installata e usa reconcile-updates per verificarla prima di un nuovo tentativo.')

    def interrupted(self):
        return [record for record in self.records()
                if record['status'] == 'recovery_required' and record.get('interrupted_at')]

    def requiring_recovery(self):
        return [record for record in self.records() if record['status'] == 'recovery_required']
