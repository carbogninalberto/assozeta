"""Author the docmanager-reads HTTP sequence (docmanager-reads.md).

document/retrieve/<uid> (retrieve_document, BE/docmanager/views/document_view.py),
in Django's order:

- is_valid_uuid(uid): 400 with the ValueError text as a list.
- ?download: only the exact value 'false' selects view_file (no
  Content-Disposition); any other value, or none, selects download_file whose
  Content-Disposition is content_disposition_header(bool(?download), name)
  with commas in the stored filename replaced by spaces.
- Without an impersonation session request.user is recomputed from the
  Authorization header, then the BKN_AUTH cookie, then ?token (SimpleJWT
  JWTAuthentication, no identity resolution); with one it stays the scoped user.
- ?download_token: django.core.signing.loads (salt
  docmanager.association-export-download, max_age 3600); a bad signature,
  wrong purpose, missing ids or another document is 401 'Invalid download
  token.'; then the archive row of the token's association for this document
  whose filename starts with 'export_' (else 404).
- ?token: Document by (uid, token) when the token parses as a UUID.
- Otherwise an authenticated user resolves the uid as a Document id, then a
  Subscription id (GroupAwareManager: X-Group-Id), a MedicalCertificate id
  and an Invoice id (soft-delete + group manager) (else 404); anonymous is
  401 'Invalid token. Not Authorized.' (a view Response: no challenge).
- An 'export_' document reached without a download token needs its first
  archive row's association owner as request.user (403 otherwise).
- A document reached by account (no document or download token) is served to
  the association's staff, the instance owner and administrators, and to the
  member it belongs to (docmanager.access): an athlete account needs it among
  the PDFs, medical certificate documents and attached files of the
  subscriptions of its user, of the associates of those subscriptions and of
  associates sharing a tax code with them, the certificates of its user, or
  the PDFs of the invoices of its payments (403 'Risorsa non disponibile per
  questo utente e associazione.' otherwise).
- An association-scoped impersonation session needs the document among
  visible_documents (403 'Documento non disponibile per questa associazione.').
- The object is streamed from the S3 default storage: key from filepath or
  <creation timestamp>/<document id>/<filename> under AWS_LOCATION; a missing
  object not linked to one subscription/invoice is 500 {'exception': 'File
  not found'}; a key outside the location is 500 {'exception': "Attempted
  access to '<name>' denied."}. Content-Type is mimetypes.guess_type of the
  name when it is one of the inline types (PDF, PNG, JPEG, GIF, WebP); every
  other file is application/octet-stream with an attachment
  Content-Disposition, also on the view path. X-Content-Type-Options is
  nosniff; Content-Length and Content-Encoding come from the object.

document/<uid>/delete (application.document_scope.delete_document) is covered
up to the owner lookup: IsAuthenticated, is_valid_uuid, a missing document
and a requester without an association are 404 'Document not found.'.

Run with ``python3 -I``.
"""
import base64
from datetime import datetime, timezone
import gzip
import posixpath
import json
from pathlib import Path
from urllib.parse import quote

