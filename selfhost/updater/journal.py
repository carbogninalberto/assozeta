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
    def __init__(self, message, code=None, record=None):
        super().__init__(message)
        self.code = code
        self.record = record


def restore_recovery(record):
    """Server steps after a restore stopped half-way: go back to the safety backup."""
    safety = record.get('safety_backup')
    if safety:
        return (f'I dati potrebbero essere incompleti. Dal server ripristina il backup di sicurezza con '
                f'./bin/assozeta restore backups/{safety} --yes --no-backup, poi esegui ./bin/assozeta reconcile-updates.')
    return ('I dati potrebbero essere incompleti. Dal server ripristina l’ultimo backup "Prima del ripristino" con '
            './bin/assozeta restore backups/<nome> --yes --no-backup, poi esegui ./bin/assozeta reconcile-updates.')


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
            record, _ = self.create_in(db, request, blocked_reason=blocked_reason)
            return record

    def create_in(self, db, request, blocked_reason=None, status='queued', stage='queued'):
        """Admit or replay within the caller's IMMEDIATE transaction."""
        existing = db.execute('SELECT record FROM operations WHERE request_id=?', (request['request_id'],)).fetchone()
        if existing:
            record = json.loads(existing[0])
            if request.get('kind') == 'restart':
                keys = ('actor_id',)
            elif request.get('kind') == 'go_tasks':
                keys = ('actor_id', 'enabled')
            elif request.get('kind') == 'runtime_switch':
                keys = ('actor_id', 'target', 'expected_generation')
            else:
                keys = ('actor_id', 'release_id', 'tag')
            if record.get('kind', 'update') != request.get('kind', 'update') or any(record[key] != request[key] for key in keys):
                raise Conflict('This request identifier was already used for a different update.', 'idempotency_conflict', record)
            return record, True
        if blocked_reason:
            raise Conflict(blocked_reason, 'recovery_required')
        recovery = db.execute("SELECT record FROM operations WHERE status='recovery_required' LIMIT 1").fetchone()
        if recovery:
            raise Conflict('Un aggiornamento richiede una verifica di ripristino prima di un nuovo tentativo.',
                           'recovery_required', json.loads(recovery[0]))
        active = db.execute("SELECT record FROM operations WHERE status IN ('queued', 'running')").fetchone()
        if active:
            raise Conflict('An update is already in progress.', 'operation_in_progress', json.loads(active[0]))
        if (request.get('kind') == 'runtime_switch' and 'source_generation' in request
                and request['source_generation'] != request['expected_generation']):
            raise Conflict('La generazione richiesta non è più attuale.', 'stale_generation')
        record = {
            **request, 'id': str(uuid4()), 'status': status, 'stage': stage,
            'target_version': request.get('tag'), 'created_at': now(), 'updated_at': now(),
            'error': None, 'recovery': None,
        }
        db.execute('INSERT INTO operations VALUES (?, ?, ?, ?)', (record['id'], request['request_id'], status, json.dumps(record)))
        return record, False

    def update(self, operation_id, *, if_status=None, **changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM operations WHERE id=?', (operation_id,)).fetchone()
            if not row:
                raise KeyError(operation_id)
            record = json.loads(row[0])
            if if_status is not None and record['status'] != if_status:
                return None
            record.update(changes, updated_at=now())
            db.execute('UPDATE operations SET status=?, record=? WHERE id=?', (record['status'], json.dumps(record), operation_id))
            return record

    def records(self, *statuses):
        query = 'SELECT record FROM operations'
        if statuses:
            query += ' WHERE status IN (' + ','.join('?' for _ in statuses) + ')'
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute(query + ' ORDER BY rowid DESC', statuses)]

    def interrupt_running(self):
        for record in self.records():
            if record['status'] == 'running' and record.get('kind') == 'backup':
                # A backup changes no data: report it as failed, not as a recovery.
                self.update(record['id'], status='failed', stage='failed', interrupted_at=now(),
                            error='Il backup è stato interrotto.',
                            recovery='Se l’app non torna disponibile, avviala dal server con ./bin/assozeta start.')
            elif record['status'] == 'running' and record.get('kind') == 'restore':
                if record['stage'] in ('queued', 'checking', 'backup'):
                    # Interrupted before the data were replaced.
                    self.update(record['id'], status='failed', stage='failed', interrupted_at=now(),
                                failed_stage=record['stage'], error='Il ripristino è stato interrotto prima di modificare i dati.',
                                recovery='Se l’app non torna disponibile, avviala dal server con ./bin/assozeta start.')
                else:
                    self.update(record['id'], status='recovery_required', stage='recovery_required',
                                interrupted_at=now(), failed_stage=record['stage'],
                                error='Il ripristino è stato interrotto.', recovery=restore_recovery(record))
            elif record['status'] in ('queued', 'running') and record.get('kind') == 'fetch':
                # The recovery key lived only in memory: a fetch cannot resume.
                self.update(record['id'], status='failed', stage='failed', interrupted_at=now(),
                            error='Il recupero dalla copia remota è stato interrotto. Riprova.')
            elif record['status'] == 'running' and record.get('kind') == 'go_tasks':
                # The configuration is saved and the CLI restart is idempotent.
                self.update(record['id'], status='failed', stage='failed', interrupted_at=now(),
                            error='La modifica delle attività in background su Go è stata interrotta.',
                            recovery='Se l’app non torna disponibile, esegui ./bin/assozeta restart dal server.')
            elif record['status'] == 'running' and record.get('kind') not in ('restart', 'runtime_switch'):
                self.update(record['id'], status='recovery_required', stage='recovery_required',
                            interrupted_at=now(), failed_stage=record['stage'],
                            error='Il servizio di aggiornamento è stato interrotto.',
                            recovery='Le migrazioni non vengono ripetute automaticamente. Se esiste una transazione in sospeso, usa recover-upgrade con il backup associato; altrimenti riavvia la versione installata e usa reconcile-updates per verificarla prima di un nuovo tentativo.')

    def interrupted(self):
        return [record for record in self.records()
                if record['status'] == 'recovery_required' and record.get('interrupted_at')]

    def requiring_recovery(self):
        return [record for record in self.records() if record['status'] == 'recovery_required']
