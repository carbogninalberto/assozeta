"""Capture Python's bytes for the instance outbound Go ports.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/instance_outbound_golden.py``
- BE_GO/internal/repository/testdata/release_catalog_pickles.json: release
  feeds (as JSON text pages) and pickle.dumps(fetch_releases-value,
  pickle.DEFAULT_PROTOCOL) built by BE/instance/release_catalog.normalize_release
  on json.loads of the same text (django-redis' serializer), as its SHA-256,
  length and first 2 KiB; covers framing,
  unframed large strings, memo indexes past 255, batches past 1000, integer
  opcodes and shared string objects.
- BE_GO/internal/smtpsend/testdata/django_text.json: Django 5.2's
  EmailMessage(subject, body, formataddr((name, from)), [to]).message()
  .as_bytes(linesep='\\r\\n') with fixed Date/Message-ID, and compat32
  Header(value, header_name=name).encode('\\r\\n', 78) folds.
Only placeholder values; no network, no database.
"""
import hashlib
import json
from pathlib import Path
import pickle
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'BE'))

from instance.release_catalog import normalize_release, version_tuple  # noqa: E402


def release(rid, tag, *, name=None, body='', ready=True, published='2026-02-01T00:00:00Z'):
    assets = [{'name': f'assozeta-selfhost-{tag}.tar.gz', 'state': 'uploaded', 'size': 10},
              {'name': 'assozeta-update.json', 'state': 'uploaded', 'size': 1}] if ready else []
    return {'id': rid, 'tag_name': tag, 'name': name, 'body': body, 'draft': False, 'prerelease': False,
            'published_at': published, 'assets': assets}


def catalog(pages):
    releases = {}
    for text in pages:
        for raw in json.loads(text):
            item = normalize_release(raw)
            if item:
                releases[item['id']] = item
    return sorted(releases.values(), key=lambda item: version_tuple(item['tag']), reverse=True)


FEEDS = {
    'empty': [[]],
    'single': [[release(7, 'v1.0.0', name='', body='')]],
    'shared-strings': [[release(1, 'v1.0.0', name='é', body='é'), release(2, 'v1.0.1', name='€', body='€'),
                        release(3, 'v1.0.2', name='', body=None, published='Z'), release(4, 'v1.0.3', name='Z', body='Z', ready=False)]],
    'integers': [[release(rid, f'v2.{i}.0') for i, rid in enumerate(
        (0, 255, 256, 65535, 65536, 2 ** 31 - 1, 2 ** 31, 2 ** 40, -5, -(2 ** 31), -(2 ** 31) - 1, 2 ** 62))]],
    'long-notes': [[release(10 + n, f'v3.{n}.0', name=f'R{n}', body=('notes %d ' % n) * size)
                    for n, size in enumerate((3000, 1, 4000, 3500))]],
    'memo-past-255': [[release(100 + n, f'v4.{n}.0', name=f'Nome {n}', body=f'Nota {n} ✓') for n in range(100)],
                      [release(300 + n, f'v4.{n}.1', name='', body='') for n in range(60)]],
    'batches-past-1000': [[release(5000 + n, f'v5.{n // 100}.{n % 100}', name='', body='', ready=n % 2 == 0)
                           for n in range(start, start + 100)] for start in range(0, 1100, 100)] + [[]],
    'frame-boundaries': [[release(20 + n, f'v6.{n}.0', body='b' * size) for n, size in enumerate(
        (65530, 65536, 10, 3))]],
    'unicode': [[release(30, 'v7.1٣.0', name='Ünïcødé 🚀', body='línea\nnueva\r\n\ttab "quoted" \\ back'),
                 release(31, 'v7.0.0', name='x' * 255, body='y' * 256)]],
    'duplicates': [[release(40, 'v8.0.0', name='first'), release(41, 'v8.1.0')], [release(40, 'v8.2.0', name='second')]],
}


def releases_golden():
    cases = []
    for name, pages in FEEDS.items():
        texts = [json.dumps(page, ensure_ascii=False) for page in pages]
        data = pickle.dumps(catalog(texts), pickle.DEFAULT_PROTOCOL)
        cases.append({'name': name, 'pages': texts, 'pickle_sha256': hashlib.sha256(data).hexdigest(), 'pickle_length': len(data),
                      'pickle_head_hex': data[:2048].hex()})
    return cases


def email_golden():
    import django
    from django.conf import settings
    settings.configure(EMAIL_USE_LOCALTIME=False, DEFAULT_CHARSET='utf-8')
    django.setup()
    from django.core.mail import EmailMessage
    from email.header import Header
    from email.utils import formataddr
    subject = 'Assozeta — verifica email'
    body = 'Questo messaggio è stato richiesto dal proprietario dell’istanza per verificare la configurazione email.'
    date = 'Mon, 09 Nov 2026 08:43:54 -0000'
    names = ['Seed', '', 'Associazione Più', 'A "q" <x>', 'Back\\slash', 'Prova, Uno', 'à' * 48, 'é' * 10 + ' ' + 'x' * 40,
             'Società "Più"', 'Nome molto lungo ' * 4, 'a.b', 'Semi;colon']
    messages = []
    for i, name in enumerate(names):
        for to, message_id in (('to@example.test', '<179421383461.1059.1089588722576169830@host.example>'),
                               ('t' * 60 + '@example.test', '<179421383461.1059.1@' + 'h' * 70 + '.example>')):
            message = EmailMessage(subject, body, formataddr((name, 'from@example.test')), [to],
                                   headers={'Date': date, 'Message-ID': message_id})
            messages.append({'name': f'{i}-{to[:2]}', 'sender_name': name, 'from_email': 'from@example.test', 'to': to,
                             'message_id': message_id, 'subject': str(Header(subject, 'utf-8').encode()),
                             'bytes': message.message().as_bytes(linesep='\r\n').decode('latin-1')})
    folds = []
    for header, value in (('From', 'x ' * 60), ('Subject', 'a;b;c ' * 20), ('To', 'a@b.c, ' * 15), ('X', '\t'.join(['word'] * 30)),
                          ('Message-ID', '<' + 'm' * 100 + '>'), ('From', 'short <a@b.c>'), ('X', ''), ('X', '  leading spaces ' * 8),
                          ('Subject', 'one-very-long-token-' * 5 + ' tail'), ('X', 'a,b;c d ' * 12)):
        folds.append({'header': header, 'value': value,
                      'folded': Header(value, header_name=header).encode(linesep='\r\n', maxlinelen=78)})
    return {'messages': messages, 'folds': folds}


def main():
    (ROOT / 'BE_GO/internal/repository/testdata').mkdir(exist_ok=True)
    (ROOT / 'BE_GO/internal/repository/testdata/release_catalog_pickles.json').write_text(
        json.dumps({'protocol': pickle.DEFAULT_PROTOCOL, 'cases': releases_golden()}, ensure_ascii=False, indent=1) + '\n')
    (ROOT / 'BE_GO/internal/smtpsend/testdata/django_text.json').write_text(
        json.dumps(email_golden(), ensure_ascii=False, indent=1) + '\n')


if __name__ == '__main__':
    main()