OUTPUT = Path(__file__).with_name('docmanager_reads_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
STREAM = '<stream>'
LOCATION = 'docs-fixture'


def uid(n):
    return f'a1000000-0000-4000-8000-{983000 + n:012d}'


DOCUMENTS, OBJECTS = {}, {}


def document(key, filename, *, filepath=None, created, body=None, content_type='application/octet-stream', encoding=None):
    """A Document row and (unless body is None) its stored object."""
    moment = datetime.fromisoformat(created)
    row = {'id': uid(len(DOCUMENTS) + 1), 'token': uid(100 + len(DOCUMENTS) + 1), 'filename': filename,
           'filepath': filepath, 'created': created}
    DOCUMENTS[key] = row
    if body is not None:
        name = filepath if filepath else f'{moment.timestamp()}/{row["id"]}/{filename}'
        raw = body if isinstance(body, bytes) else body.encode()
        # django-storages: clean_name (normpath, then backslashes become '/'), then safe_join normalizes again.
        name = posixpath.normpath(posixpath.normpath(name).replace('\\', '/'))
        OBJECTS[key] = {'key': f'{LOCATION}/{name}', 'body_b64': base64.b64encode(raw).decode(),
                        'content_type': content_type, 'encoding': encoding}
    return row


document('plain', 'report.pdf', created='2026-02-03T04:05:06.123456+00:00', body='%PDF-fixture plain report', content_type='application/pdf')
document('comma', 'notes, final.txt', filepath='archive/custom/notes-final.txt', created='2026-02-04T10:00:00+00:00',
         body='plain text notes', content_type='text/plain')
document('unicode', 'ricevuta è.pdf', created='2026-02-05T10:00:00+00:00', body='%PDF-fixture unicode', content_type='application/pdf')
document('quote', 'say "hi" \\ there.png', created='2026-02-06T10:00:00+00:00', body='PNG-fixture', content_type='image/png')
document('noext', 'README', created='2026-02-07T10:00:00+00:00', body='no extension', content_type='application/x-fixture-readme')
document('unknown', 'data.zzq', created='2026-02-08T10:00:00+00:00', body='unknown extension', content_type='application/x-fixture-unknown')
document('gzip', 'table.csv.gz', created='2026-02-09T10:00:00+00:00', body=gzip.compress(b'a,b\n1,2\n', mtime=0),
         content_type='application/gzip', encoding='gzip')
document('upper', 'Photo.JPG', created='2026-02-10T10:00:00+00:00', body='JPEG-fixture', content_type='image/jpeg')
document('tarball', 'backup.tgz', created='2026-02-11T10:00:00+00:00', body='tar-fixture', content_type='application/x-fixture-tar')
document('hidden', '.hidden', created='2026-02-12T10:00:00+00:00', body='dot file', content_type='application/x-fixture-hidden')
document('missing', 'gone.pdf', created='2026-02-13T10:00:00+00:00')
document('escape', 'escape.pdf', filepath='../outside.pdf', created='2026-02-14T10:00:00+00:00')
document('export', 'export_own.zip', filepath='exports/own/export_own.zip', created='2026-02-15T10:00:00+00:00',
         body='PK-export own', content_type='application/zip')
document('export_foreign', 'export_foreign.zip', filepath='exports/foreign/export_foreign.zip', created='2026-02-16T10:00:00+00:00',
         body='PK-export foreign', content_type='application/zip')
document('orphan_export', 'export_orphan.zip', created='2026-02-17T10:00:00+00:00', body='PK-orphan', content_type='application/zip')
document('athlete', 'athlete.pdf', created='2026-02-18T10:00:00+00:00', body='%PDF-athlete', content_type='application/pdf')
document('group', 'group.pdf', created='2026-02-19T10:00:00+00:00', body='%PDF-group', content_type='application/pdf')
document('medical', 'certificato.pdf', created='2026-02-20T10:00:00+00:00', body='%PDF-medical', content_type='application/pdf')
document('invoice', 'ricevuta.pdf', created='2026-02-21T10:00:00+00:00', body='%PDF-invoice', content_type='application/pdf')
document('deleted_invoice', 'annullata.pdf', created='2026-02-22T10:00:00+00:00', body='%PDF-deleted', content_type='application/pdf')
document('foreign', 'foreign.pdf', created='2026-02-23T10:00:00+00:00', body='%PDF-foreign', content_type='application/pdf')
document('docx', 'lettera.docx', created='2026-02-24T10:00:00+00:00', body='docx-fixture', content_type='application/x-fixture-docx')
# The member's records (docmanager.access): each has its own document.
for index, key in enumerate(('same_associate', 'same_tax', 'linked_associate', 'athlete_medical', 'associate_medical', 'attached',
                             'attached_other', 'athlete_invoice', 'associate_invoice', 'other_tax')):
    document(key, f'{key.replace("_", "-")}.pdf', created=f'2026-03-{index + 1:02d}T10:00:00+00:00', body=f'%PDF-{key}',
             content_type='application/pdf')
# Stored PDFs whose object is missing (download_file/view_file print them again): no body.
for index, (key, filename, filepath) in enumerate((
        ('reprint_sub', 'tessera.pdf', None), ('reprint_inv', 'ricevuta è.pdf', None), ('reprint_both', 'entrambi.pdf', None),
        ('reprint_two', 'doppia.pdf', None), ('reprint_three', 'tripla.pdf', None), ('reprint_group', 'gruppo.pdf', None),
        ('reprint_filepath', 'percorso.pdf', 'archive/custom/percorso.pdf'), ('reprint_deleted', 'annullata bis.pdf', None))):
    document(key, filename, filepath=filepath, created=f'2026-04-{index + 1:02d}T10:00:00.250000+00:00')

# Athletes of the family: another member, and the account the primary association belongs to.
EXTRA_USERS = {label: {'id': uid(700 + index), 'username': f'docmanager-reads-{label}', 'email': f'docmanager-reads-{label}@example.test'}
               for index, label in enumerate(('member', 'primary_athlete'))}
PRIMARY = {'association': uid(710), 'user': 'primary_athlete', 'denomination': 'Associazione principale'}
INSTANCE = {'id': 983090, 'domain': 'docmanager-reads.example.test', 'name': 'Docmanager reads', 'self_hosted': True,
            'setup_provenance': 'fresh'}
ASSOCIATES = {
    'person': {'id': uid(720), 'association': 'own_association', 'user': None, 'tax_code': 'DCMRDS80A01H501A'},
    'elsewhere': {'id': uid(721), 'association': 'foreign_association', 'user': None, 'tax_code': 'DCMRDS80A01H501A'},
    'linked': {'id': uid(722), 'association': 'own_association', 'user': 'athlete', 'tax_code': 'DCMRDS81B02H501B'},
    'blank': {'id': uid(723), 'association': 'own_association', 'user': None, 'tax_code': ''},
    'blank_elsewhere': {'id': uid(724), 'association': 'foreign_association', 'user': None, 'tax_code': ''},
}

ARCHIVES = {
    'export': {'id': uid(300), 'association': 'own_association', 'document': 'export', 'date': '2026-02-15'},
    'export_foreign': {'id': uid(301), 'association': 'foreign_association', 'document': 'export_foreign', 'date': '2026-02-16'},
}
SUBSCRIPTIONS = {
    'plain': {'id': uid(400), 'association': 'own_association', 'user': 'owner', 'document': 'plain', 'group': None},
    'athlete': {'id': uid(401), 'association': 'own_association', 'user': 'athlete', 'document': 'athlete', 'group': None,
                'associate': 'person'},
    'group': {'id': uid(402), 'association': 'own_association', 'user': 'owner', 'document': 'group', 'group': 'own_group'},
    'nodoc': {'id': uid(403), 'association': 'own_association', 'user': 'owner', 'document': None, 'group': None},
    'foreign': {'id': uid(404), 'association': 'foreign_association', 'user': 'foreign_owner', 'document': 'foreign', 'group': None},
    # Entered by the association for the athlete's associate, with a certificate of its own.
    'same_associate': {'id': uid(405), 'association': 'own_association', 'user': 'owner', 'document': 'same_associate', 'group': None,
                       'associate': 'person', 'medical': 'associate'},
    # The same tax code in the other association's records.
    'same_tax': {'id': uid(406), 'association': 'foreign_association', 'user': 'foreign_owner', 'document': 'same_tax', 'group': None,
                 'associate': 'elsewhere'},
    # An associate whose account is the athlete's.
    'linked_associate': {'id': uid(407), 'association': 'own_association', 'user': 'owner', 'document': 'linked_associate',
                         'group': None, 'associate': 'linked'},
    # Blank tax codes do not join members: the athlete enrolled one, the other association keeps another.
    'blank': {'id': uid(408), 'association': 'own_association', 'user': 'athlete', 'document': None, 'group': None, 'associate': 'blank'},
    'blank_elsewhere': {'id': uid(409), 'association': 'foreign_association', 'user': 'foreign_owner', 'document': 'other_tax',
                        'group': None, 'associate': 'blank_elsewhere'},
}
REPRINT_SUBSCRIPTIONS = {'reprint_sub': ('reprint_sub', None), 'reprint_both': ('reprint_both', None),
                         'reprint_two_a': ('reprint_two', None), 'reprint_two_b': ('reprint_two', None),
                         'reprint_three_a': ('reprint_three', None), 'reprint_three_b': ('reprint_three', None),
                         'reprint_group': ('reprint_group', 'own_group'), 'reprint_group_other': ('reprint_group', None),
                         'reprint_filepath': ('reprint_filepath', None)}
SUBSCRIPTIONS.update({label: {'id': uid(420 + index), 'association': 'own_association', 'user': 'owner', 'document': key, 'group': group}
                      for index, (label, (key, group)) in enumerate(REPRINT_SUBSCRIPTIONS.items())})
MEDICALS = {'medical': {'id': uid(500), 'document': 'medical'}, 'empty': {'id': uid(501), 'document': None},
            'athlete': {'id': uid(502), 'document': 'athlete_medical', 'user': 'athlete'},
            'associate': {'id': uid(503), 'document': 'associate_medical'}}
FILES = {'attached': {'id': uid(730), 'subscription': 'athlete', 'document': 'attached'},
         'attached_other': {'id': uid(731), 'subscription': 'plain', 'document': 'attached_other'}}
INVOICES = {
    'invoice': {'id': uid(600), 'association': 'own_association', 'document': 'invoice', 'deleted': False},
    'deleted': {'id': uid(601), 'association': 'own_association', 'document': 'deleted_invoice', 'deleted': True},
    'athlete': {'id': uid(602), 'association': 'own_association', 'document': 'athlete_invoice', 'deleted': False},
    'associate': {'id': uid(603), 'association': 'own_association', 'document': 'associate_invoice', 'deleted': False},
}
INVOICES.update({label: {'id': uid(620 + index), 'association': 'own_association', 'document': key, 'deleted': deleted}
                 for index, (label, key, deleted) in enumerate((('reprint_inv', 'reprint_inv', False), ('reprint_both', 'reprint_both', False),
                                                                ('reprint_three', 'reprint_three', False),
                                                                ('reprint_deleted', 'reprint_deleted', True)))})
PAYMENTS = {
    'invoice': {'id': uid(740), 'association': 'own_association', 'invoice': 'invoice', 'user': 'owner', 'associate': None},
    'athlete': {'id': uid(741), 'association': 'own_association', 'invoice': 'athlete', 'user': 'athlete', 'associate': None},
    'associate': {'id': uid(742), 'association': 'own_association', 'invoice': 'associate', 'user': 'owner', 'associate': 'person'},
}
UNKNOWN = uid(999)

cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None, stream=None, challenge=False, renders=(), effect=None):
    case = {'name': f'docmanager-reads-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'challenge': challenge, 'renders': list(renders), 'effect': effect}
    if stream is not None:
        case['stream'] = stream
    cases.append(case)


def retrieve(key_or_id, query=''):
    target = DOCUMENTS[key_or_id]['id'] if key_or_id in DOCUMENTS else key_or_id
    return f'/document/retrieve/{target}' + (f'?{query}' if query else '')


def disposition(as_attachment, filename):
    """django.utils.http.content_disposition_header."""
    if not filename:
        return 'attachment' if as_attachment else None
    kind = 'attachment' if as_attachment else 'inline'
    if filename.isascii() and all(c == '\t' or 0x20 <= ord(c) <= 0x7e for c in filename):
        return kind + '; filename="{}"'.format(filename.replace('\\', '\\\\').replace('"', '\\"'))
    return kind + "; filename*=utf-8''" + quote(filename)


INLINE_TYPES = ('application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp')


def streamed(key, content_type, *, attachment=False, view=False):
    """download_file (comma-free name, disposition) or view_file (no disposition) for the guessed type of the name
    (None when mimetypes knows none). Documents are served with a fixed set of inline types; every other file is
    application/octet-stream and an attachment, whatever the request asks."""
    name = DOCUMENTS[key]['filename']
    obj = OBJECTS[key]
    if not view:
        name = name.replace(',', ' ')
    if content_type in INLINE_TYPES:
        shown = None if view else disposition(attachment, name)
    else:
        content_type, shown = 'application/octet-stream', disposition(True, name)
    return {'object': key, 'content_type': content_type, 'encoding': obj['encoding'], 'disposition': shown}


def ok(name, actor, key, content_type, *, query='', path=None, headers=None, attachment=False, view=False):
    add(name, actor, path or retrieve(key, query), 200, STREAM, headers=headers,
        stream=streamed(key, content_type, attachment=attachment, view=view))


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
NOT_AUTHORIZED = {'error': 'Invalid token. Not Authorized.'}
NOT_FOUND = {'error': 'Document not found.'}
BAD_TOKEN = {'error': 'Invalid download token.'}
EXPORT_DENIED = {'error': 'Not authorized to access this export.'}
NOT_VISIBLE = {'detail': 'Documento non disponibile per questa associazione.'}
NOT_AVAILABLE = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
BAD_UUID = ['badly formed hexadecimal UUID string']
INVALID_JWT = {'detail': 'Given token not valid for any token type', 'code': 'token_not_valid',
               'messages': [{'token_class': 'AccessToken', 'token_type': 'access', 'message': 'Token is invalid'}]}
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']

# --- retrieve: transport, identity and validation -------------------------------------------------
add('retrieve-anonymous', 'anonymous', retrieve('plain'), 401, NOT_AUTHORIZED)
add('retrieve-anonymous-unknown', 'anonymous', retrieve(UNKNOWN), 401, NOT_AUTHORIZED)
add('retrieve-invalid-bearer', 'anonymous', retrieve('plain'), 401, INVALID_JWT, headers={'Authorization': 'Bearer not-a-jwt'},
    challenge=True)
add('retrieve-basic-scheme-anonymous', 'anonymous', retrieve('plain'), 401, NOT_AUTHORIZED, headers={'Authorization': 'Basic Zm9vOmJhcg=='})
add('retrieve-admin-without-session', 'admin', retrieve('plain'), 403, ADMIN)
add('retrieve-restricted-collaborator', 'restricted_collaborator', retrieve('plain'), 403, unmapped(retrieve('plain')))
add('retrieve-scoped-athlete-invalid-uuid', 'owner', '/document/retrieve/not-a-uuid', 400, BAD_UUID, headers=SCOPED)
add('retrieve-invalid-uuid', 'owner', '/document/retrieve/not-a-uuid', 400, BAD_UUID)
add('retrieve-invalid-uuid-anonymous', 'anonymous', '/document/retrieve/zz' + 'a' * 30, 400,
    ["invalid literal for int() with base 16: 'zz" + 'a' * 30 + "'"])
add('retrieve-post-not-allowed', 'owner', retrieve('plain'), 405, method('POST'), method='POST')
add('retrieve-options', 'anonymous', retrieve('plain'), 200,
    {'name': 'Retrieve Document', 'description': '', 'renders': ['application/json'], 'parses': PARSES}, method='OPTIONS')

# --- retrieve: authenticated lookups -----------------------------------------------------------
ok('retrieve-owner-document', 'owner', 'plain', 'application/pdf')
ok('retrieve-owner-download-false', 'owner', 'plain', 'application/pdf', query='download=false', view=True)
ok('retrieve-owner-download-one', 'owner', 'plain', 'application/pdf', query='download=1', attachment=True)
ok('retrieve-owner-download-capital-false', 'owner', 'plain', 'application/pdf', query='download=False', attachment=True)
ok('retrieve-owner-download-empty', 'owner', 'plain', 'application/pdf', query='download=')
ok('retrieve-owner-download-repeated', 'owner', 'plain', 'application/pdf', query='download=1&download=false', view=True)
ok('retrieve-owner-uppercase-uuid', 'owner', 'plain', 'application/pdf', path=retrieve(DOCUMENTS['plain']['id'].upper()))
ok('retrieve-owner-by-subscription', 'owner', 'plain', 'application/pdf', path=retrieve(SUBSCRIPTIONS['plain']['id']))
ok('retrieve-owner-by-group-subscription', 'owner', 'group', 'application/pdf', path=retrieve(SUBSCRIPTIONS['group']['id']))
ok('retrieve-owner-by-group-subscription-own-group', 'owner', 'group', 'application/pdf', path=retrieve(SUBSCRIPTIONS['group']['id']),
   headers={'X-Group-Id': IDS['own_group']})
add('retrieve-owner-by-group-subscription-other-group', 'owner', retrieve(SUBSCRIPTIONS['group']['id']), 404, NOT_FOUND,
    headers={'X-Group-Id': IDS['foreign_group']})
ok('retrieve-owner-by-group-subscription-malformed-group', 'owner', 'group', 'application/pdf',
   path=retrieve(SUBSCRIPTIONS['group']['id']), headers={'X-Group-Id': 'not-a-group'})
ok('retrieve-owner-document-other-group', 'owner', 'group', 'application/pdf', headers={'X-Group-Id': IDS['foreign_group']})
add('retrieve-owner-subscription-without-document', 'owner', retrieve(SUBSCRIPTIONS['nodoc']['id']), 404, NOT_FOUND)
ok('retrieve-owner-by-medical', 'owner', 'medical', 'application/pdf', path=retrieve(MEDICALS['medical']['id']))
add('retrieve-owner-medical-without-document', 'owner', retrieve(MEDICALS['empty']['id']), 404, NOT_FOUND)
ok('retrieve-owner-by-invoice', 'owner', 'invoice', 'application/pdf', path=retrieve(INVOICES['invoice']['id']))
add('retrieve-owner-by-deleted-invoice', 'owner', retrieve(INVOICES['deleted']['id']), 404, NOT_FOUND)
ok('retrieve-owner-deleted-invoice-document', 'owner', 'deleted_invoice', 'application/pdf')
add('retrieve-owner-unknown', 'owner', retrieve(UNKNOWN), 404, NOT_FOUND)
ok('retrieve-owner-foreign-document', 'owner', 'foreign', 'application/pdf')
ok('retrieve-foreign-owner-by-foreign-subscription', 'foreign_owner', 'foreign', 'application/pdf', path=retrieve(SUBSCRIPTIONS['foreign']['id']))
ok('retrieve-full-collaborator', 'full_collaborator', 'plain', 'application/pdf')
ok('retrieve-athlete', 'athlete', 'athlete', 'application/pdf')
ok('retrieve-owner-without-association', 'owner_without_association', 'plain', 'application/pdf')
ok('retrieve-admin-impersonating-owner', 'admin', 'plain', 'application/pdf', headers=ADMIN_OWNER)

# --- retrieve: credentials outside the Authorization header ------------------------------------
ok('retrieve-cookie-owner', 'anonymous', 'plain', 'application/pdf', headers={'Cookie': 'BKN_AUTH=$jwt:owner'})
add('retrieve-cookie-invalid', 'anonymous', retrieve('plain'), 401, NOT_AUTHORIZED, headers={'Cookie': 'BKN_AUTH=garbage'})
ok('retrieve-query-jwt-owner', 'anonymous', 'plain', 'application/pdf', query='token=$jwt:owner')
ok('retrieve-query-jwt-restricted-collaborator', 'anonymous', 'plain', 'application/pdf', query='token=$jwt:restricted_collaborator')
ok('retrieve-query-jwt-admin', 'anonymous', 'plain', 'application/pdf', query='token=$jwt:admin')
add('retrieve-query-jwt-unknown', 'anonymous', retrieve(UNKNOWN, 'token=$jwt:owner'), 404, NOT_FOUND)
ok('retrieve-document-token', 'anonymous', 'plain', 'application/pdf', query='token=' + DOCUMENTS['plain']['token'])
ok('retrieve-document-token-hex', 'anonymous', 'plain', 'application/pdf',
   query='token=' + DOCUMENTS['plain']['token'].replace('-', '').upper())
ok('retrieve-document-token-braces', 'anonymous', 'plain', 'application/pdf', query='token=%7B' + DOCUMENTS['plain']['token'] + '%7D')
ok('retrieve-document-token-download-false', 'anonymous', 'plain', 'application/pdf',
   query='download=false&token=' + DOCUMENTS['plain']['token'], view=True)
add('retrieve-document-token-of-other-document', 'anonymous', retrieve('plain', 'token=' + DOCUMENTS['comma']['token']), 401, NOT_AUTHORIZED)
add('retrieve-document-token-garbage', 'anonymous', retrieve('plain', 'token=garbage'), 401, NOT_AUTHORIZED)
add('retrieve-document-token-empty', 'anonymous', retrieve('plain', 'token='), 401, NOT_AUTHORIZED)
ok('retrieve-owner-with-other-token', 'owner', 'plain', 'application/pdf', query='token=' + DOCUMENTS['comma']['token'])

# --- retrieve: export documents and download tokens --------------------------------------------
ok('retrieve-export-owner', 'owner', 'export', 'application/zip')
add('retrieve-export-foreign-owner', 'foreign_owner', retrieve('export'), 403, EXPORT_DENIED)
add('retrieve-export-full-collaborator', 'full_collaborator', retrieve('export'), 403, EXPORT_DENIED)
add('retrieve-export-athlete', 'athlete', retrieve('export'), 403, EXPORT_DENIED)
ok('retrieve-export-admin-impersonating-owner', 'admin', 'export', 'application/zip', headers=ADMIN_OWNER)
ok('retrieve-export-query-jwt-owner', 'anonymous', 'export', 'application/zip', query='token=$jwt:owner')
add('retrieve-export-document-token', 'anonymous', retrieve('export', 'token=' + DOCUMENTS['export']['token']), 403, EXPORT_DENIED)
ok('retrieve-orphan-export-owner', 'owner', 'orphan_export', 'application/zip')
add('retrieve-orphan-export-athlete', 'athlete', retrieve('orphan_export'), 403, NOT_AVAILABLE)
ok('retrieve-download-token', 'anonymous', 'export', 'application/zip', query='download_token=$download:export')
ok('retrieve-download-token-attachment', 'anonymous', 'export', 'application/zip', query='download=1&download_token=$download:export',
   attachment=True)
ok('retrieve-download-token-foreign-owner', 'foreign_owner', 'export', 'application/zip', query='download_token=$download:export')
ok('retrieve-download-token-compressed', 'anonymous', 'export', 'application/zip', query='download_token=$download:compressed')
add('retrieve-download-token-other-document', 'anonymous', retrieve('plain', 'download_token=$download:export'), 401, BAD_TOKEN)
add('retrieve-download-token-expired', 'anonymous', retrieve('export', 'download_token=$download:expired'), 401, BAD_TOKEN)
add('retrieve-download-token-tampered', 'anonymous', retrieve('export', 'download_token=$download:tampered'), 401, BAD_TOKEN)
add('retrieve-download-token-wrong-purpose', 'anonymous', retrieve('export', 'download_token=$download:wrong_purpose'), 401, BAD_TOKEN)
add('retrieve-download-token-wrong-salt', 'anonymous', retrieve('export', 'download_token=$download:wrong_salt'), 401, BAD_TOKEN)
add('retrieve-download-token-not-dict', 'anonymous', retrieve('export', 'download_token=$download:not_dict'), 401, BAD_TOKEN)
add('retrieve-download-token-empty-association', 'anonymous', retrieve('export', 'download_token=$download:empty_association'), 401, BAD_TOKEN)
add('retrieve-download-token-garbage', 'anonymous', retrieve('export', 'download_token=garbage'), 401, BAD_TOKEN)
add('retrieve-download-token-bad-signature', 'anonymous', retrieve('export', 'download_token=a:b:c'), 401, BAD_TOKEN)
add('retrieve-download-token-uppercase-path', 'anonymous',
    retrieve(DOCUMENTS['export']['id'].upper(), 'download_token=$download:export'), 401, BAD_TOKEN)
add('retrieve-download-token-foreign-export', 'anonymous', retrieve('export_foreign', 'download_token=$download:foreign_export_own_association'),
    404, NOT_FOUND)
add('retrieve-download-token-non-export', 'anonymous', retrieve('plain', 'download_token=$download:plain_own_association'), 404, NOT_FOUND)
add('retrieve-download-token-int-association', 'anonymous', retrieve('export', 'download_token=$download:int_association'), 404, NOT_FOUND)
add('retrieve-download-token-malformed-association', 'anonymous', retrieve('export', 'download_token=$download:malformed_association'),
    500, SERVER)
add('retrieve-download-token-without-timestamp', 'anonymous', retrieve('export', 'download_token=$download:without_timestamp'), 500, SERVER)
add('retrieve-download-token-bad-timestamp', 'anonymous', retrieve('export', 'download_token=$download:bad_timestamp'), 500, SERVER)
ok('retrieve-download-token-empty-with-owner', 'owner', 'export', 'application/zip', query='download_token=')

# --- retrieve: the member's own documents ---------------------------------------------------------
ok('retrieve-athlete-by-subscription', 'athlete', 'athlete', 'application/pdf', path=retrieve(SUBSCRIPTIONS['athlete']['id']))
ok('retrieve-athlete-associate-subscription-document', 'athlete', 'same_associate', 'application/pdf')
ok('retrieve-athlete-by-associate-subscription', 'athlete', 'same_associate', 'application/pdf',
   path=retrieve(SUBSCRIPTIONS['same_associate']['id']))
ok('retrieve-athlete-same-tax-code-document', 'athlete', 'same_tax', 'application/pdf')
ok('retrieve-athlete-linked-associate-document', 'athlete', 'linked_associate', 'application/pdf')
ok('retrieve-athlete-own-certificate', 'athlete', 'athlete_medical', 'application/pdf')
ok('retrieve-athlete-by-own-certificate', 'athlete', 'athlete_medical', 'application/pdf', path=retrieve(MEDICALS['athlete']['id']))
ok('retrieve-athlete-associate-certificate', 'athlete', 'associate_medical', 'application/pdf')
ok('retrieve-athlete-by-associate-certificate', 'athlete', 'associate_medical', 'application/pdf',
   path=retrieve(MEDICALS['associate']['id']))
ok('retrieve-athlete-attached-file', 'athlete', 'attached', 'application/pdf')
ok('retrieve-athlete-own-receipt', 'athlete', 'athlete_invoice', 'application/pdf')
ok('retrieve-athlete-by-own-invoice', 'athlete', 'athlete_invoice', 'application/pdf', path=retrieve(INVOICES['athlete']['id']))
ok('retrieve-athlete-associate-receipt', 'athlete', 'associate_invoice', 'application/pdf')
ok('retrieve-athlete-by-associate-invoice', 'athlete', 'associate_invoice', 'application/pdf',
   path=retrieve(INVOICES['associate']['id']))
ok('retrieve-athlete-view', 'athlete', 'athlete', 'application/pdf', query='download=false', view=True)
ok('retrieve-athlete-cookie', 'anonymous', 'athlete', 'application/pdf', headers={'Cookie': 'BKN_AUTH=$jwt:athlete'})
ok('retrieve-athlete-query-jwt', 'anonymous', 'same_associate', 'application/pdf', query='token=$jwt:athlete')
add('retrieve-athlete-other-document', 'athlete', retrieve('plain'), 403, NOT_AVAILABLE)
add('retrieve-athlete-by-other-subscription', 'athlete', retrieve(SUBSCRIPTIONS['plain']['id']), 403, NOT_AVAILABLE)
add('retrieve-athlete-group-subscription-document', 'athlete', retrieve('group'), 403, NOT_AVAILABLE)
add('retrieve-athlete-other-certificate', 'athlete', retrieve('medical'), 403, NOT_AVAILABLE)
add('retrieve-athlete-by-other-certificate', 'athlete', retrieve(MEDICALS['medical']['id']), 403, NOT_AVAILABLE)
add('retrieve-athlete-other-attached-file', 'athlete', retrieve('attached_other'), 403, NOT_AVAILABLE)
add('retrieve-athlete-other-receipt', 'athlete', retrieve('invoice'), 403, NOT_AVAILABLE)
add('retrieve-athlete-by-other-invoice', 'athlete', retrieve(INVOICES['invoice']['id']), 403, NOT_AVAILABLE)
add('retrieve-athlete-unlinked-document', 'athlete', retrieve('docx'), 403, NOT_AVAILABLE)
add('retrieve-athlete-blank-tax-code-document', 'athlete', retrieve('other_tax'), 403, NOT_AVAILABLE)
add('retrieve-athlete-other-document-view', 'athlete', retrieve('plain', 'download=false'), 403, NOT_AVAILABLE)
add('retrieve-athlete-missing-object-of-other', 'athlete', retrieve('missing'), 403, NOT_AVAILABLE)
add('retrieve-athlete-unknown', 'athlete', retrieve(UNKNOWN), 404, NOT_FOUND)
add('retrieve-athlete-cookie-other-document', 'anonymous', retrieve('plain'), 403, NOT_AVAILABLE,
    headers={'Cookie': 'BKN_AUTH=$jwt:athlete'})
add('retrieve-athlete-query-jwt-other-document', 'anonymous', retrieve('plain', 'token=$jwt:athlete'), 403, NOT_AVAILABLE)
ok('retrieve-athlete-document-token-of-other', 'athlete', 'plain', 'application/pdf', query='token=' + DOCUMENTS['plain']['token'])
add('retrieve-athlete-other-document-token-mismatch', 'athlete', retrieve('plain', 'token=' + DOCUMENTS['comma']['token']), 403,
    NOT_AVAILABLE)
add('retrieve-member-athlete-document', 'member', retrieve('athlete'), 403, NOT_AVAILABLE)
add('retrieve-member-by-athlete-subscription', 'member', retrieve(SUBSCRIPTIONS['athlete']['id']), 403, NOT_AVAILABLE)
add('retrieve-member-athlete-certificate', 'member', retrieve('athlete_medical'), 403, NOT_AVAILABLE)
add('retrieve-member-athlete-receipt', 'member', retrieve('athlete_invoice'), 403, NOT_AVAILABLE)
ok('retrieve-member-document-token', 'member', 'athlete', 'application/pdf', query='token=' + DOCUMENTS['athlete']['token'])
ok('retrieve-primary-association-account', 'primary_athlete', 'plain', 'application/pdf')
ok('retrieve-primary-association-account-by-invoice', 'primary_athlete', 'athlete_invoice', 'application/pdf',
   path=retrieve(INVOICES['athlete']['id']))
ok('retrieve-full-collaborator-athlete-document', 'full_collaborator', 'athlete', 'application/pdf')
ok('retrieve-foreign-owner-athlete-receipt', 'foreign_owner', 'athlete_invoice', 'application/pdf')
ok('retrieve-query-jwt-admin-athlete-document', 'anonymous', 'athlete', 'application/pdf', query='token=$jwt:admin')
ok('retrieve-admin-impersonating-owner-athlete-document', 'admin', 'athlete', 'application/pdf', headers=ADMIN_OWNER)

# --- retrieve: association-scoped impersonation --------------------------------------------------
ok('retrieve-scoped-athlete-own-document', 'owner', 'athlete', 'application/pdf', headers=SCOPED)
ok('retrieve-scoped-athlete-by-subscription', 'owner', 'athlete', 'application/pdf', headers=SCOPED,
   path=retrieve(SUBSCRIPTIONS['athlete']['id']))
add('retrieve-scoped-athlete-other-document', 'owner', retrieve('plain'), 403, NOT_VISIBLE, headers=SCOPED)
add('retrieve-scoped-athlete-download-token', 'owner', retrieve('export', 'download_token=$download:export'), 403, NOT_VISIBLE, headers=SCOPED)
add('retrieve-scoped-athlete-export', 'owner', retrieve('export'), 403, EXPORT_DENIED, headers=SCOPED)
add('retrieve-scoped-athlete-unknown', 'owner', retrieve(UNKNOWN), 404, NOT_FOUND, headers=SCOPED)

# --- retrieve: storage keys and response headers -----------------------------------------------
ok('retrieve-filepath-comma', 'owner', 'comma', 'text/plain')
ok('retrieve-filepath-comma-view', 'owner', 'comma', 'text/plain', query='download=false', view=True)
ok('retrieve-unicode-filename', 'owner', 'unicode', 'application/pdf')
ok('retrieve-quoted-filename', 'owner', 'quote', 'image/png', attachment=True, query='download=yes')
ok('retrieve-no-extension', 'owner', 'noext', 'application/x-fixture-readme')
ok('retrieve-unknown-extension', 'owner', 'unknown', 'application/x-fixture-unknown')
ok('retrieve-gzip-encoding', 'owner', 'gzip', 'text/csv')
ok('retrieve-uppercase-extension', 'owner', 'upper', 'image/jpeg')
ok('retrieve-suffix-map', 'owner', 'tarball', 'application/x-tar')
ok('retrieve-dot-file', 'owner', 'hidden', 'application/x-fixture-hidden')
add('retrieve-missing-object', 'owner', retrieve('missing'), 500, {'exception': 'File not found'})
add('retrieve-missing-object-view', 'owner', retrieve('missing', 'download=false'), 500, {'exception': 'File not found'})
add('retrieve-key-outside-location', 'owner', retrieve('escape'), 500, {'exception': "Attempted access to '../outside.pdf' denied."})

# --- retrieve: a stored PDF whose object is missing is printed again --------------------------
# download_file/view_file call the renderer for the view of the one subscription (else the one invoice) that has
# the document as its PDF, with the request's Authorization header or 'Bearer <?token>', publish save_to_storage
# with '<creation timestamp>/<id>/<filename>' and the PDF, read no outcome and still stream: until the worker has
# stored the file the answer is the provider's missing-key text. effect.published names the document whose
# storage task the case publishes; effect.store runs the task afterwards, as the worker would.
NO_KEY = {'exception': 'An error occurred (NoSuchKey) when calling the GetObject operation: The specified key does not exist.'}
PDF, FAILED, NOT_PDF = ({'status': 200, 'content_type': 'application/pdf', 'body': '$pdf'},
                        {'status': 500, 'content_type': 'text/plain', 'body': 'renderer failed'},
                        {'status': 200, 'content_type': 'text/html', 'body': '<html>busy</html>'})


def view_of(kind, label):
    row = SUBSCRIPTIONS[label] if kind == 'subscription' else INVOICES[label]
    return f'document/{kind}/{row["id"]}/view'


def reprint(name, actor, key, kind, label, *, query='', headers=None, authorization='$bearer', reply=PDF, store=False):
    add(name, actor, retrieve(key, query), 500, NO_KEY, headers=headers,
        renders=[{'url': view_of(kind, label), 'authorization': authorization, 'reply': reply}],
        effect={'published': key if reply is PDF else None, 'store': store})


def rendered(name, actor, key, *, query='', view=False):
    add(name, actor, retrieve(key, query), 200, STREAM,
        stream={'object': '$rendered', 'content_type': 'application/pdf', 'encoding': None,
                'disposition': None if view else disposition(False, DOCUMENTS[key]['filename'].replace(',', ' '))})


reprint('reprint-subscription-first-try', 'owner', 'reprint_sub', 'subscription', 'reprint_sub', store=True)
rendered('reprint-subscription-stored', 'owner', 'reprint_sub')
# The query token becomes the bearer; view_file prints the same way.
reprint('reprint-invoice-view-query-token', 'anonymous', 'reprint_inv', 'invoice', 'reprint_inv', query='download=false&token=$jwt:owner',
        authorization='Bearer $jwt:owner', store=True)
rendered('reprint-invoice-stored-view', 'owner', 'reprint_inv', query='download=false', view=True)
# A renderer failure or an answer that is not a PDF publishes nothing; the stream is still attempted.
reprint('reprint-renderer-failure', 'owner', 'reprint_both', 'subscription', 'reprint_both', reply=FAILED)
reprint('reprint-renderer-not-pdf', 'owner', 'reprint_both', 'subscription', 'reprint_both', reply=NOT_PDF)
# The subscription comes before the invoice. A cookie session has no Authorization header and no ?token.
reprint('reprint-subscription-before-invoice-cookie', 'anonymous', 'reprint_both', 'subscription', 'reprint_both',
        headers={'Cookie': 'BKN_AUTH=$jwt:owner'}, authorization=None)
# Until the task has run, every request prints again.
reprint('reprint-not-stored-yet-prints-again', 'full_collaborator', 'reprint_both', 'subscription', 'reprint_both', query='download=1')
add('reprint-two-subscriptions-file-not-found', 'owner', retrieve('reprint_two'), 500, {'exception': 'File not found'})
# Two subscriptions and one invoice: the invoice view. The document's own token is the bearer.
reprint('reprint-invoice-after-two-subscriptions-document-token', 'anonymous', 'reprint_three', 'invoice', 'reprint_three',
        query='token=' + DOCUMENTS['reprint_three']['token'], authorization='Bearer ' + DOCUMENTS['reprint_three']['token'])
# The subscription manager reads X-Group-Id: two subscriptions without the header, one inside the group.
add('reprint-group-two-subscriptions-file-not-found', 'owner', retrieve('reprint_group'), 500, {'exception': 'File not found'})
reprint('reprint-group-header-one-subscription', 'owner', 'reprint_group', 'subscription', 'reprint_group',
        headers={'X-Group-Id': IDS['own_group']})
# A document with a filepath is read from it, while the task stores under the creation timestamp: the stored
# file is never the one read.
reprint('reprint-filepath-first-try', 'owner', 'reprint_filepath', 'subscription', 'reprint_filepath', store=True)
reprint('reprint-filepath-prints-again', 'owner', 'reprint_filepath', 'subscription', 'reprint_filepath', query='download=false', store=True)
add('reprint-invoice-out-of-the-list-file-not-found', 'owner', retrieve('reprint_deleted'), 500, {'exception': 'File not found'})

# --- delete: up to the owner lookup ------------------------------------------------------------
DELETE_PLAIN = f'/document/{DOCUMENTS["plain"]["id"]}/delete'
add('delete-anonymous', 'anonymous', DELETE_PLAIN, 401, ANONYMOUS, method='DELETE', challenge=True)
add('delete-admin-without-session', 'admin', DELETE_PLAIN, 403, ADMIN, method='DELETE')
add('delete-restricted-collaborator', 'restricted_collaborator', DELETE_PLAIN, 403, unmapped(DELETE_PLAIN), method='DELETE')
add('delete-scoped-athlete', 'owner', DELETE_PLAIN, 403, SCOPED_DENIED, method='DELETE', headers=SCOPED)
add('delete-get-not-allowed', 'owner', DELETE_PLAIN, 405, method('GET'))
add('delete-options', 'owner', DELETE_PLAIN, 200,
    {'name': 'Delete Document', 'description': '', 'renders': ['application/json'], 'parses': PARSES}, method='OPTIONS')
add('delete-invalid-uuid', 'owner', '/document/not-a-uuid/delete', 400, BAD_UUID, method='DELETE')
add('delete-unknown', 'owner', f'/document/{UNKNOWN}/delete', 404, NOT_FOUND, method='DELETE')
add('delete-athlete', 'athlete', DELETE_PLAIN, 404, NOT_FOUND, method='DELETE')
add('delete-owner-without-association', 'owner_without_association', DELETE_PLAIN, 404, NOT_FOUND, method='DELETE')

# Names urllib.parse reads as a URL (a scheme of two or more characters): mimetypes.guess_type guesses from the URL's
# path (after the scheme, before '#', '?' and, for a few schemes, ';'), and a data: name answers the text before its
# first ';' or ','. download_file replaces the commas first, so its data: name has no type.
document('urlname', 'Nota:spese.pdf', created='2026-03-20T10:00:00+00:00', body='%PDF-url name', content_type='application/x-fixture-url')
document('urlparts', 'ab:c.pdf;v.txt?x#y.png', created='2026-03-21T10:00:00+00:00', body='url parts',
         content_type='application/x-fixture-parts')
document('dataname', 'data:text/css,body.pdf', created='2026-03-22T10:00:00+00:00', body='body {}',
         content_type='application/x-fixture-data')
ok('retrieve-url-name', 'owner', 'urlname', 'application/pdf')
ok('retrieve-url-name-view', 'owner', 'urlname', 'application/pdf', query='download=false', view=True)
ok('retrieve-url-name-parts', 'owner', 'urlparts', 'text/plain', query='download=1', attachment=True)
ok('retrieve-data-name-view', 'owner', 'dataname', 'text/css', query='download=false', view=True)
ok('retrieve-data-name-download', 'owner', 'dataname', None)

# A network location urlsplit refuses is a ValueError after the object was fetched: the exception text is the answer
# (the object is looked up first, so a missing one answers 'File not found'). Valid bracketed hosts and non-ASCII
# locations that keep their NFKC form are plain URLs; a data: type of any text is the guessed type (never inline).
for key, filename, text in (
        ('netloc-open', 'ab://[::1', 'Invalid IPv6 URL'),
        ('netloc-ipv4', 'ab://[1.2.3.4]', 'An IPv4 address cannot be in brackets'),
        ('netloc-host', 'ab://[abc]', "'abc' does not appear to be an IPv4 or IPv6 address"),
        ('netloc-future', 'ab://[vz.x]', 'IPvFuture address is invalid'),
        ('netloc-after', 'ab://[::1]x', 'Invalid IPv6 URL'),
        ('netloc-nfkc', 'ab://h\u2100st', "netloc 'h\u2100st' contains invalid characters under NFKC normalization"),
        ('netloc-fullwidth', 'ab://x\uff0f', "netloc 'x\uff0f' contains invalid characters under NFKC normalization")):
    document(key, filename, created='2026-03-25T10:00:00+00:00', body='netloc', content_type='application/x-fixture-netloc')
    add('retrieve-' + key, 'owner', retrieve(key), 500, {'exception': text})
add('retrieve-netloc-open-view', 'owner', retrieve('netloc-open', 'download=false'), 500, {'exception': 'Invalid IPv6 URL'})
document('netloc-missing', 'ab://[::1', filepath='named/netloc-missing', created='2026-03-25T11:00:00+00:00', body=None)
add('retrieve-netloc-missing-object', 'owner', retrieve('netloc-missing'), 500, {'exception': 'File not found'})
document('netloc-valid', 'ab://[::1]:80/c.pdf', created='2026-03-25T12:00:00+00:00', body='%PDF-netloc', content_type='application/x-fixture-netloc')
document('netloc-unicode', 'ab://h\u00f4st/c.pdf', created='2026-03-25T13:00:00+00:00', body='%PDF-unicode host',
         content_type='application/x-fixture-netloc')
document('netloc-compat', 'ab://\uff48/c.png', created='2026-03-25T14:00:00+00:00', body='png', content_type='application/x-fixture-netloc')
document('dataname-unicode', 'data:text/h\u00e9,x', created='2026-03-25T15:00:00+00:00', body='data', content_type='application/x-fixture-data')
ok('retrieve-netloc-valid', 'owner', 'netloc-valid', 'application/pdf')
ok('retrieve-netloc-unicode', 'owner', 'netloc-unicode', 'application/pdf')
ok('retrieve-netloc-compat-view', 'owner', 'netloc-compat', 'image/png', query='download=false', view=True)
ok('retrieve-data-name-unicode-view', 'owner', 'dataname-unicode', None, query='download=false', view=True)

# Types outside the inline list are downloads on both paths; the object's own type is not used.
document('webpage', 'pagina.html', created='2026-03-23T10:00:00+00:00', body='<p>pagina</p>', content_type='text/html')
document('drawing', 'disegno.svg', created='2026-03-24T10:00:00+00:00', body='<svg xmlns="http://www.w3.org/2000/svg"/>',
         content_type='image/svg+xml')
document('datapage', 'data:text/html,x', created='2026-03-25T10:00:00+00:00', body='<p>x</p>', content_type='text/html')
document('datapng', 'data:image/png,x', created='2026-03-26T10:00:00+00:00', body='PNG-data name', content_type='text/html')
document('gif', 'animazione.GIF', created='2026-03-27T10:00:00+00:00', body='GIF89a-fixture', content_type='image/gif')
document('webp', 'foto.webp', created='2026-03-28T10:00:00+00:00', body='RIFF-fixture', content_type='image/webp')
document('script', 'codice.js', created='2026-03-29T10:00:00+00:00', body='void 0', content_type='text/javascript')
document('markup', 'dati.xml', created='2026-03-30T10:00:00+00:00', body='<a/>', content_type='application/xml')
document('bitmap', 'foto.bmp', created='2026-03-31T10:00:00+00:00', body='BM-fixture', content_type='image/bmp')
ok('retrieve-html-view', 'owner', 'webpage', 'text/html', query='download=false', view=True)
ok('retrieve-html-download-empty', 'owner', 'webpage', 'text/html', query='download=')
ok('retrieve-html-download', 'owner', 'webpage', 'text/html', query='download=true', attachment=True)
ok('retrieve-html-document-token-view', 'anonymous', 'webpage', 'text/html',
   query='download=false&token=' + DOCUMENTS['webpage']['token'], view=True)
ok('retrieve-svg-view', 'owner', 'drawing', 'image/svg+xml', query='download=false', view=True)
ok('retrieve-svg-default', 'owner', 'drawing', 'image/svg+xml')
ok('retrieve-data-html-name-view', 'owner', 'datapage', 'text/html', query='download=false', view=True)
ok('retrieve-data-png-name-view', 'owner', 'datapng', 'image/png', query='download=false', view=True)
ok('retrieve-script-view', 'owner', 'script', 'text/javascript', query='download=false', view=True)
ok('retrieve-xml-view', 'owner', 'markup', 'text/xml', query='download=false', view=True)
ok('retrieve-bmp-view', 'owner', 'bitmap', 'image/bmp', query='download=false', view=True)
ok('retrieve-gif-view', 'owner', 'gif', 'image/gif', query='download=false', view=True)
ok('retrieve-webp-view', 'owner', 'webp', 'image/webp', query='download=false', view=True)
ok('retrieve-webp-default', 'owner', 'webp', 'image/webp')

# Names the header rules treat specially. A quotable name ending in a newline keeps it in the quoted form and Django's
# response refuses the header after the object was opened: 500 {'exception': ...}. Other line breaks and non-ASCII names
# use the percent-encoded form. An empty name has no type and its attachment header has no filename.
document('newline', 'verbale.pdf\n', filepath='named/newline', created='2026-04-01T10:00:00+00:00', body='%PDF-newline',
         content_type='application/pdf')
document('newline_accent', 'verbalè.pdf\n', filepath='named/newline-accent', created='2026-04-02T10:00:00+00:00',
         body='%PDF-newline accent', content_type='application/pdf')
document('newline_inside', 'ver\nbale.pdf', filepath='named/newline-inside', created='2026-04-03T10:00:00+00:00',
         body='%PDF-newline inside', content_type='application/pdf')
document('newline_missing', 'assente.pdf\n', filepath='named/newline-missing', created='2026-04-04T10:00:00+00:00')
document('blank', '', filepath='named/blank', created='2026-04-05T10:00:00+00:00', body='blank name', content_type='application/pdf')
bad_header = lambda value: {'exception': "Header values can't contain newlines (got " + repr(value) + ')'}
add('retrieve-newline-name-view', 'owner', retrieve('newline', 'download=false'), 500,
    bad_header('attachment; filename="verbale.pdf\n"'))
add('retrieve-newline-name-default', 'owner', retrieve('newline'), 500, bad_header('attachment; filename="verbale.pdf\n"'))
add('retrieve-newline-name-document-token', 'anonymous', retrieve('newline', 'download=1&token=' + DOCUMENTS['newline']['token']), 500,
    bad_header('attachment; filename="verbale.pdf\n"'))
ok('retrieve-newline-accent-name-view', 'owner', 'newline_accent', None, query='download=false', view=True)
ok('retrieve-newline-accent-name-default', 'owner', 'newline_accent', None)
ok('retrieve-newline-inside-name-view', 'owner', 'newline_inside', 'application/pdf', query='download=false', view=True)
ok('retrieve-newline-inside-name-download', 'owner', 'newline_inside', 'application/pdf', query='download=1', attachment=True)
add('retrieve-newline-name-missing-object', 'owner', retrieve('newline_missing'), 500, {'exception': 'File not found'})
ok('retrieve-blank-name-view', 'owner', 'blank', None, query='download=false', view=True)
ok('retrieve-blank-name-default', 'owner', 'blank', None)
assert streamed('blank', None, view=True)['disposition'] == 'attachment'
assert streamed('newline_accent', None)['disposition'] == "attachment; filename*=utf-8''verbal%C3%A8.pdf%0A"
assert streamed('newline_inside', 'application/pdf')['disposition'] == "inline; filename*=utf-8''ver%0Abale.pdf"

# The storage answers HEAD with a client error other than 404 (here a key longer than it accepts): default_storage.exists
# raises botocore's ClientError, whose text names the status and its reason phrase.
document('longkey', 'lungo.pdf', filepath='lungo/' + 'è' * 600, created='2026-04-06T10:00:00+00:00')
HEAD_REFUSED = {'exception': 'An error occurred (400) when calling the HeadObject operation: Bad Request'}
add('retrieve-storage-head-refused-view', 'owner', retrieve('longkey', 'download=false'), 500, HEAD_REFUSED)
add('retrieve-storage-head-refused-download', 'owner', retrieve('longkey', 'download=1'), 500, HEAD_REFUSED)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'document retrieve streaming and document delete pre-checks',
    'ids': IDS, 'location': LOCATION, 'documents': DOCUMENTS, 'objects': OBJECTS, 'archives': ARCHIVES,
    'subscriptions': SUBSCRIPTIONS, 'medicals': MEDICALS, 'invoices': INVOICES, 'extra_users': EXTRA_USERS, 'primary': PRIMARY,
    'instance': INSTANCE, 'associates': ASSOCIATES, 'files': FILES, 'payments': PAYMENTS, 'cases': cases},
    ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
