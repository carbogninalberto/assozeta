"""Matrix family association_data: export_association_data, import_association_data, instance.restore.execute.

task-coverage.md: real database/storage and restore coordination; supported historical archives,
rejection paths that currently hand back, partial work and job progress polling; exclusive restore and
runtime change must serialize even when called from a worker.

Boundary: the matrix stack with the private MinIO shared by both runtimes (p4_family_storage harness: Django
worker with S3 storage, Redis cache, RUNNING_VERSION 'v9.8.7-restore'; Go consumer with the same keys).
Seeds reuse TESTS/adapters/go/celery_consumer_import_process.py (seed_source: a rich association with members,
tutors, tags, subscriptions, payments, invoices, folders, media; export_archive: the Django export of it) and
celery_consumer_restore_process.py (build_archives, Stack.seed: the current association, the queued
DataRestore operation, the uploaded backup), with their TRUNCATE limited to business tables so the ledger
and the authority row survive.

Scenarios (each ends with the identity oracle and the task's record):
- export_association_data__{django,go,queued_forward,queued_reverse}: (Go producer: kwargs sport_association_id,
  user_id as association_transfer_writes.go publishes) one new export Document whose ZIP object holds
  manifest.json and the 5 media members; one send_mail_async child for the owner in the association's tenant
  (parent = the export job), owned by the executing runtime (its delivery is the email family's contract);
  the progress a polling client reads (application.export_progress.get_last_export on the shared Redis cache)
  names the job with status SUCCESS, whichever runtime ran it.
- import_association_data__{django,go,queued_forward,queued_reverse}: (Go producer: kwargs zip_file_path,
  owner_password) the archive is imported into an instance with another association: the source ids of the
  association, its three members and the subscription exist, the result says success, the staged archive
  is deleted and an unrelated object is kept.
- import_association_data__rejected_archive_{django,go}: a truncated (not-a-ZIP) archive -- a rejection path
  Go hands back to Django today -- ends with the Django outcome on either runtime: SUCCESS result with
  success false, nothing imported, the staged archive deleted.
- instance.restore.execute__{django,go,queued_forward,queued_reverse}: (Go producer: args [operation id],
  task id = operation id, timelimit [3600, 3300], as data_restore.go publishes) the queued operation
  completes with one attempt.
- instance.restore.execute__serializes_with_the_switch_{forward,reverse}: the restore is claimed on the
  source (held at claim:granted), admission closes, the restore runs to completion on the source while the
  switch settles; the authority commit happens only after the restore completed (committed_at >
  completed_at) and the target never runs it.
"""
import io
import time
import uuid
import zipfile
from types import SimpleNamespace

from TESTS.handoff.p3_stack import GO_QUEUE, check, wait_for
from TESTS.handoff.p4_family_storage import (LOCATION, Case, bucket, clear_bucket, put, run, s3, start)
from TESTS.handoff.p4_matrix_kit import (await_done, identity, produce_django, produce_go_task, require_matrix_prerequisites,
                                          require_record)

FAMILY = 'association_data'
EXPORT, IMPORT, RESTORE = 'export_association_data', 'import_association_data', 'instance.restore.execute'
STAGED = 'imports/p4-import.zip'


def safe_truncate():
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("""SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables WHERE schemaname = 'public'
            AND tablename NOT IN ('django_migrations', 'django_content_type', 'auth_permission')
            AND tablename NOT LIKE 'assozeta\\_%%' AND tablename NOT LIKE 'handoff\\_test\\_%%'""")
        cursor.execute(f'TRUNCATE {cursor.fetchone()[0]} RESTART IDENTITY CASCADE')


def adapters():
    from TESTS.adapters.go import celery_consumer_import_process as importer
    from TESTS.adapters.go import celery_consumer_restore_process as restorer
    importer.truncate = restorer.truncate = safe_truncate
    return importer, restorer


def upload_media(stack, media):
    for name, body in media.items():
        put(stack, f'{LOCATION}/{name}', body)


