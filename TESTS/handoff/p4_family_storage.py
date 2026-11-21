"""Matrix family storage: save_to_storage, export_invoices_to_zip (task-coverage.md, Broker consumers).

Boundary: the matrix stack with a private MinIO (P4MatrixStack, need 'storage': bucket 'handoff-p4',
location 'private') shared by BOTH runtimes, as in production: the Django worker runs with the production
S3Boto3Storage (path-style, no ACL, AWS_S3_FILE_OVERWRITE False) on that bucket, the Go consumer with the
same AWS_* keys. Seeds reuse TESTS/adapters/go/celery_consumer_print_process.py (owners, association,
Documents and their source objects).

Scenarios (every one ends with the identity oracle and the task's record in task-handoff-dispositions.json):
- <task>__django / __go / __queued_forward / __queued_reverse (p4_matrix_kit boundaries; Go producer = the
  admitted envelope document_render.go / misc_exports.go publish: save_to_storage args [file, base64],
  export_invoices_to_zip args [files, association uuid]).
  save_to_storage: exactly one new object at private/<file_path>, body byte-identical to the decoded PDF.
  export_invoices_to_zip: one new Document '<Rome YYYY-MM-DD_HH-MM>_ricevute.zip', one
  SportAssociationDocumentsArchive for the association, the ZIP object at
  private/<creation timestamp>/<document id>/<filename> with entries
  ['1_ricevuta 1.pdf', '3_ricevuta-2.pdf', '4_ricevuta è.pdf'] whose bytes equal the source objects.
- save_to_storage__taken_name_{django,go}: the target name exists; the original is untouched and exactly one
  alternative object (Django get_available_name: '_<7 chars>' before '.pdf') holds the bytes. Today Go hands
  this input back to Django (consumer_save_to_storage.go); in exclusive Go mode it must finish on Go.
- save_to_storage__large_payload_{django,go}: contents above the S3 multipart threshold (9 MiB) are stored
  byte-identical (md5), no incomplete multipart upload left. Today Go hands this back.
- save_to_storage__partial_upload_{forward,reverse}: NEW SEAM barrier 'storage:part_uploaded' (key = task
  id) after the first part of a multipart upload of a task, before completing it, in both runtimes
  (Django: the storage backend's multipart transfer of save_to_storage; Go: the consumer's S3 writer). The
  source executor is killed there; after the switch the target runs the job once: the final object is
  byte-identical, list_multipart_uploads is empty (partial cleanup), the result is stable (SUCCESS once).

Shared harness for the consumer families (imported by p4_family_workflows/print/association_data):
  django_worker(stack): a prefork Django worker on TESTS.handoff.p4_django_settings with the S3 default
    storage, a Redis cache, CURRENT_HOST = the stack's recording stub and RUNNING_VERSION overridden in
    the worker process (kit proposal: p4_django_settings should honour ASSOZETA_HANDOFF_CURRENT_HOST and
    S3 storage when the 'storage' need is selected, then this bootstrap is unnecessary).
  go_executors(stack): Go worker + consumer with CURRENT_HOST and RUNNING_VERSION in the consumer env file.
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
import uuid
from types import SimpleNamespace

from TESTS.handoff.p3_stack import GO_QUEUE, ROOT, ContractFailure, check, wait_for
from TESTS.handoff.p4_matrix_kit import (await_done, children_of, exclusive_go, identity, produce_django, produce_go_task,
                                          require_matrix_prerequisites, require_record)

FAMILY = 'storage'
LOCATION = 'private'
VERSION = 'v9.8.7-restore'
BOOT = r'''
import json, os, sys
from django.conf import settings
for key, value in json.loads(os.environ['P4_DJANGO_OVERRIDES']).items():
    setattr(settings, key, value)
import core.settings
core.settings.CURRENT_HOST = os.environ['P4_CURRENT_HOST']
from core.celery import app
app.conf.worker_pool = 'prefork'
app.worker_main(['worker', *sys.argv[1:]])
'''


# -- shared consumer-family harness --------------------------------------------------------------------------
class Rig:
    """Per-stack test services of the consumer families: the recording CURRENT_HOST stub."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        rig = self
        self.requests, self.lock = [], threading.Lock()
        pdf = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self):
                with rig.lock:
                    rig.requests.append({'path': self.path, 'authorization': self.headers.get('Authorization'),
                                         'agent': self.headers.get('User-Agent', '')})
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Length', str(len(pdf)))
                self.end_headers()
                self.wfile.write(pdf)

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def take(self):
        with self.lock:
            taken, self.requests = self.requests, []
        return taken

    def seen(self):
        with self.lock:
            return list(self.requests)


