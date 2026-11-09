"""sync_manuale_corpus and compare_manuale_captures: actual manage.py versus the Go command.

Every case builds one private fixture directory, then runs the actual
``manage.py <command>`` process (production settings, no database access by
the command) and the race-built ``manuale`` Go binary on a fresh copy of that
fixture at the same absolute path, with the same arguments and environment.
Exit status, stdout, the CommandError/argparse message, every file left in
the fixture (bytes, mode, symlink target, and whether its mtime is the seeded
one or was set during the run) and, for sync_manuale_corpus, the requests
received by a per-run local HTTP publisher must be identical. Django must
first meet the authored expectation of each case.

Django's process startup writes resolver and system-check warnings to
stderr; only its CommandError line or argparse error message is compared.
Paths Django ends with a traceback are compared by exit status, stdout and
files; Go prints one fixed line there. The publisher compares method and
path; the user agent differs by runtime and is not compared. Images that the
Go comparison refuses (formats other than PNG, 16-bit samples) are checked
separately: Django succeeds, Go must exit 1 before writing the report.
"""
import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'BE')]
from TESTS.adapters.django.production_runtime import require_production_python

REVISION = 'a' * 40
RELEASE = 'release-fixture'
DAY = 24 * 60 * 60
SEEDED = 1_600_000_000  # mtime of every seeded file (2020-09-13)
DJANGO = f'''
import sys
sys.path[:0] = [{str(ROOT / 'BE')!r}, {str(ROOT)!r}]
from unittest.mock import patch
with patch('environ.Env.read_env'):
    import django
    django.setup()
from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py', *sys.argv[1:]])
'''
SYNC_ERROR = 'Published corpus unavailable or incompatible; previous index retained.'
NOT_PASSED = 'Variance comparison requires passed real captures'
DIFFERENT_HASH = 'Capture contents differ from their recorded hashes'
DRIFTED = 'Screenshots drifted under identical inputs; previous verified manual/index retained.'
TRACEBACK = 'traceback'


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def canonical(value):
    from application.manuale.index import canonical as django_canonical
    return django_canonical(value)


def png(width=4, height=3, mode='RGBA', fill=(10, 20, 30, 255), changes=(), **options):
    from PIL import Image
    image = Image.new('RGBA', (width, height), fill)
    for (x, y), value in changes:
        image.putpixel((x, y), value)
    if mode == 'P':
        image = image.convert('RGB').quantize(colors=options.pop('colors', 4))
    elif mode != 'RGBA':
        image = image.convert(mode)
    buffer = io.BytesIO()
    image.save(buffer, format=options.pop('format', 'PNG'), **options)
    return buffer.getvalue()


def write(path, data, mtime=SEEDED):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    os.utime(path, (mtime, mtime))


# ---------------------------------------------------------------- sync corpus

class Publisher(BaseHTTPRequestHandler):
    routes = {}
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        type(self).requests.append(('GET', self.path))
        status, headers, body = self.routes.get(self.path, (404, {}, b'missing'))
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PublisherServer(ThreadingHTTPServer):
    # Eight concurrent screenshot downloads must not overflow the listen backlog.
    request_queue_size = 64


IMAGES = [png(fill=(200, 10 * i, 30, 255)) for i in range(12)]


def corpus(publisher, images=(0, 1), *, shared=True, metadata=None, chunk_extra=None, top=None, image_extra=None):
    """A sealed release corpus; floats, big integers and non-ASCII text exercise the canonical form."""
    chunks = []
    for number, image in enumerate(images):
        record = {'path': f'images/{number}.png', 'sha256': digest(IMAGES[image]), 'url': f'{publisher}/image-{image}.png'}
        record.update((image_extra or {}).get(number, {}))
        chunk = {'id': f'guide#sezione-{number}', 'page': 'guide', 'section': f'sezione-{number}',
                 'title': f'Sezione {number} – perché', 'text': 'Istruzioni verificate   con «virgolette» e \x7f',
                 'status': 'verified', 'url': 'https://manual.example.test/guide', 'screenshots': [record],
                 'scenario_ids': ['fixture'], 'page_title': 'Guida', 'language': 'it', 'intent': 'read',
                 'audience': 'public', 'features': [], 'evidence': [], 'runtime_dependencies': {}}
        chunk.update((chunk_extra or {}).get(number, {}))
        chunks.append(chunk)
    if shared and len(chunks) > 1:
        chunks[-1]['screenshots'].append(copy.deepcopy(chunks[0]['screenshots'][0]))
    value = {'format': 1, 'embedding': 'italian-tfidf-lsa-v5', 'dependencies': {}, 'chunks': chunks,
             'content_identity': 'd' * 64, 'gaps': [{'page': 'guida', 'chiave': 'é'}],
             'semantics': {'vocabulary': ['verificate', 'ünicode'], 'idf': [1.0, 0.1, 1e-05, 1e16, 2.5e-324],
                           'basis': [[0.30000000000000004, -0.0]], 'vectors': [[123456789012345678901234567890]]},
             'metadata': {'application_revision': REVISION, 'release': RELEASE, 'publication_status': 'published',
                          'code_state': 'committed', 'manual_revision': 'c' * 40, 'manual_state': 'committed'}}
    value['metadata'].update(metadata or {})
    value.update(top or {})
    value['identity'] = digest(canonical({k: v for k, v in value.items() if k != 'identity'}))
    return value