# -- export ---------------------------------------------------------------------------------------------
def export_seed(stack):
    importer, _ = adapters()
    clear_bucket(stack)
    importer.seed_source()
    upload_media(stack, importer.MEDIA)
    # The association's own sender over the system SMTP (as in p3 d06_child_during_drain): the notification goes
    # through CommunicationConfiguration.send_email on both runtimes and is counted by the stack's SMTP.
    from communications.models import CommunicationConfiguration
    CommunicationConfiguration.objects.create(sport_association_id=importer.ASSOCIATION, email_smtp_host=None,
                                              email_smtp_port=None, email_smtp_user=None, email_smtp_password=None,
                                              email_sender_name='Esporta', email_encryption='NONE', daily_email_limit=1000,
                                              daily_email_balance=0)
    from docmanager.models import Document
    return {'m': importer, 'documents': set(Document.objects.values_list('document_id', flat=True))}


def export_verify(stack, ctx, runtime, roots, kids):
    from docmanager.models import Document
    m, root = ctx['m'], roots[0]
    created = [d for d in Document.objects.exclude(document_id__in=ctx['documents']) if d.filename.endswith('.zip')]
    check(len(created) == 1, f'{runtime}: one export archive Document, got {[d.filename for d in created]}')
    objects = bucket(stack)
    key = f'{LOCATION}/{created[0].filepath}'
    check(key in objects, f'{runtime}: the export archive object {key} is stored (keys {sorted(objects)[:8]})')
    names = zipfile.ZipFile(io.BytesIO(objects[key])).namelist()
    check('manifest.json' in names and len([n for n in names if n.startswith('files/')]) == len(m.MEDIA),
          f'{runtime}: archive members {names}')
    direct = [k for k in kids if stack.work_row(k)['parent_id'] == root]
    mails = [k for k in direct if stack.work_row(k)['task_name'] == 'core.tasks.send_mail_async']
    check(len(mails) == 1 and len(direct) == 1, f'{runtime}: one notification mail child, got {[stack.work_row(k)["task_name"] for k in direct]}')
    from TESTS.handoff.p4_family_storage import decoded
    _, _, mail, headers = decoded(stack, mails[0])
    check(mail.get('recipient_list') == ['owner@example.test'] and str(mail.get('sport_association_id')) == str(m.ASSOCIATION)
          and headers.get('parent_id') == root, f'{runtime}: the completion notice goes to the owner in the tenant: {mail}')
    from application.export_progress import get_last_export
    last = get_last_export(str(m.ASSOCIATION)) or {}
    check(last.get('task_id') == root and last.get('status') == 'SUCCESS',
          f'{runtime}: the progress a polling client reads (application.export_progress.get_last_export, the shared '
          f'cache) names the job as SUCCESS: {({k: last.get(k) for k in ("task_id", "status")})}')


EXPORT_CASE = Case(EXPORT, export_seed, {
    'django': lambda s, c: [produce_django(s, EXPORT, args=[str(c['m'].ASSOCIATION), str(c['m'].OWNER)])],
    'go': lambda s, c: [produce_go_task(s, EXPORT, kwargs={'sport_association_id': str(c['m'].ASSOCIATION),
                                                           'user_id': str(c['m'].OWNER)})]}, export_verify, timeout=240,
    # Delivering the notice is the email family's contract (send_mail_async); here it is published, owned by the
    # executing runtime and either sent or in its SMTP retry -- never handed to the inactive runtime.
    pending=lambda s, c, k: s.work_row(k)['task_name'] == 'core.tasks.send_mail_async',
    child_expectation=lambda s, c, k: (lambda st: 'target_completed' if st == 'completed' else f'terminal:{st}')(
        wait_for(lambda: (lambda r: r['state'] if r['state'] != 'running' else None)(s.work_row(k)), 'mail attempt settles')))