def rig(stack):
    if getattr(stack, 'p4_rig', None) is None:
        stack.p4_rig = Rig()
    return stack.p4_rig


def s3(stack):
    if not stack.storage_endpoint:
        raise RuntimeError('the storage families need the private MinIO of P4MatrixStack (Docker + the MinIO image)')
    import boto3
    from botocore.config import Config
    return boto3.client('s3', endpoint_url=stack.storage_endpoint, aws_access_key_id=stack.storage_access,
                        aws_secret_access_key=stack.storage_secret, region_name='us-east-1',
                        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}, retries={'max_attempts': 2}))


def bucket(stack):
    client = s3(stack)
    objects = {}
    for item in client.list_objects_v2(Bucket=stack.storage_bucket).get('Contents', []):
        objects[item['Key']] = client.get_object(Bucket=stack.storage_bucket, Key=item['Key'])['Body'].read()
    return objects


def clear_bucket(stack):
    client = s3(stack)
    for item in client.list_objects_v2(Bucket=stack.storage_bucket).get('Contents', []):
        client.delete_object(Bucket=stack.storage_bucket, Key=item['Key'])
    for upload in client.list_multipart_uploads(Bucket=stack.storage_bucket).get('Uploads', []):
        client.abort_multipart_upload(Bucket=stack.storage_bucket, Key=upload['Key'], UploadId=upload['UploadId'])


def put(stack, key, body):
    client = s3(stack) if body else s3(stack)  # a fresh client per empty body (MinIO empty-PUT pooling quirk)
    client.put_object(Bucket=stack.storage_bucket, Key=key, Body=body, ContentType='application/octet-stream')


def overrides(stack):
    from TESTS.handoff.p3_stack import REDIS_PASSWORD
    return {'STORAGES': {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                         'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
            'AWS_ACCESS_KEY_ID': stack.storage_access, 'AWS_SECRET_ACCESS_KEY': stack.storage_secret,
            'AWS_STORAGE_BUCKET_NAME': stack.storage_bucket, 'AWS_S3_ENDPOINT_URL': stack.storage_endpoint,
            'AWS_S3_REGION_NAME': 'us-east-1', 'AWS_LOCATION': LOCATION, 'AWS_S3_ADDRESSING_STYLE': 'path',
            'AWS_DEFAULT_ACL': None, 'AWS_S3_FILE_OVERWRITE': False, 'RUNNING_VERSION': VERSION,
            'CACHES': {'default': {'BACKEND': 'django_redis.cache.RedisCache',
                                   'LOCATION': f'redis://127.0.0.1:{stack.redis_port}/1',
                                   'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': REDIS_PASSWORD}}}}


def django_worker(stack):
    import subprocess
    if stack.processes['django']:
        return stack.processes['django'][0]
    s3(stack)
    log = stack.output / f'django-s3-worker-{len(stack.children) + 1}.log'
    stream = log.open('w')
    env = dict(stack.env, ASSOZETA_HANDOFF_APPLICATION='handoff-django-worker', P4_CURRENT_HOST=rig(stack).url,
               P4_DJANGO_OVERRIDES=json.dumps(overrides(stack)), RUNNING_VERSION=VERSION)
    process = subprocess.Popen([sys.executable, '-c', BOOT, '-P', 'prefork', '-c', '1', '-Q', 'celery',
                                '-n', f'p4-consumers-{len(stack.children)}@%h', '--without-heartbeat', '--without-gossip',
                                '--without-mingle', '-l', 'WARNING'], cwd=ROOT / 'BE', env=env, stdout=stream,
                               stderr=subprocess.STDOUT)
    stack.children.append((process, stream, log))
    stack.processes['django'].append(process)
    stack.django_ready()
    return process