def corpus_path(release=RELEASE):
    from urllib.parse import quote
    return f'/{REVISION}/{quote(release, safe="")}.json'


def sync_case(name, *, status=0, message=None, index='new', args=(), env=None, build=None, images=(0, 1),
              corpus_options=None, routes=None, release=RELEASE, seed=None, raw=None):
    """build(case, base, publisher) may seed files; the publisher serves the corpus and images."""
    return {'command': 'sync', 'name': name, 'status': status, 'message': message, 'index': index, 'args': list(args),
            'env': env or {}, 'build': build, 'images': images, 'corpus_options': corpus_options or {},
            'routes': routes or {}, 'release': release, 'seed': seed, 'raw': raw}


def previous_index(base, publisher, images=(2,), extra_assets=()):
    """An active index whose screenshots leave the corpus, plus their cached files."""
    value = corpus(publisher, images, shared=False)
    write(base / 'cache/index.json', canonical(value) + '\n')
    for image in images:
        write(base / f'cache/assets/{digest(IMAGES[image])}.png', IMAGES[image])
    for name, data in extra_assets:
        write(base / 'cache/assets' / name, data)
    return value


def stale_source(base, publisher):
    write(base / 'source/BE/application/views/fixture.py', 'current source\n')


EVIDENCE = [{'path': 'BE/application/views/fixture.py', 'sha256': digest('current source\n')}]
STALE = [{'path': 'BE/application/views/fixture.py', 'sha256': digest('older source\n')}]

SYNC_CASES = [
    sync_case('fresh cache: corpus, deduplicated screenshots and canonical index'),
    sync_case('nested index directory is created', env={'MANUAL_INDEX_PATH': 'cache/nested/deeper/index.json'}),
    sync_case('cached screenshot with the verified bytes is reused',
              build=lambda base, publisher: write(base / f'cache/assets/{digest(IMAGES[0])}.png', IMAGES[0])),
    sync_case('cached screenshot with different bytes is downloaded again',
              build=lambda base, publisher: write(base / f'cache/assets/{digest(IMAGES[0])}.png', IMAGES[5])),
    sync_case('cached screenshot with the verified digest but no PNG header',
              status=1, message=SYNC_ERROR, index='absent', images=(11,),
              build=lambda base, publisher: write(base / f'cache/assets/{digest(IMAGES[11])}.png', IMAGES[11])),
    sync_case('previous screenshots are retired, old unreferenced ones pruned, foreign files kept',
              build=lambda base, publisher: previous_index(base, publisher, (2, 3), [
                  (digest(b'unreferenced') + '.png', b'unreferenced'), ('notes.png', b'kept'),
                  (digest(b'recent') + '.png', b'recent'), (digest(IMAGES[1]) + '.txt', b'other suffix')]),
              seed={'recent': True}),
    sync_case('unreadable previous index is not retired', build=lambda base, publisher: (
        write(base / 'cache/index.json', '{"format": 1'), write(base / f'cache/assets/{digest(IMAGES[4])}.png', IMAGES[4]))),
    sync_case('corpus source not configured', status=1, message=SYNC_ERROR, index='absent',
              env={'MANUAL_CORPUS_BASE_URL': ''}),
    sync_case('plain HTTP publisher without a manual run', status=1, message=SYNC_ERROR, index='absent',
              env={'ASSOZETA_MANUAL_RUN_ID': ''}),
    sync_case('publisher base URL with a query', status=1, message=SYNC_ERROR, index='absent',
              env={'MANUAL_CORPUS_BASE_URL': '{publisher}?release=1'}),
    sync_case('publisher base URL with credentials', status=1, message=SYNC_ERROR, index='absent',
              env={'MANUAL_CORPUS_BASE_URL': 'http://user:secret@127.0.0.1:{port}'}),
    sync_case('trailing slashes are removed from the base URL', env={'MANUAL_CORPUS_BASE_URL': '{publisher}///'}),
    sync_case('application revision is not a commit', status=1, message=SYNC_ERROR, index='absent',
              env={'MANUAL_APPLICATION_REVISION': 'A' * 40}),
    sync_case('release name is quoted in the corpus path', release='release fixture/1~ü+x',
              env={'RUNNING_VERSION': 'release fixture/1~ü+x'}, corpus_options={'metadata': {'release': 'release fixture/1~ü+x'}}),
    sync_case('publisher error keeps the previous index', status=1, message=SYNC_ERROR, index='unchanged',
              build=lambda base, publisher: previous_index(base, publisher), routes={'corpus': (503, {}, b'down')}),
    sync_case('corpus that is not JSON', status=1, message=SYNC_ERROR, index='absent', raw=b'<html>not json</html>'),
    sync_case('corpus identity does not match its content', status=1, message=SYNC_ERROR, index='unchanged',
              build=lambda base, publisher: previous_index(base, publisher), corpus_options={'tamper': True}),
    sync_case('unsupported corpus format', status=1, message=SYNC_ERROR, index='absent', corpus_options={'top': {'format': 2}}),
    sync_case('draft publication', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'metadata': {'publication_status': 'draft'}}),
    sync_case('working-tree implementation evidence', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'metadata': {'code_state': 'working-tree'}}),
    sync_case('generated manual state', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'metadata': {'manual_state': 'generated-from-committed'}}),
    sync_case('manual revision is not a commit', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'metadata': {'manual_revision': 'c' * 39}}),
    sync_case('corpus for another application revision', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'metadata': {'application_revision': 'b' * 40}}),
    sync_case('corpus for another release', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'metadata': {'release': 'release-other'}}),
    sync_case('conflicting implementation evidence hashes', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'chunk_extra': {0: {'evidence': EVIDENCE}, 1: {'evidence': STALE}}}),
    sync_case('implementation evidence matches the source root', build=stale_source,
              env={'MANUAL_SOURCE_ROOT': 'source'}, corpus_options={'chunk_extra': {0: {'evidence': EVIDENCE}}}),
    sync_case('stale implementation evidence keeps the previous index', status=1, message=SYNC_ERROR, index='unchanged',
              build=lambda base, publisher: (stale_source(base, publisher), previous_index(base, publisher)),
              env={'MANUAL_SOURCE_ROOT': 'source'}, corpus_options={'chunk_extra': {0: {'evidence': STALE}}}),
    sync_case('stale evidence is not checked without a source root', corpus_options={'chunk_extra': {0: {'evidence': STALE}}}),
    sync_case('screenshot digest is not SHA-256', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'image_extra': {1: {'sha256': 'E' * 64}}}),
    sync_case('screenshot URL outside the allowed publishers', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'image_extra': {1: {'url': 'http://manual.example.test/image.png'}}}),
    sync_case('screenshot path is not a PNG', status=1, message=SYNC_ERROR, index='absent',
              corpus_options={'image_extra': {0: {'path': 'images/0.webp'}}}),
    sync_case('downloaded screenshot differs from its digest', status=1, message=SYNC_ERROR, index='unchanged',
              build=lambda base, publisher: previous_index(base, publisher), routes={'/image-1.png': (200, {}, IMAGES[6])}),
    sync_case('downloaded screenshot is missing', status=1, message=SYNC_ERROR, index='absent',
              routes={'/image-1.png': (404, {}, b'')}),
    sync_case('downloaded screenshot without a PNG header', status=1, message=SYNC_ERROR, index='absent', images=(0, 11)),
    sync_case('redirect inside the local publisher is followed',
              routes={'corpus-redirect': True, '/image-0.png': (302, {'Location': '/moved-0.png'}, b'')}),
    sync_case('redirect to another publisher is refused', status=1, message=SYNC_ERROR, index='absent',
              routes={'/image-0.png': (302, {'Location': 'http://manual.example.test/image-0.png'}, b'')}),
    sync_case('screenshots install in batches of eight; a second-batch failure stops promotion',
              status=1, message=SYNC_ERROR, index='absent', images=tuple(range(10)),
              routes={'/image-9.png': (500, {}, b'')}),
    sync_case('ten distinct screenshots in two batches', images=tuple(range(10))),
    sync_case('extra argument', status=2, message='unrecognized arguments: extra', index='absent', args=['extra']),
]
IMAGES[11] = b'not a png but referenced by digest'