# -- import ---------------------------------------------------------------------------------------------
def import_target(stack, archive):
    importer, _ = adapters()
    from application.models import User, SportAssociation, PaymentCategory
    safe_truncate()
    User.original_objects.bulk_create([User(user_id=importer.OTHER_OWNER, username='other-owner', role=1, password=importer.PBKDF2,
                                            date_joined=importer.SOURCE_CREATED, email='other@example.test')])
    SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=importer.OTHER, user_id=importer.OTHER_OWNER,
                                                                    denomination='Altra', tax_code='00000000000')])
    PaymentCategory.objects.bulk_create([PaymentCategory(payment_category_id=importer.GLOBAL_CATEGORY,
                                                         name='Entrate e proventi da attività tipiche',
                                                         creation_date=importer.SOURCE_CREATED)])
    clear_bucket(stack)
    put(stack, f'{LOCATION}/unrelated/keep.txt', b'keep')
    put(stack, f'{LOCATION}/{STAGED}', archive)
    return {'m': importer}


def driver_export(stack, association, media):
    """The Django export of `association` in this driver process, with its media in whichever default storage the
    driver's settings select (disk or the stack's bucket)."""
    from django.core.files.storage import default_storage
    from application.services.export_service import AssociationExportService
    clear_bucket(stack)
    upload_media(stack, media)
    document = AssociationExportService(association).export()
    with default_storage.open(document.filepath, 'rb') as stream:
        return stream.read()


def rich_archive(stack):
    if getattr(stack, 'p4_rich_archive', None) is None:
        importer, _ = adapters()
        importer.seed_source()
        stack.p4_rich_archive = driver_export(stack, importer.ASSOCIATION, importer.MEDIA)
    return stack.p4_rich_archive


def import_verify(expect_success):
    def verify(stack, ctx, runtime, roots, kids):
        import json
        from django_celery_results.models import TaskResult
        from application.models import SportAssociation, Associate, Subscription
        m = ctx['m']
        result = json.loads(TaskResult.objects.get(task_id=roots[0]).result)
        check(isinstance(result, dict) and result.get('success') is expect_success, f'{runtime}: import result {result}')
        objects = bucket(stack)
        check(f'{LOCATION}/{STAGED}' not in objects and f'{LOCATION}/unrelated/keep.txt' in objects,
              f'{runtime}: the staged archive is deleted and unrelated objects kept: {sorted(objects)[:8]}')
        imported = SportAssociation.original_objects.filter(sport_association_id=m.ASSOCIATION).exists()
        check(imported is expect_success, f'{runtime}: association imported = {imported}')
        if expect_success:
            members = set(Associate._base_manager.filter(sport_association_id=m.ASSOCIATION).values_list('associate_id', flat=True))
            check(members == {m.U(71), m.U(72), m.U(73)} and Subscription._base_manager.filter(subscription_id=m.U(101)).exists(),
                  f'{runtime}: source ids are preserved: {sorted(members)}')
    return verify


def import_case(archive, success):
    return Case(IMPORT, lambda s: import_target(s, archive(s)), {
        'django': lambda s, c: [produce_django(s, IMPORT, args=[STAGED])],
        'go': lambda s, c: [produce_go_task(s, IMPORT, kwargs={'zip_file_path': STAGED, 'owner_password': ''})]},
        import_verify(success), timeout=240)


