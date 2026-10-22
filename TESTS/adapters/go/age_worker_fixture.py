"""Shared disposable current-Django-schema age expectations, not migration proof."""
import contextlib
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
KIND = 'update_age_of_associates'


def initialize_current_schema(work, host, port, database, log):
    if not database.startswith('assozeta_contract_'):
        raise ValueError('Private contract database required')
    sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
    import environ
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    environment = {k: v for k, v in os.environ.items()
                   if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    os.environ.clear()
    os.environ.update(environment)
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings',
        TESTS_DATABASE=database, TESTS_WORKDIR=str(work), DBTYPE='POSTGRES', DBHOST=str(host), DBPORT=str(port),
        DBNAME=database, DBUSER='contracts', DBPASSWORD='private-worker-password-canary',
        USE_PGBOUNCER='False', STORE_ON_DISK='True', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
        JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()).decode(),
        JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    import django
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), patch('environ.Env.read_env'):
        django.setup()
        from django.apps import apps
        from django.conf import settings
        from django.core.management import call_command
        settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
        call_command('migrate', interactive=False, verbosity=0, run_syncdb=True, stdout=log, stderr=log)
        from application.audit_resolvers import setup_audit_resolvers
        setup_audit_resolvers()


class AgeFixture:
    """Nine explicit scope controls, resettable without resetting the job queue."""
    def __init__(self):
        from application.models import User, SportAssociation, Group, Associate
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        from django.contrib.contenttypes.models import ContentType
        self.ct = ContentType.objects.get_for_model(Associate)
        self.owners = [UUID(int=80001 + i) for i in range(2)]
        self.associations = [UUID(int=80011 + i) for i in range(2)]
        self.groups = [UUID(int=80021 + i) for i in range(2)]
        User.original_objects.bulk_create([User(user_id=pk, username=f'age-worker-{i}', role=1,
            password='!private-fixture') for i, pk in enumerate(self.owners)])
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=pk,
            user_id=self.owners[i], denomination='Private age fixture', tax_code='00000000000',
            deleted=bool(i)) for i, pk in enumerate(self.associations)])
        Group.objects.bulk_create([Group(group_id=pk, sport_association_id=self.associations[i],
            name='Private age group') for i, pk in enumerate(self.groups)])
        self.sentinel = LogEntry.objects.bulk_create([LogEntry(content_type=ContentType.objects.get_for_model(User),
            object_pk=str(self.owners[0]), object_repr='Unrelated age worker sentinel', action=1, changes={})])[0]
        AuditLogIndex.objects.create(log_entry=self.sentinel, sport_association_id=self.associations[0],
                                    resolution_path='sentinel', created_at=datetime.now(timezone.utc))

    def seed(self):
        from application.models import Associate
        from auditlog.models import LogEntry
        from auditlog.context import disable_auditlog
        self.today = datetime.now(timezone.utc).date()
        self.started = datetime.now(timezone.utc)
        # days, minor, deleted, disabled, draft, association, expected promotion
        controls = [(None, True, False, False, False, 0, False),
                    (6574, True, False, False, False, 0, False),
                    (6575, True, False, False, False, 0, True),
                    (6576, True, False, True, False, 1, True),
                    (40000, True, False, False, True, None, True),
                    (6575, True, True, False, False, 0, False),
                    (7000, False, False, False, False, 0, False),
                    (-100, True, False, False, False, 0, False),
                    (0, True, False, False, False, 1, False)]
        self.records = []
        with disable_auditlog():
            Associate._base_manager.all().delete()
            LogEntry.objects.filter(content_type=self.ct).delete()
        rows = []
        for i, (days, minor, deleted, disabled, draft, association, changed) in enumerate(controls):
            pk = UUID(int=80100+i)
            self.records.append((pk, association, changed))
            rows.append(Associate(associate_id=pk, born_date=None if days is None else self.today-timedelta(days=days),
                is_minor=minor, deleted=deleted, disabled=disabled, draft=draft,
                sport_association_id=None if association is None else self.associations[association],
                group_id=None if association is None else self.groups[association],
                first_name='Age', last_name='Fixture', notes='preserve', creation_date=self.started))
        Associate._base_manager.bulk_create(rows)
        return self.snapshot()

    def snapshot(self):
        from django.apps import apps
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    def assert_effect(self, before):
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        assert datetime.now(timezone.utc).date() == self.today, 'Fixture crossed UTC date boundary; recapture required'
        after = self.snapshot()
        promoted = {pk for pk, _, changed in self.records if changed}
        expected = [dict(row, is_minor=False) if row['associate_id'] in promoted else row
                    for row in before['application.Associate']]
        assert after['application.Associate'] == expected, 'Wrong age/scope or unrelated field mutation'
        logs = list(LogEntry.objects.filter(content_type=self.ct))
        assert len(logs) == 3 and {entry.object_pk for entry in logs} == {str(pk) for pk in promoted}
        for entry in logs:
            association = next(association for pk, association, _ in self.records if str(pk) == entry.object_pk)
            assert entry.action == 1 and entry.changes == {'is_minor': ['True', 'False']}
            assert entry.object_repr == f'Associate object ({entry.object_pk})' and entry.changes_text == ''
            assert self.started <= entry.timestamp <= datetime.now(timezone.utc)
            assert all(getattr(entry, field) is None for field in ('actor_id', 'actor_email', 'object_id',
                'serialized_data', 'additional_data', 'remote_addr', 'remote_port', 'cid'))
            indexes = list(AuditLogIndex.objects.filter(log_entry=entry))
            assert len(indexes) == int(association is not None)
            if indexes:
                assert indexes[0].sport_association_id == self.associations[association]
                assert indexes[0].resolution_path == 'direct' and indexes[0].created_at == entry.timestamp
        for model in before:
            if model not in ('application.Associate', 'auditlog.LogEntry', 'application.AuditLogIndex'):
                assert before[model] == after[model], 'Unrelated Django model changed: ' + model
        assert [r for r in before['auditlog.LogEntry'] if r['content_type_id'] != self.ct.pk] == [
            r for r in after['auditlog.LogEntry'] if r['content_type_id'] != self.ct.pk]
        assert [r for r in before['application.AuditLogIndex'] if r['log_entry_id'] == self.sentinel.pk] == [
            r for r in after['application.AuditLogIndex'] if r['log_entry_id'] == self.sentinel.pk]