def sync_fixture(case, base, publisher):
    options = dict(case['corpus_options'])
    tamper = options.pop('tamper', False)
    value = corpus(publisher, case['images'], **options)
    if tamper:
        value['chunks'][0]['text'] = 'Istruzioni modificate'
    routes = {f'/image-{i}.png': (200, {}, IMAGES[i]) for i in range(len(IMAGES))}
    routes['/moved-0.png'] = (200, {}, IMAGES[0])
    body = case['raw'] if case['raw'] is not None else json.dumps(value).encode()
    routes[corpus_path(case['release'])] = (200, {}, body)
    for key, response in case['routes'].items():
        if key == 'corpus':
            routes[corpus_path(case['release'])] = response
        elif key == 'corpus-redirect':
            routes['/redirected.json'] = routes[corpus_path(case['release'])]
            routes[corpus_path(case['release'])] = (301, {'Location': '/redirected.json'}, b'')
        else:
            routes[key] = response
    (base / 'cache').mkdir(parents=True, exist_ok=True)
    if case['build']:
        case['build'](base, publisher)
    if case['seed'] and case['seed'].get('recent'):
        recent = time.time() - DAY / 2
        os.utime(base / f'cache/assets/{digest(b"recent")}.png', (recent, recent))
    return value, routes


# ------------------------------------------------------------ compare captures

INPUT_VALUES = {'application_revision': REVISION, 'source_hashes': {'BE/a.py': 'a' * 64}, 'tooling_hashes': {},
                'fixture_version': 3, 'reference_date': '2026-01-15', 'viewport': {'width': 1920, 'height': 1080},
                'locale': 'it-IT', 'timezone': 'Europe/Rome', 'theme': 'light', 'device_scale_factor': 1,
                'backend': 'real', 'fixture_profile': 'standard', 'capture_format': 'full-hd-v1'}


def report(scenario, shots, **fields):
    """shots: [(path, bytes, master_path or None, master bytes)]."""
    value = {'id': scenario, 'status': 'passed', **copy.deepcopy(INPUT_VALUES), 'screenshots': []}
    for path, data, master, master_data in shots:
        record = {'path': path, 'sha256': digest(data), 'width': 4, 'height': 3}
        if master:
            record['master'] = {'path': master, 'sha256': digest(master_data)}
        value['screenshots'].append(record)
    value.update(fields)
    return value