IMPORT_CASE = import_case(rich_archive, True)
REJECTED_CASE = import_case(lambda s: rich_archive(s)[:len(rich_archive(s)) // 2], False)


# -- restore --------------------------------------------------------------------------------------------
def restore_seed(stack):
    import redis
    import instance.tasks  # noqa: F401  registers instance.restore.execute in the driver's producer app
    from TESTS.handoff.p3_stack import REDIS_PASSWORD
    _, restorer = adapters()
    original = restorer.export_archive
    restorer.export_archive = lambda: (original(), driver_export(stack, restorer.ASSOCIATION, restorer.MEDIA))[1]
    if getattr(stack, 'p4_restore_archives', None) is None:
        stack.p4_restore_archives = restorer.build_archives()
    restorer.BUCKET = stack.storage_bucket
    clear_bucket(stack)
    fake = SimpleNamespace(s3=s3(stack), cache=redis.Redis(host='127.0.0.1', port=stack.redis_port, db=1, password=REDIS_PASSWORD),
                           archives=stack.p4_restore_archives, fix_defaults=restorer.Stack.fix_defaults)
    restorer.Stack.seed(fake, SimpleNamespace(archive='good', op={}, setup=None))
    return {'m': restorer}


def restore_produce_go(stack, ctx):
    operation = str(ctx['m'].OPERATION)
    import json
    payload = json.loads(stack.envelope(RESTORE, operation, args=[operation], queue=GO_QUEUE, stamp=time.time()))
    payload['headers']['timelimit'] = [3600, 3300]
    raw = json.dumps(payload).encode()
    check(stack.admit('go', operation, RESTORE, GO_QUEUE, raw) == 'accepted', 'admission')
    stack.push(GO_QUEUE, raw)
    return [operation]


def restore_verify(stack, ctx, runtime, roots, kids):
    from instance.models import DataRestore
    operation = DataRestore._base_manager.get(pk=ctx['m'].OPERATION)
    check((operation.state, operation.attempts) == ('completed', 1),
          f'{runtime}: the restore completes with one attempt: {operation.state}/{operation.stage}/{operation.attempts}')


RESTORE_CASE = Case(RESTORE, restore_seed, {
    'django': lambda s, c: [produce_django(s, RESTORE, args=[str(c['m'].OPERATION)], task_id=str(c['m'].OPERATION),
                                           time_limit=3600, soft_time_limit=3300)],
    'go': restore_produce_go}, restore_verify, timeout=300)


def serializes_with_the_switch(source, target):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        ctx = restore_seed(stack)
        operation = str(ctx['m'].OPERATION)
        stack.barrier.hold('claim:granted', operation)
        start(stack, source)
        RESTORE_CASE.produce[source](stack, ctx)
        stack.barrier.wait_arrived('claim:granted', operation, f'the {source} executor claiming the restore')
        record, steps = stack.freeze(source, 1)
        stack.barrier.release('claim:granted', operation)
        check(await_done(stack, operation, f'the {source} executor (restore during the drain)', 300) == 'completed',
              'the admitted restore finishes on the source')
        restore_verify(stack, ctx, source, [operation], [])
        generation = stack.finish_switch(record, steps, stop=list(stack.processes[source]))
        stack.processes[source] = []
        start(stack, target)
        from instance.models import DataRestore
        completed = DataRestore._base_manager.get(pk=ctx['m'].OPERATION).completed_at
        committed = stack.sql('SELECT committed_at FROM public.assozeta_go_job_ownership WHERE singleton=1')[0][0]
        check(completed and committed and committed > completed,
              f'the runtime change committed after the restore completed: {committed} vs {completed}')
        identity(stack, {operation: 'source_completed'}, (source, 1), (target, generation), {source: 1, target: 0})
        require_record(RESTORE, FAMILY)
    return scenario


def standard(case, name=None):
    slug = (name or case.task).replace('.', '_')
    return {f'{slug}__{label}': (lambda mode: lambda stack: run(stack, case, FAMILY, mode))(mode)
            for label, mode in (('django', 'django'), ('go', 'go'), ('queued_forward', 'forward'), ('queued_reverse', 'reverse'))}


SCENARIOS = {**standard(EXPORT_CASE), **standard(IMPORT_CASE), **standard(RESTORE_CASE)}
for _mode in ('django', 'go'):
    SCENARIOS[f'import_association_data__rejected_archive_{_mode}'] = (lambda m: lambda stack: run(stack, REJECTED_CASE, FAMILY, m))(_mode)
SCENARIOS['instance_restore_execute__serializes_with_the_switch_forward'] = serializes_with_the_switch('django', 'go')
SCENARIOS['instance_restore_execute__serializes_with_the_switch_reverse'] = serializes_with_the_switch('go', 'django')