def go_executors(stack):
    path = stack.go_consumer_env_file()
    text = path.read_text()
    extra = {'CURRENT_HOST': rig(stack).url, 'CURRENT_HOST_PUPPETEER': rig(stack).url, 'RUNNING_VERSION': VERSION}
    lines = [line for line in text.splitlines() if line.split('=', 1)[0] not in extra]
    path.write_text('\n'.join(lines + [f'{k}={v}' for k, v in extra.items()]) + '\n')
    return stack.executors('go')


def start(stack, runtime):
    return django_worker(stack) if runtime == 'django' else go_executors(stack)


def decoded(stack, task_id):
    """(task name, args, kwargs, headers) of a ledger row's envelope."""
    from kombu.utils.json import loads  # Celery's JSON (uuid/datetime/decimal markers decoded)
    envelope = loads(bytes(stack.work_row(task_id)['envelope']))
    body = loads(base64.b64decode(envelope['body']))
    return envelope['headers']['task'], body[0], body[1], envelope['headers']


def descendants(stack, roots):
    found, frontier = [], list(roots)
    while frontier:
        kids = [k for k in children_of(stack, frontier.pop()) if k not in found and k not in roots]
        found += kids
        frontier += kids
    return found


class Case:
    """seed(stack) -> ctx; produce[runtime](stack, ctx) -> [task ids]; verify(stack, ctx, runtime, roots, children);
    pending(stack, ctx, child id) -> True when that child legitimately stays pending (ETA)."""

    def __init__(self, task, seed, produce, verify, pending=None, timeout=120, child_expectation=None):
        self.task, self.seed, self.produce, self.verify, self.pending, self.timeout = task, seed, produce, verify, pending, timeout
        self.child_expectation = child_expectation


def settle_children(stack, case, ctx, roots, who):
    def done():
        kids = descendants(stack, roots)
        states = {k: (stack.work_row(k) or {}).get('state') for k in kids}
        open_ = [k for k, s in states.items() if s not in ('completed', 'failed') and not (case.pending and case.pending(stack, ctx, k))]
        return None if open_ else (kids or [True])
    try:
        kids = wait_for(done, f'{who}: children of {roots} never finished', timeout=case.timeout)
    except ContractFailure:
        states = {k: (lambda r: (r['task_name'], r['state'], r['retries'], r['eta']))(stack.work_row(k))
                  for k in descendants(stack, roots)}
        raise ContractFailure(f'{who}: children of {roots} never finished: {states}') from None
    return [k for k in kids if k is not True]


def run(stack, case, family, mode):
    """mode: django | go (steady) | forward | reverse (the task queued at the switch, run on the target)."""
    require_matrix_prerequisites(stack)
    source, target = {'django': ('django', None), 'go': ('go', None), 'forward': ('django', 'go'),
                      'reverse': ('go', 'django')}[mode]
    stack.set_authority(source, 1)
    ctx = case.seed(stack)
    if target is None:
        start(stack, source)
    roots = case.produce[source](stack, ctx)
    executor, generation = source, 1
    if target:
        generation = stack.switch(source, 1, target)
        start(stack, target)
        executor = target
    for task_id in roots:
        state = await_done(stack, task_id, f'the {executor} executor', case.timeout)
        check(state == 'completed', f'{case.task} ({mode}): ledger state {state}, expected completed on {executor}')
    kids = settle_children(stack, case, ctx, roots, f'{case.task} ({mode})')
    case.verify(stack, ctx, executor, roots, kids)
    if executor == 'go':
        exclusive_go(stack, roots + kids)
    other = 'go' if executor == 'django' else 'django'
    accepted = {t: 'target_completed' for t in roots}
    if case.child_expectation:  # children that finish later may publish their own children: settle until stable
        seen = {}
        while set(kids) - set(seen):
            for kid in kids:
                if kid not in seen:
                    seen[kid] = case.child_expectation(stack, ctx, kid)
            kids = descendants(stack, roots)
        accepted.update(seen)
    accepted.update({k: (case.child_expectation(stack, ctx, k) if case.child_expectation else
                         'target_pending' if case.pending and case.pending(stack, ctx, k) else 'target_completed') for k in kids})
    identity(stack, accepted, (other if target is None else source, 1), (executor, generation),
             {executor: sum(1 for v in accepted.values() if v == 'target_completed'), other: 0})
    require_record(case.task, family)