def captures(base, scenario, old_shots, new_shots, old_fields=None, new_fields=None, raw_old=None, raw_new=None):
    """Writes the retained baseline bundle and the current run for one scenario."""
    for root, shots, fields, raw in [(base / 'run/capture-baseline', old_shots, old_fields, raw_old),
                                     (base / 'run', new_shots, new_fields, raw_new)]:
        for path, data, master, master_data in shots:
            write(root / 'captures' / path, data)
            if master:
                write(root / master, master_data)
        text = raw if raw is not None else json.dumps(report(scenario, shots, **(fields or {})), ensure_ascii=False, indent=1)
        write(root / f'{scenario}.json', text)


BASE = png(40, 25)
SHOT = [('scenario/one.png', BASE, 'masters/scenario/one.png', png(40, 25, fill=(1, 2, 3, 255)))]


def drift(pixels, width=40, height=25, value=(250, 0, 0, 255), **options):
    return png(width, height, changes=[((i % width, i // width), value) for i in range(pixels)], **options)


def compare_case(name, build, *, status=0, message=None, entries=None, report_status='passed', args=None,
                 refused=False, output='variance.json'):
    return {'command': 'compare', 'name': name, 'build': build, 'status': status, 'message': message,
            'entries': entries, 'report_status': report_status, 'args': args, 'refused': refused, 'output': output}


def only(path, data):
    return [(path, data, None, None)]


FULL_HD = png(1920, 1080, mode='RGB', fill=(240, 240, 240, 255))
FULL_HD_DRIFT = png(1920, 1080, mode='RGB', fill=(240, 240, 240, 255),
                    changes=[((x, y), (20, 90, 200, 255)) for x in range(100, 150) for y in range(300, 340)])


def symlink_inside(base):
    captures(base, 'linked', only('linked/a.png', BASE), only('linked/a.png', BASE))
    target = base / 'run/captures/linked/a.png'
    write(base / 'run/captures/linked/real.png', target.read_bytes())
    target.unlink()
    target.symlink_to('real.png')


def symlink_outside(base):
    captures(base, 'escape', only('escape/a.png', BASE), only('escape/a.png', BASE))
    write(base / 'outside.png', BASE)
    target = base / 'run/captures/escape/a.png'
    target.unlink()
    target.symlink_to(base / 'outside.png')


def several(base):
    captures(base, 'b-second', only('b/x.png', BASE), only('b/x.png', drift(1)))
    captures(base, 'a-first', SHOT, SHOT)
    captures(base, '.hidden', only('h/x.png', BASE), only('h/x.png', BASE))


def missing_artifact(base, identical):
    fields = None if identical else {'theme': 'dark'}
    captures(base, 'gone', only('gone/a.png', BASE) + only('gone/b.png', BASE), only('gone/a.png', BASE), new_fields=fields)


NAN_INPUTS = '{"id": "nan", "status": "passed", "theme": NaN, "screenshots": []}'


def equality(base, old_fields, new_fields, current=drift(30)):
    captures(base, 'inputs', only('i/a.png', BASE), only('i/a.png', current), old_fields=old_fields, new_fields=new_fields)


def missing_input(base):
    equality(base, {'locale': None}, {})
    current = json.loads((base / 'run/inputs.json').read_text())
    del current['locale']
    write(base / 'run/inputs.json', json.dumps(current))


def odd_identity(base):
    raw = ('{"id": {"b": 1, "a": [1.5e-7, 1e16, -0.0, 12345678901234567890123, Infinity, 0.1, 100.0], "b": "è😀\\u0007"},'
           ' "status": "passed", "screenshots": [{"path": "odd/a.png", "sha256": "%s"}]}' % digest(BASE))
    for root in (base / 'run/capture-baseline', base / 'run'):
        write(root / 'captures/odd/a.png', BASE)
        write(root / 'odd.json', raw)


def pure_paths(base):
    shots = [('p//./x.png', BASE, 'masters/p/x.png', BASE)]
    captures(base, 'paths', shots, [('p/x.png', BASE, 'masters/p/x.png', BASE)])


def modes(base, old, new, old_options=None, new_options=None):
    captures(base, 'modes', only('m/a.png', png(6, 5, mode=old, **(old_options or {}))),
             only('m/a.png', png(6, 5, mode=new, **(new_options or {}))))


TRANSLUCENT = dict(fill=(10, 20, 30, 128))
COMPARE_CASES = [
    compare_case('identical captures and masters', lambda b: captures(b, 'scenario', SHOT, SHOT),
                 entries=[('captures/scenario/one.png', 'stable'), ('masters/scenario/one.png', 'stable')]),
    compare_case('one changed pixel stays within the threshold', lambda b: captures(b, 'small', only('s/a.png', BASE), only('s/a.png', drift(1))),
                 entries=[('captures/s/a.png', 'stable')]),
    compare_case('drift above the threshold under identical inputs', lambda b: captures(b, 'big', only('d/a.png', BASE), only('d/a.png', drift(2))),
                 status=1, message=DRIFTED, report_status='failed', entries=[('captures/d/a.png', 'changed')]),
    compare_case('drift above the threshold with different inputs', lambda b: captures(
        b, 'big', only('d/a.png', BASE), only('d/a.png', drift(40)), new_fields={'application_revision': 'b' * 40}),
                 entries=[('captures/d/a.png', 'changed')]),
    compare_case('Full HD crop drift within the threshold', lambda b: captures(b, 'hd', only('hd/a.png', FULL_HD), only('hd/a.png', FULL_HD_DRIFT)),
                 entries=[('captures/hd/a.png', 'stable')]),
    compare_case('one-level change in a Full HD frame', lambda b: captures(b, 'level', only('hd/a.png', FULL_HD), only('hd/a.png', png(
        1920, 1080, mode='RGB', fill=(240, 240, 240, 255), changes=[((7, 9), (240, 241, 240, 255))]))),
                 entries=[('captures/hd/a.png', 'stable')]),
    compare_case('dimensions changed under identical inputs', lambda b: captures(b, 'dim', only('d/a.png', BASE), only('d/a.png', png(41, 25))),
                 status=1, message=DRIFTED, report_status='failed', entries=[('captures/d/a.png', 'dimensions-changed')]),
    compare_case('checkpoint removed under identical inputs', lambda b: missing_artifact(b, True), status=1, message=DRIFTED,
                 report_status='failed', entries=[('captures/gone/a.png', 'stable'), ('captures/gone/b.png', 'checkpoint-changed')]),
    compare_case('checkpoint removed with different inputs', lambda b: missing_artifact(b, False),
                 entries=[('captures/gone/a.png', 'stable'), ('captures/gone/b.png', 'checkpoint-changed')]),
    compare_case('no retained baseline', lambda b: (b / 'run').mkdir(parents=True), entries=[]),
    compare_case('reports in name order, hidden reports included', several,
                 entries=[('captures/h/x.png', 'stable'), ('captures/scenario/one.png', 'stable'),
                          ('masters/scenario/one.png', 'stable'), ('captures/b/x.png', 'stable')]),
    compare_case('baseline report did not pass', lambda b: captures(b, 'failed', SHOT, SHOT, old_fields={'status': 'failed'}),
                 status=1, message=NOT_PASSED, report_status=None),
    compare_case('current report did not pass', lambda b: captures(b, 'failed', SHOT, SHOT, new_fields={'status': ['passed']}),
                 status=1, message=NOT_PASSED, report_status=None),
    compare_case('capture bytes differ from the recorded hash', lambda b: (
        captures(b, 'hash', only('h/a.png', BASE), only('h/a.png', BASE)), write(b / 'run/captures/h/a.png', drift(1))),
                 status=1, message=DIFFERENT_HASH, report_status=None),
    compare_case('baseline hash mismatch is reported before a missing current file', lambda b: (
        captures(b, 'hash', only('h/a.png', BASE), only('h/a.png', BASE)), write(b / 'run/capture-baseline/captures/h/a.png', drift(1)),
        (b / 'run/captures/h/a.png').unlink()), status=1, message=DIFFERENT_HASH, report_status=None),
    compare_case('current report missing', lambda b: (captures(b, 'gone', SHOT, SHOT), (b / 'run/gone.json').unlink()),
                 status=1, message=TRACEBACK, report_status=None),
    compare_case('capture path escapes the run', lambda b: captures(b, 'up', only('../../outside.png', BASE), only('../../outside.png', BASE)),
                 status=1, message=TRACEBACK, report_status=None),
    compare_case('symlink inside the run is followed', symlink_inside, entries=[('captures/linked/a.png', 'stable')]),
    compare_case('symlink out of the run is refused', symlink_outside, status=1, message=TRACEBACK, report_status=None),
    compare_case('pathlib-normalized capture paths', pure_paths,
                 entries=[('captures/p/x.png', 'stable'), ('masters/p/x.png', 'stable')]),
    compare_case('RGB and opaque RGBA decode to the same pixels', lambda b: modes(b, 'RGB', 'RGBA'), entries=[('captures/m/a.png', 'stable')]),
    compare_case('grayscale and grayscale with alpha', lambda b: modes(b, 'L', 'LA', None, TRANSLUCENT), entries=[('captures/m/a.png', 'changed')],
                 status=1, message=DRIFTED, report_status='failed'),
    compare_case('palette with transparency against RGBA', lambda b: modes(b, 'P', 'RGBA', {'transparency': 0}), entries=[('captures/m/a.png', 'changed')],
                 status=1, message=DRIFTED, report_status='failed'),
    compare_case('two-colour palette at 1 bit', lambda b: modes(b, 'P', 'RGB', {'colors': 2, 'bits': 1}), entries=[('captures/m/a.png', 'stable')]),
    compare_case('RGB colour key transparency', lambda b: modes(b, 'RGB', 'RGB', {'transparency': (10, 20, 30)}),
                 entries=[('captures/m/a.png', 'changed')], status=1, message=DRIFTED, report_status='failed'),
    compare_case('interlaced translucent RGBA', lambda b: captures(b, 'adam7', only('a/a.png', png(9, 9, **TRANSLUCENT)),
                 only('a/a.png', interlaced(png(9, 9, changes=[((4, 4), (1, 1, 1, 1))], **TRANSLUCENT)))),
                 entries=[('captures/a/a.png', 'changed')], status=1, message=DRIFTED, report_status='failed'),
    compare_case('numeric and nested NaN inputs follow Python equality', lambda b: equality(
        b, {'device_scale_factor': 1, 'fixture_version': True, 'viewport': [float('nan'), 2 ** 100]},
        {'device_scale_factor': 1.0, 'fixture_version': 1.0, 'viewport': [float('nan'), float(2 ** 100)]}),
                 entries=[('captures/i/a.png', 'changed')], status=1, message=DRIFTED, report_status='failed'),
    compare_case('a NaN input is unequal at the top level', lambda b: equality(b, {'theme': float('nan')}, {'theme': float('nan')}),
                 entries=[('captures/i/a.png', 'changed')]),
    compare_case('large integer input differs from the nearest float', lambda b: equality(
        b, {'fixture_version': 2 ** 60 + 1}, {'fixture_version': float(2 ** 60 + 1)}), entries=[('captures/i/a.png', 'changed')]),
    compare_case('a missing input equals null', missing_input, entries=[('captures/i/a.png', 'changed')],
                 status=1, message=DRIFTED, report_status='failed'),
    compare_case('scenario identity is written as Python JSON', odd_identity, entries=[('captures/odd/a.png', 'stable')]),
    compare_case('scenario without id and without captures', lambda b: captures(b, 'empty', [], [], new_fields={'id': None},
                 raw_new='{"status": "passed", "screenshots": {}}'), entries=[]),
    compare_case('scenario without id but with captures', lambda b: captures(b, 'noid', only('n/a.png', BASE), only('n/a.png', BASE),
                 raw_new=json.dumps({'status': 'passed', 'screenshots': [{'path': 'n/a.png', 'sha256': digest(BASE)}]})),
                 status=1, message=TRACEBACK, report_status=None),
    compare_case('report that is a list', lambda b: captures(b, 'list', SHOT, SHOT, raw_old='[]'), status=1, message=TRACEBACK, report_status=None),
    compare_case('master that is not an object', lambda b: captures(b, 'master', SHOT, SHOT,
                 raw_new=json.dumps({'id': 'x', 'status': 'passed', 'screenshots': [{'path': 'a.png', 'sha256': 'x', 'master': 'm.png'}]})),
                 status=1, message=TRACEBACK, report_status=None),
    compare_case('screenshot that is not an image', lambda b: captures(b, 'text', only('t/a.png', b'text'), only('t/a.png', b'text')),
                 status=1, message=TRACEBACK, report_status=None),
    compare_case('output directory missing', lambda b: captures(b, 'scenario', SHOT, SHOT), status=1, message=TRACEBACK,
                 report_status=None, output='missing/variance.json'),
    compare_case('required arguments missing', lambda b: (b / 'run').mkdir(parents=True), status=2,
                 message='the following arguments are required: --output', args=['--run', 'run']),
    compare_case('option without its value', lambda b: (b / 'run').mkdir(parents=True), status=2,
                 message='argument --output: expected one argument', args=['--run=run', '--output']),
    compare_case('unrecognized argument', lambda b: (b / 'run').mkdir(parents=True), status=2,
                 message='unrecognized arguments: extra', args=['--run', 'run', '--output', 'variance.json', 'extra']),
    compare_case('JPEG captures are refused by Go', lambda b: captures(b, 'jpeg', only('j/a.png', png(8, 8, mode='RGB', format='JPEG')),
                 only('j/a.png', png(8, 8, mode='RGB', format='JPEG'))), refused=True, entries=[('captures/j/a.png', 'stable')]),
    compare_case('16-bit captures are refused by Go', lambda b: captures(b, 'deep', only('d/a.png', png(8, 8, mode='I;16')),
                 only('d/a.png', png(8, 8, mode='I;16'))), refused=True, entries=[('captures/d/a.png', 'stable')]),
]


def interlaced(data):
    """Re-encode with Adam7 interlacing (PIL cannot write it) using a minimal PNG writer."""
    from PIL import Image
    import struct
    import zlib
    image = Image.open(io.BytesIO(data)).convert('RGBA')
    width, height = image.size
    pixels = image.tobytes()
    raw = b''
    for x0, y0, dx, dy in [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]:
        for y in range(y0, height, dy):
            row = b''.join(pixels[(y * width + x) * 4:(y * width + x) * 4 + 4] for x in range(x0, width, dx))
            if row:
                raw += b'\0' + row

    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 1))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


# -------------------------------------------------------------------- runner

def tree(root, started):
    """Every entry under root: kind, mode, bytes digest or link target, seeded/run mtime."""
    state = {}
    if not root.exists():
        return state
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        relative = str(path.relative_to(root))
        if stat.S_ISLNK(info.st_mode):
            state[relative] = ['link', os.readlink(path)]
        elif stat.S_ISDIR(info.st_mode):
            state[relative] = ['dir', oct(stat.S_IMODE(info.st_mode))]
        else:
            when = 'seeded' if int(info.st_mtime) == SEEDED else 'run' if info.st_mtime >= started - 1 else (
                'recent' if info.st_mtime >= started - DAY else 'old')
            state[relative] = ['file', oct(stat.S_IMODE(info.st_mode)), digest(path.read_bytes()), when]
    return state


def outcome(completed, runtime):
    """The command's own stderr: CommandError text, argparse message, or a traceback marker."""
    lines = completed.stderr.splitlines()
    errors = [line[len('CommandError: '):] for line in lines if line.startswith('CommandError: ')]
    if errors:
        return errors[-1]
    if completed.returncode == 2:
        return lines[-1].split(': error: ', 1)[1] if lines and ': error: ' in lines[-1] else lines
    if completed.returncode:
        if runtime == 'django':
            return TRACEBACK if any(line.startswith('Traceback (most recent call last)') for line in lines) else lines
        return TRACEBACK if len(lines) == 1 and lines[0].startswith('compare-captures failed') else lines
    return None


def run_process(runtime, executable, environment, command, args, cwd):
    django_name = {'sync': 'sync_manuale_corpus', 'compare': 'compare_manuale_captures'}[command]
    go_name = {'sync': 'sync-corpus', 'compare': 'compare-captures'}[command]
    argv = ([sys.executable, '-I', '-c', DJANGO, django_name, *args] if runtime == 'django'
            else [str(executable), go_name, *args])
    return subprocess.run(argv, env=environment, capture_output=True, text=True, timeout=180, cwd=cwd, start_new_session=True)


def run_sync(case, executable, environment, work):
    base = work / 'fixture'
    results = {}
    server = PublisherServer(('127.0.0.1', 0), Publisher)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        publisher = f'http://127.0.0.1:{server.server_port}'
        template = work / 'template'
        shutil.rmtree(template, ignore_errors=True)
        template.mkdir()
        value, routes = sync_fixture(case, template, publisher)
        Publisher.routes = routes
        env = dict(environment, MANUAL_CORPUS_BASE_URL=publisher, MANUAL_APPLICATION_REVISION=REVISION,
                   RUNNING_VERSION=case['release'], MANUAL_INDEX_PATH='cache/index.json', MANUAL_SOURCE_ROOT='',
                   ASSOZETA_MANUAL_RUN_ID='fixture-local-publisher')
        env.update({k: v.format(publisher=publisher, port=server.server_port) for k, v in case['env'].items()})
        index_path = env['MANUAL_INDEX_PATH']
        seeded_index = (template / index_path).read_bytes() if (template / index_path).exists() else None
        for runtime in ('django', 'go'):
            if runtime == 'go' and executable is None:
                return {'case': case['name'], 'django': 'pass', 'go': 'missing command'}
            shutil.rmtree(base, ignore_errors=True)
            shutil.copytree(template, base, symlinks=True)
            # copytree keeps mtimes; the recent asset must stay inside the grace period.
            Publisher.requests = []
            started = time.time()
            completed = run_process(runtime, executable, env, 'sync', case['args'], base)
            index = (base / index_path).read_bytes() if (base / index_path).exists() else None
            results[runtime] = {'status': completed.returncode, 'stdout': completed.stdout,
                                'message': outcome(completed, runtime), 'files': tree(base, started),
                                'requests': sorted(Publisher.requests)}
            if runtime == 'django':
                expected_stdout = f'Published manual corpus updated: {value["identity"]}\n' if case['status'] == 0 else ''
                expected_index = {'new': (canonical(value) + '\n').encode(), 'absent': None, 'unchanged': seeded_index}[case['index']]
                authored = (completed.returncode, completed.stdout, results[runtime]['message'], index)
                assert authored == (case['status'], expected_stdout, case['message'], expected_index), \
                    f'{case["name"]}: Django differs from the authored case: {authored[:3]}'
            elif completed.stderr and results['go']['message'] is None:
                raise AssertionError(f'{case["name"]}: unexpected Go stderr')
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return results


def run_compare(case, executable, environment, work):
    base = work / 'fixture'
    template = work / 'template'
    shutil.rmtree(template, ignore_errors=True)
    template.mkdir()
    case['build'](template)
    args = case['args'] if case['args'] is not None else ['--run', 'run', '--output', case['output']]
    results = {}
    for runtime in ('django', 'go'):
        if runtime == 'go' and executable is None:
            return {'case': case['name'], 'django': 'pass', 'go': 'missing command'}
        shutil.rmtree(base, ignore_errors=True)
        shutil.copytree(template, base, symlinks=True)
        started = time.time()
        completed = run_process(runtime, executable, environment, 'compare', args, base)
        output = base / case['output']
        written = json.loads(output.read_text()) if output.is_file() else None
        results[runtime] = {'status': completed.returncode, 'stdout': completed.stdout,
                            'message': outcome(completed, runtime), 'files': tree(base, started)}
        if runtime == 'django':
            count = len(case['entries'] or [])
            expected_stdout = f'Capture variance measured for {count} checkpoints.\n' if case['status'] == 0 else ''
            entries = [(entry['path'], entry['status']) for entry in written['screenshots']] if written else None
            authored = (completed.returncode, completed.stdout, results[runtime]['message'],
                        written and written['status'], entries)
            want = (case['status'], expected_stdout, case['message'], case['report_status'] if case['status'] != 2 else None,
                    case['entries'] if written or case['entries'] else None)
            if case['status'] == 2 or case['report_status'] is None:
                want = want[:3] + (None, None)
            assert authored == want, f'{case["name"]}: Django differs from the authored case: {authored}'
        elif case['refused']:
            assert (completed.returncode, completed.stdout, written) == (1, '', None), f'{case["name"]}: Go did not refuse'
            assert completed.stderr.startswith('compare-captures failed: capture image format is not supported'), case['name']
            return {'case': case['name'], 'django': 'pass', 'go': 'refused before writing'}
        elif completed.stderr and results['go']['message'] is None:
            raise AssertionError(f'{case["name"]}: unexpected Go stderr')
    return results


def run_case(case, executable, environment, work, output):
    results = (run_sync if case['command'] == 'sync' else run_compare)(case, executable, environment, work)
    if 'case' in results:
        print(f"{'PASS' if results['go'] != 'missing command' else 'RED'}: {case['command']}: {case['name']} ({results['go']})", flush=True)
        return results
    if results['django'] != results['go']:
        (output / 'difference.json').write_text(json.dumps({'case': case['name'], **results}, indent=1, default=str) + '\n')
    assert results['django'] == results['go'], f'{case["name"]}: Go differs from the Django command'
    print(f"PASS: {case['command']}: {case['name']} (exit {case['status']})", flush=True)
    return {'case': case['name'], 'command': case['command'], 'status': case['status'], 'django': 'pass', 'go': 'pass'}


def environment_for(work):
    values = {}
    for line in (ROOT / 'selfhost/.env.example').read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key] = value
    base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR')}
    values.update(base)
    # Placeholders only; the commands never open the database.
    values.update(DJANGO_SETTINGS_MODULE='core.settings', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                  JWT_SECRET_KEY='placeholder-private', JWT_PUBLIC_KEY='placeholder-public', DBHOST='127.0.0.1',
                  DBPORT='1', STORE_ON_DISK='True', DJANGO_COLORS='nocolor', PYTHONUTF8='1',
                  ASSOZETA_DEPLOYMENT_MODE='production', DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='manuale-commands')
    parser.add_argument('--only', choices=['sync', 'compare'])
    parser.add_argument('--case')
    parser.add_argument('--mutate-file')
    parser.add_argument('--old')
    parser.add_argument('--new')
    args = parser.parse_args()
    require_production_python()
    output = ROOT / 'quality-reports/go-migration' / args.label
    output.mkdir(parents=True, exist_ok=True)
    (output / 'difference.json').unlink(missing_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    cases = [case for case in SYNC_CASES + COMPARE_CASES
             if (not args.only or case['command'] == args.only) and (not args.case or args.case in case['name'])]
    mutated = original = None
    if args.mutate_file:
        mutated = ROOT / args.mutate_file
        original = mutated.read_bytes()
        assert original.decode().count(args.old) == 1, 'mutation text must occur exactly once'
        mutated.write_bytes(original.decode().replace(args.old, args.new).encode())
        result['mutation'] = {'file': args.mutate_file, 'old': args.old, 'new': args.new}
    base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    try:
        with tempfile.TemporaryDirectory(prefix='az-manuale-commands-', dir='/tmp') as directory, (output / 'build.log').open('w') as log:
            work = Path(directory).resolve()
            executable = work / 'manuale'
            built = subprocess.run(['go', 'build', '-race', '-o', str(executable), './cmd/manuale'], cwd=ROOT / 'BE_GO',
                                   env=base, stdout=log, stderr=subprocess.STDOUT, timeout=600)
            if mutated:
                mutated.write_bytes(original)
            if built.returncode:
                executable = None  # RED: Django still runs against the authored expectations.
            environment = environment_for(work)
            result['cases'] = []
            failures = []
            for case in cases:
                try:
                    result['cases'].append(run_case(case, executable, environment, work, output))
                except AssertionError as error:
                    failures.append({'case': case['name'], 'error': str(error)})
                    print(f'FAIL: {case["name"]}: {error}', flush=True)
                    if not mutated:
                        raise
            result['failures'] = failures
            result['django_passed'] = sum(c['django'] == 'pass' for c in result['cases'])
            result['go_passed'] = sum(c['go'] in ('pass', 'refused before writing') for c in result['cases'])
            result['go_refused'] = sum(c['go'] == 'refused before writing' for c in result['cases'])
            summary = {'django': f"{result['django_passed']}/{len(cases)}", 'go': f"{result['go_passed']}/{len(cases)}",
                       'go_refused': result['go_refused']}
            if mutated:
                summary['mutation'] = 'CAUGHT' if failures else 'MISSED'
                result['caught'] = bool(failures)
            print(json.dumps(summary), flush=True)
            if not mutated:
                assert result['go_passed'] == len(cases), 'Go command missing or failing'
    finally:
        if mutated:
            mutated.write_bytes(original)
        report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    paths = ['TESTS/adapters/go/manuale_commands.py', 'BE/application/management/commands/sync_manuale_corpus.py',
             'BE/application/management/commands/compare_manuale_captures.py', 'BE/application/manuale/sync.py',
             'BE/application/manuale/index.py', 'BE_GO/cmd/manuale/main.go', 'BE_GO/pkg/manual/refresh.go',
             'BE_GO/pkg/manual/captures.go', 'BE_GO/pkg/manual/pyjson.go', 'BE_GO/pkg/manual/availability.go']
    result.update(success=not mutated, case_count=len(cases), race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths if (ROOT / p).exists()})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'success': result['success'], 'case_count': len(cases)}))


if __name__ == '__main__':
    main()