def standard(case, family):
    slug = case.task.replace('.', '_')
    return {f'{slug}__{mode if mode in ("django", "go") else "queued_" + mode}': (lambda m: lambda stack: run(stack, case, family, m))(mode)
            for mode in ('django', 'go', 'forward', 'reverse')}


def go_produce(name, args=None, kwargs=None, **options):
    return lambda stack, ctx: [produce_go_task(stack, name, args=(ctx[args] if isinstance(args, str) else args or ()),
                                               kwargs=kwargs or {}, **options)]


def django_produce(name, args=None, kwargs=None, **options):
    return lambda stack, ctx: [produce_django(stack, name, args=(ctx[args] if isinstance(args, str) else args or ()),
                                              kwargs=kwargs or {}, **options)]


# -- storage family ------------------------------------------------------------------------------------------
def print_module():
    from TESTS.adapters.go import celery_consumer_print_process as module
    return module


def seed_print_fixture(stack):
    """The print/storage adapter's database seed, its source objects in the shared bucket."""
    module = print_module()
    clear_bucket(stack)
    fake = SimpleNamespace(s3=s3(stack), client=lambda: s3(stack), stub=SimpleNamespace(take=lambda: []))
    module.BUCKET = stack.storage_bucket
    module.Stack.seed(fake, SimpleNamespace(setup=None))
    rig(stack).take()
    return {'module': module, 'before': bucket(stack)}


PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


def new_objects(stack, ctx):
    after = bucket(stack)
    return {k: v for k, v in after.items() if k not in ctx['before']}, after


def save_seed(stack, path='documenti/2026/ricevuta n.1.pdf', body=PDF):
    ctx = seed_print_fixture(stack)
    ctx.update(path=path, body=body, args=[path, base64.b64encode(body).decode()])
    return ctx


def save_verify(stack, ctx, runtime, roots, kids):
    added, after = new_objects(stack, ctx)
    key = f'{LOCATION}/{ctx["path"]}'
    if ctx.get('taken'):
        check(after.get(key) == ctx['before'][key], f'{runtime}: the taken object was overwritten')
        check(len(added) == 1, f'{runtime}: expected one alternative object, got {sorted(added)}')
        (name, body), = added.items()
        check(re.fullmatch(re.escape(key[:-4]) + r'_[A-Za-z0-9]{7}\.pdf', name) and body == ctx['body'],
              f'{runtime}: alternative name {name}')
    else:
        check(set(added) == {key}, f'{runtime}: save_to_storage wrote {sorted(added)}, expected [{key}]')
        check(hashlib.md5(added[key]).hexdigest() == hashlib.md5(ctx['body']).hexdigest(),
              f'{runtime}: the stored object differs from the decoded payload ({len(added[key])} vs {len(ctx["body"])} bytes)')
    uploads = s3(stack).list_multipart_uploads(Bucket=stack.storage_bucket).get('Uploads', [])
    check(not uploads, f'{runtime}: incomplete multipart uploads left behind: {[u["Key"] for u in uploads]}')
    check(not kids, f'{runtime}: save_to_storage published children {kids}')


SAVE = Case('save_to_storage', save_seed, {'django': django_produce('save_to_storage', 'args'),
                                           'go': go_produce('save_to_storage', 'args')}, save_verify)


def export_args(module):
    return [[str(module.D_FILEPATH), str(module.D_MISSING), str(module.D_TIMESTAMP), str(module.D_EMPTYPATH),
             str(module.U(99))], str(module.ASSOCIATION)]


def zip_seed(stack):
    ctx = seed_print_fixture(stack)
    ctx['args'] = export_args(ctx['module'])
    from docmanager.models import Document
    ctx['documents'] = set(Document.objects.values_list('document_id', flat=True))
    return ctx


def zip_verify(stack, ctx, runtime, roots, kids):
    import io
    import zipfile
    from docmanager.models import Document
    from application.models import SportAssociationDocumentsArchive
    module = ctx['module']
    documents = list(Document.objects.exclude(document_id__in=ctx['documents']))
    check(len(documents) == 1 and re.fullmatch(r'\d{4}-\d\d-\d\d_\d\d-\d\d_ricevute\.zip', documents[0].filename),
          f'{runtime}: one new archive Document, got {[d.filename for d in documents]}')
    document = documents[0]
    archives = list(SportAssociationDocumentsArchive.objects.filter(document_id=document.document_id))
    check(len(archives) == 1 and archives[0].sport_association_id == module.ASSOCIATION,
          f'{runtime}: the archive row links the zip to the association: {archives}')
    added, _ = new_objects(stack, ctx)
    key = f'{LOCATION}/{document.creation_date.timestamp()}/{document.document_id}/{document.filename}'
    check(set(added) == {key}, f'{runtime}: stored objects {sorted(added)}, expected [{key}]')
    entries = zipfile.ZipFile(io.BytesIO(added[key]))
    check(entries.namelist() == ['1_ricevuta 1.pdf', '3_ricevuta-2.pdf', '4_ricevuta è.pdf'],
          f'{runtime}: archive entries {entries.namelist()}')
    sources = module.stored_objects()
    expected = [sources[f'{LOCATION}/exports/src/one.pdf']] + [v for k, v in sources.items()
                                                                 if k.endswith('/ricevuta-2.pdf') or k.endswith('/ricevuta è.pdf')]
    check([entries.read(n) for n in entries.namelist()] == expected, f'{runtime}: archive member bytes differ from the sources')


ZIP = Case('export_invoices_to_zip', zip_seed, {'django': django_produce('export_invoices_to_zip', 'args'),
                                                'go': go_produce('export_invoices_to_zip', 'args')}, zip_verify)


def taken_name(runtime):
    def scenario(stack):
        def seed(stack):
            ctx = save_seed(stack, path='existing/taken.pdf')
            ctx['taken'] = True
            return ctx
        run(stack, Case('save_to_storage', seed, SAVE.produce, save_verify), FAMILY, runtime)
    return scenario


LARGE = os.urandom(9 * 1024 * 1024 + 17)


def large_payload(runtime):
    def scenario(stack):
        run(stack, Case('save_to_storage', lambda s: save_seed(s, 'documenti/grande.pdf', LARGE), SAVE.produce, save_verify,
                        timeout=240), FAMILY, runtime)
    return scenario


def partial_upload(source, target):
    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        ctx = save_seed(stack, 'documenti/parziale.pdf', LARGE)
        process = start(stack, source)
        task_id, = SAVE.produce[source](stack, ctx)
        stack.barrier.hold('storage:part_uploaded', task_id)
        arrival = stack.barrier.wait_arrived('storage:part_uploaded', task_id,
                                             f'the {source} executor in a multipart upload (barrier storage:part_uploaded)',
                                             timeout=120)
        victim = process if source == 'django' else stack.processes['go'][-1]
        stack.kill_tree(victim, arrival)
        stack.barrier.reset()
        stack.processes[source] = []
        generation = stack.switch(source, 1, target)
        start(stack, target)
        check(await_done(stack, task_id, f'the {target} executor', 240) == 'completed', 'the job completes once on the target')
        save_verify(stack, ctx, target, [task_id], descendants(stack, [task_id]))
        identity(stack, {task_id: 'target_completed'}, (source, 1), (target, generation), {target: 1, source: 0})
        check(stack.task_result(task_id) == 'SUCCESS', 'a stable SUCCESS result under the same id')
        require_record('save_to_storage', FAMILY)
    return scenario


SCENARIOS = {**standard(SAVE, FAMILY), **standard(ZIP, FAMILY)}
for _runtime in ('django', 'go'):
    SCENARIOS[f'save_to_storage__taken_name_{_runtime}'] = taken_name(_runtime)
    SCENARIOS[f'save_to_storage__large_payload_{_runtime}'] = large_payload(_runtime)
SCENARIOS['save_to_storage__partial_upload_forward'] = partial_upload('django', 'go')
SCENARIOS['save_to_storage__partial_upload_reverse'] = partial_upload('go', 'django')
