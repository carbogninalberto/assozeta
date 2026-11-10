"""Author the document/<uid>/delete HTTP sequence (document-delete.md).

application.document_scope.delete_document, in Django's order:
- identity resolution, IsAuthenticated, @api_view(['DELETE']) (Allow DELETE, OPTIONS);
- is_valid_uuid(uid): 400 with the ValueError text as a one-item list;
- Document by id and SportAssociation.objects.filter(user=request.user).first() (the
  owner for collaborators); a missing document or association is 404 'Document not found.';
- document_belongs_to: every object referencing the document (foreign keys and
  many-to-many links, base managers) is resolved to its association with the audit
  resolver of its model, else its own sport_association; an object without a known
  owner is resolved through the objects referencing it, one level up. A reference of
  another association answers 404; references all of the caller's association allow
  the delete; otherwise the association the document's creation audit entries are
  indexed to decides (exactly the caller's);
- Document.delete(): Django's collector. Archive rows, subscription files, medical
  certificates and medical appointments cascade (each audited); many-to-many links are
  removed without audit; SET_NULL references (subscription, invoice, supplier invoice,
  customer invoice and instructor hours PDFs, and the subscription of a deleted
  certificate) are cleared by an update, without audit. Models whose foreign key is not
  nullable are deleted and audited before the document; nullable ones after it. Rows of
  one model are audited in descending key order.
- 200 {'msg': 'success!'}. The stored object is not removed.

Each effect lists the rows removed ('deleted': rows matching the given columns), the
columns cleared and the audit entries in creation order with their index row.

Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('document_delete_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
cases = []

DOCUMENT, ARCHIVE, PAYMENT, INVOICE = 'docmanager.Document', 'application.SportAssociationDocumentsArchive', 'application.Payment', 'application.Invoice'
SUPPLIER_INVOICE, CUSTOMER_INVOICE, SUBSCRIPTION = 'application.InvoiceSuppliers', 'application.CustomerInvoice', 'application.Subscription'
CERTIFICATE, APPOINTMENT, FILE = 'application.MedicalCertificate', 'application.MedicalAppointments', 'application.SubscriptionFile'
MEMBERSHIP, LOCATION, MODULE, RESPONSE = ('application.SubscriptionMembership', 'application.CourseLocation', 'application.Module',
                                          'application.ModuleResponses')
INSTRUCTOR, HOURS = 'application.Instructor', 'application.InstructorHours'
THROUGH = {  # (owner model, m2m field) -> (through label, owner column)
    (PAYMENT, 'attachments'): ('application.Payment_attachments', 'payment_id'),
    (INVOICE, 'attached_documents'): ('application.Invoice_attached_documents', 'invoice_id'),
    (MEMBERSHIP, 'attached_membership_documents'): ('application.SubscriptionMembership_attached_membership_documents', 'subscriptionmembership_id'),
    (LOCATION, 'documents'): ('application.CourseLocation_documents', 'courselocation_id'),
    (RESPONSE, 'attachments'): ('application.ModuleResponses_attachments', 'moduleresponses_id'),
    (INSTRUCTOR, 'documents'): ('application.Instructor_documents', 'instructor_id'),
}
KEYS = {DOCUMENT: 'document_id', ARCHIVE: 'sport_association_documents_archive_id', PAYMENT: 'payment_id', INVOICE: 'invoice_id',
        SUPPLIER_INVOICE: 'invoice_supplier_id', CUSTOMER_INVOICE: 'customer_invoice_id', SUBSCRIPTION: 'subscription_id',
        CERTIFICATE: 'medical_id', APPOINTMENT: 'medical_appointments_id', FILE: 'subscription_file_id',
        MEMBERSHIP: 'subscription_membership_id', LOCATION: 'course_location_id', MODULE: 'module_id', RESPONSE: 'module_response_id',
        INSTRUCTOR: 'instructor_id', HOURS: 'instructor_hours_id'}

_next = iter(range(1, 1000))


def uid():
    return f'a1000000-0000-4000-8000-{986000 + next(_next):012d}'


ROWS, LINKS, ENTRIES, DOCUMENTS = [], [], [], {}
CREATED = '2026-03-01T10:00:00+00:00'


def row(model, **fields):
    key = uid()
    ROWS.append({'model': model, 'fields': {KEYS[model]: key, **fields}})
    return key


def document(name, *, size=None, filepath=None):
    key = uid()
    DOCUMENTS[name] = {'document_id': key, 'token': uid(), 'filename': f'{name}.pdf', 'filepath': filepath, 'file_size_bytes': size,
                       'creation_date': CREATED}
    ROWS.append({'model': DOCUMENT, 'fields': DOCUMENTS[name]})
    return key


def link(model, key, field, doc):
    LINKS.append({'model': model, 'key': key, 'field': field, 'document': doc})


def created_entry(doc, association, action=0):
    """An audit entry of the document (action 0 is its creation) indexed to association (None: no index row)."""
    ENTRIES.append({'document': doc, 'association': association, 'action': action})


def subscription(association, *, user='owner', medical=None, pdf=None, group=None):
    return row(SUBSCRIPTION, sport_association_id=association, user_id=IDS[user], medical_id=medical, document_pdf_id=pdf, group_id=group)


# --- audit expectations --------------------------------------------------------------------------
def document_changes(name):
    stored = DOCUMENTS[name]
    changes = {'document_id': [stored['document_id'], 'None'], 'token': [stored['token'], 'None'], 'filename': [stored['filename'], 'None'],
               'creation_date': ['2026-03-01 10:00:00', 'None'],
               # auditlog walks _meta.get_fields(): the reverse foreign key whose accessor is its own name.
               'customer_invoice_pdf': ['application.CustomerInvoice.None', 'None']}
    if stored['filepath'] is not None:
        changes['filepath'] = [stored['filepath'], 'None']
    if stored['file_size_bytes'] is not None:
        changes['file_size_bytes'] = [str(stored['file_size_bytes']), 'None']
    return changes


def audit(model, key, changes, association, path):
    return {'model': model, 'key': key, 'changes': changes, 'association': association, 'path': path}


def document_audit(name, actor_association=OWN):
    """The Document entry: no resolver and no association in its changes, so the actor's association."""
    return audit(DOCUMENT, DOCUMENTS[name]['document_id'], document_changes(name), actor_association,
                 'actor.sport_association' if actor_association else None)


def effect(*, deleted=(), cleared=(), entries=()):
    return {'deleted': [{'model': model, 'match': match} for model, match in deleted],
            'cleared': [{'model': model, 'key': key, 'field': field} for model, key, field in cleared], 'audit': list(entries)}


def gone(name):
    return (DOCUMENT, {'document_id': DOCUMENTS[name]['document_id']})


def unlinked(model, key, field, name):
    label, column = THROUGH[(model, field)]
    return (label, {column: key, 'document_id': DOCUMENTS[name]['document_id']})


def add(name, actor, doc, status, response, *, method='DELETE', headers=None, effect=None):
    path = doc if doc.startswith('/') else f'/document/{DOCUMENTS[doc]["document_id"]}/delete'
    cases.append({'name': f'document-delete-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect})


OK, NOT_FOUND = {'msg': 'success!'}, {'error': 'Document not found.'}

# --- seeded rows ---------------------------------------------------------------------------------
# Archive rows.
document('archived', size=1234, filepath='archive/archived.pdf')
ARCHIVED = row(ARCHIVE, sport_association_id=OWN, document_id=DOCUMENTS['archived']['document_id'], date='2026-03-02', folder_id=None)
document('archived_twice')
ARCHIVED_TWICE = [row(ARCHIVE, sport_association_id=OWN, document_id=DOCUMENTS['archived_twice']['document_id'], date='2026-03-03',
                      folder_id=None) for _ in range(2)]
document('archived_foreign')
row(ARCHIVE, sport_association_id=FOREIGN, document_id=DOCUMENTS['archived_foreign']['document_id'], date='2026-03-04', folder_id=None)
document('archived_shared')
for association in (OWN, FOREIGN):
    row(ARCHIVE, sport_association_id=association, document_id=DOCUMENTS['archived_shared']['document_id'], date='2026-03-05', folder_id=None)
document('archived_collaborator')
ARCHIVED_COLLABORATOR = row(ARCHIVE, sport_association_id=OWN, document_id=DOCUMENTS['archived_collaborator']['document_id'],
                            date='2026-03-06', folder_id=None)
document('archived_for_foreign_owner')
row(ARCHIVE, sport_association_id=OWN, document_id=DOCUMENTS['archived_for_foreign_owner']['document_id'], date='2026-03-07', folder_id=None)
document('archived_methods')
row(ARCHIVE, sport_association_id=OWN, document_id=DOCUMENTS['archived_methods']['document_id'], date='2026-03-08', folder_id=None)

# Unreferenced documents: the creation audit entry decides.
for name, entries in (('loose_own', [(OWN, 0)]), ('loose_none', []), ('loose_foreign', [(FOREIGN, 0)]),
                      ('loose_both', [(OWN, 0), (FOREIGN, 0)]), ('loose_unindexed', [(None, 0)]), ('loose_update_only', [(OWN, 1)]),
                      ('loose_twice_own', [(OWN, 0), (OWN, 0)])):
    document(name)
    for association, action in entries:
        created_entry(DOCUMENTS[name]['document_id'], association, action)

# Payment attachments (many-to-many).
PAYMENTS = {}
for name, fields in (('attachment', {'sport_association_id': OWN}), ('attachment_deleted', {'sport_association_id': OWN, 'deleted': True}),
                     ('attachment_group', {'sport_association_id': OWN, 'group_id': IDS['own_group']}),
                     ('attachment_foreign', {'sport_association_id': FOREIGN}), ('attachment_unowned', {'sport_association_id': None}),
                     ('attachment_unowned_created', {'sport_association_id': None})):
    document(name)
    PAYMENTS[name] = row(PAYMENT, **fields)
    link(PAYMENT, PAYMENTS[name], 'attachments', DOCUMENTS[name]['document_id'])
created_entry(DOCUMENTS['attachment_unowned_created']['document_id'], OWN)

# Invoices.
document('invoice_pdf')
INVOICE_PDF = row(INVOICE, sport_association_id=OWN, membership_fee='0.00', activity_fee='10.00',
                  document_pdf_id=DOCUMENTS['invoice_pdf']['document_id'])
document('invoice_pdf_foreign')
row(INVOICE, sport_association_id=FOREIGN, membership_fee='0.00', activity_fee='10.00',
    document_pdf_id=DOCUMENTS['invoice_pdf_foreign']['document_id'])
document('invoice_attached')
INVOICE_ATTACHED = row(INVOICE, sport_association_id=OWN, membership_fee='0.00', activity_fee='10.00', deleted=True)
link(INVOICE, INVOICE_ATTACHED, 'attached_documents', DOCUMENTS['invoice_attached']['document_id'])
document('supplier_invoice')
SUPPLIER = row(SUPPLIER_INVOICE, sport_association_id=OWN, invoice_identifier='F-1', amount='12.50',
               document_pdf_id=DOCUMENTS['supplier_invoice']['document_id'])
document('customer_invoice')
CUSTOMER = row(CUSTOMER_INVOICE, sport_association_id=OWN, pdf_id=DOCUMENTS['customer_invoice']['document_id'])

# Subscriptions, certificates, appointments and files.
document('subscription_pdf')
SUBSCRIPTION_PDF = subscription(OWN, pdf=DOCUMENTS['subscription_pdf']['document_id'], group=IDS['own_group'])
document('subscription_pdf_foreign')
subscription(FOREIGN, user='foreign_owner', pdf=DOCUMENTS['subscription_pdf_foreign']['document_id'])
# A certificate has no owner of its own: the subscriptions carrying it decide.
document('certificate')
CERTIFICATE_OWN = row(CERTIFICATE, document_id=DOCUMENTS['certificate']['document_id'], user_id=None, creation_date=CREATED,
                      expiration_date='2027-01-31', competitive_medical_certificate=False, notes=None)
CERTIFICATE_SUBSCRIPTION = subscription(OWN, medical=CERTIFICATE_OWN)
document('certificate_foreign')
subscription(FOREIGN, user='foreign_owner', medical=row(
    CERTIFICATE, document_id=DOCUMENTS['certificate_foreign']['document_id'], user_id=None, creation_date=CREATED, expiration_date=None,
    competitive_medical_certificate=False, notes=None))
document('certificate_loose')
row(CERTIFICATE, document_id=DOCUMENTS['certificate_loose']['document_id'], user_id=IDS['athlete'], creation_date=CREATED,
    expiration_date=None, competitive_medical_certificate=False, notes=None)
document('certificate_loose_created')
CERTIFICATE_LOOSE = row(CERTIFICATE, document_id=DOCUMENTS['certificate_loose_created']['document_id'], user_id=IDS['athlete'],
                        creation_date=CREATED, expiration_date=None, competitive_medical_certificate=True, notes='idoneo')
created_entry(DOCUMENTS['certificate_loose_created']['document_id'], OWN)
document('appointment')
APPOINTMENT_SUBSCRIPTION = subscription(OWN)
APPOINTMENT_ROW = row(APPOINTMENT, subscription_id=APPOINTMENT_SUBSCRIPTION, region='agonistica',
                      document_id=DOCUMENTS['appointment']['document_id'], date='2026-04-01', notes=None, sport='Nuoto', meta=None,
                      address='Via Roma 1', creation_date=CREATED)
document('file')
FILE_SUBSCRIPTION = subscription(OWN)
FILE_ROW = row(FILE, subscription_id=FILE_SUBSCRIPTION, document_id=DOCUMENTS['file']['document_id'], creation_date=CREATED)
document('file_foreign')
row(FILE, subscription_id=subscription(FOREIGN, user='foreign_owner'), document_id=DOCUMENTS['file_foreign']['document_id'],
    creation_date=CREATED)
# One document under two models: a file of the caller's subscription and an archive row.
document('file_and_archive')
BOTH_FILE = row(FILE, subscription_id=FILE_SUBSCRIPTION, document_id=DOCUMENTS['file_and_archive']['document_id'], creation_date=CREATED)
BOTH_ARCHIVE = row(ARCHIVE, sport_association_id=OWN, document_id=DOCUMENTS['file_and_archive']['document_id'], date='2026-03-09',
                   folder_id=None)

# Other many-to-many holders.
document('membership')
MEMBERSHIP_ROW = row(MEMBERSHIP, sport_association_id=OWN)
link(MEMBERSHIP, MEMBERSHIP_ROW, 'attached_membership_documents', DOCUMENTS['membership']['document_id'])
document('location')
LOCATION_ROW = row(LOCATION, sport_association_id=OWN)
link(LOCATION, LOCATION_ROW, 'documents', DOCUMENTS['location']['document_id'])
document('response')
RESPONSE_ROW = row(RESPONSE, module_id=row(MODULE, sport_association_id=OWN))
link(RESPONSE, RESPONSE_ROW, 'attachments', DOCUMENTS['response']['document_id'])
document('response_foreign')
RESPONSE_FOREIGN = row(RESPONSE, module_id=row(MODULE, sport_association_id=FOREIGN))
link(RESPONSE, RESPONSE_FOREIGN, 'attachments', DOCUMENTS['response_foreign']['document_id'])

# Instructors belong to the association of their owner account.
INSTRUCTOR_OWN = row(INSTRUCTOR, user_id=IDS['owner'], first_name='Ada', last_name='Bianchi')
INSTRUCTOR_FOREIGN = row(INSTRUCTOR, user_id=IDS['foreign_owner'], first_name='Ugo', last_name='Neri')
INSTRUCTOR_UNOWNED = row(INSTRUCTOR, user_id=IDS['owner_without_association'], first_name='Lia', last_name='Verdi')
document('instructor_document')
link(INSTRUCTOR, INSTRUCTOR_OWN, 'documents', DOCUMENTS['instructor_document']['document_id'])
document('instructor_document_foreign')
link(INSTRUCTOR, INSTRUCTOR_FOREIGN, 'documents', DOCUMENTS['instructor_document_foreign']['document_id'])
document('hours')
HOURS_OWN = row(HOURS, instructor_id=INSTRUCTOR_OWN, hours='2.00', document_id=DOCUMENTS['hours']['document_id'])
document('hours_unowned')
row(HOURS, instructor_id=INSTRUCTOR_UNOWNED, hours='1.00', document_id=DOCUMENTS['hours_unowned']['document_id'])
document('hours_unowned_created')
HOURS_UNOWNED = row(HOURS, instructor_id=INSTRUCTOR_UNOWNED, hours='1.50', document_id=DOCUMENTS['hours_unowned_created']['document_id'])
created_entry(DOCUMENTS['hours_unowned_created']['document_id'], OWN)
# An instructor without a known owner holding the document, while its hours are referenced by nothing.
document('instructor_unowned_document')
link(INSTRUCTOR, INSTRUCTOR_UNOWNED, 'documents', DOCUMENTS['instructor_unowned_document']['document_id'])
created_entry(DOCUMENTS['instructor_unowned_document']['document_id'], OWN)

UNKNOWN = uid()

# --- authorization, methods, lookups -------------------------------------------------------------
METHODS = f'/document/{DOCUMENTS["archived_methods"]["document_id"]}/delete'
PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']
add('anonymous', 'anonymous', METHODS, 401, {'detail': 'Authentication credentials were not provided.'})
add('admin-without-session', 'admin', METHODS, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('restricted-collaborator', 'restricted_collaborator', METHODS, 403,
    {'detail': f"Access denied: No permission mapping for endpoint '{METHODS.strip('/')}'. Contact administrator to grant access."})
add('scoped-athlete', 'owner', METHODS, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('get-not-allowed', 'owner', METHODS, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('post-not-allowed', 'owner', METHODS, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'owner', METHODS, 200, {'name': 'Delete Document', 'description': '', 'renders': ['application/json'], 'parses': PARSES},
    method='OPTIONS')
add('invalid-uuid', 'owner', '/document/not-a-uuid/delete', 400, ['badly formed hexadecimal UUID string'])
add('unknown', 'owner', f'/document/{UNKNOWN}/delete', 404, NOT_FOUND)
add('athlete', 'athlete', 'archived_methods', 404, NOT_FOUND)
add('owner-without-association', 'owner_without_association', 'archived_methods', 404, NOT_FOUND)
add('deleted-association-owner', 'deleted_association_owner', 'archived_methods', 404, NOT_FOUND)

# --- documents of another association, or of nobody ---------------------------------------------
add('archive-of-another-association', 'owner', 'archived_foreign', 404, NOT_FOUND)
add('archive-shared-with-another-association', 'owner', 'archived_shared', 404, NOT_FOUND)
add('own-archive-by-another-owner', 'foreign_owner', 'archived_for_foreign_owner', 404, NOT_FOUND)
add('unreferenced-without-creation-entry', 'owner', 'loose_none', 404, NOT_FOUND)
add('unreferenced-created-for-another-association', 'owner', 'loose_foreign', 404, NOT_FOUND)
add('unreferenced-created-for-two-associations', 'owner', 'loose_both', 404, NOT_FOUND)
add('unreferenced-creation-entry-without-association', 'owner', 'loose_unindexed', 404, NOT_FOUND)
add('unreferenced-update-entry-only', 'owner', 'loose_update_only', 404, NOT_FOUND)
add('payment-of-another-association', 'owner', 'attachment_foreign', 404, NOT_FOUND)
add('payment-without-association', 'owner', 'attachment_unowned', 404, NOT_FOUND)
add('invoice-of-another-association', 'owner', 'invoice_pdf_foreign', 404, NOT_FOUND)
add('subscription-of-another-association', 'owner', 'subscription_pdf_foreign', 404, NOT_FOUND)
add('certificate-of-another-association-subscription', 'owner', 'certificate_foreign', 404, NOT_FOUND)
add('certificate-without-subscription', 'owner', 'certificate_loose', 404, NOT_FOUND)
add('file-of-another-association-subscription', 'owner', 'file_foreign', 404, NOT_FOUND)
add('response-of-another-association-module', 'owner', 'response_foreign', 404, NOT_FOUND)
add('instructor-of-another-owner', 'owner', 'instructor_document_foreign', 404, NOT_FOUND)
add('hours-of-instructor-without-association', 'owner', 'hours_unowned', 404, NOT_FOUND)


# --- deletes ------------------------------------------------------------------------------------
def archive_audit(key, name, date):
    return audit(ARCHIVE, key, {'sport_association_documents_archive_id': [key, 'None'], 'sport_association': [OWN, 'None'],
                                'document': [DOCUMENTS[name]['document_id'], 'None'], 'date': [date, 'None'], 'folder': ['None', 'None']},
                 OWN, 'changes_json.sport_association')


def file_audit(key, name, actor_association=OWN):
    return audit(FILE, key, {'subscription_file_id': [key, 'None'], 'subscription': [FILE_SUBSCRIPTION, 'None'],
                             'document': [DOCUMENTS[name]['document_id'], 'None'], 'creation_date': ['2026-03-01 10:00:00', 'None']},
                 actor_association, 'actor.sport_association')


def certificate_audit(key, name, *, user, expiration, competitive, notes):
    changes = {'medical_id': [key, 'None'], 'document': [DOCUMENTS[name]['document_id'], 'None'], 'user': [user or 'None', 'None'],
               'creation_date': ['2026-03-01 10:00:00', 'None'], 'competitive_medical_certificate': [competitive, 'None']}
    if expiration:
        changes['expiration_date'] = [expiration, 'None']
    if notes:
        changes['notes'] = [notes, 'None']
    return audit(CERTIFICATE, key, changes, OWN, 'actor.sport_association')


add('archive', 'owner', 'archived', 200, OK, effect=effect(
    deleted=[gone('archived'), (ARCHIVE, {KEYS[ARCHIVE]: ARCHIVED})],
    entries=[archive_audit(ARCHIVED, 'archived', '2026-03-02'), document_audit('archived')]))
add('archive-again', 'owner', 'archived', 404, NOT_FOUND)
add('two-archive-rows', 'owner', 'archived_twice', 200, OK, effect=effect(
    deleted=[gone('archived_twice'), *((ARCHIVE, {KEYS[ARCHIVE]: key}) for key in ARCHIVED_TWICE)],
    entries=[*(archive_audit(key, 'archived_twice', '2026-03-03') for key in sorted(ARCHIVED_TWICE, reverse=True)),
             document_audit('archived_twice')]))
# The collaborator is the audit actor and has no association of its own: the document entry gets no index row.
add('archive-by-collaborator', 'full_collaborator', 'archived_collaborator', 200, OK, effect=effect(
    deleted=[gone('archived_collaborator'), (ARCHIVE, {KEYS[ARCHIVE]: ARCHIVED_COLLABORATOR})],
    entries=[archive_audit(ARCHIVED_COLLABORATOR, 'archived_collaborator', '2026-03-06'),
             document_audit('archived_collaborator', actor_association=None)]))
add('unreferenced-created-for-the-association', 'owner', 'loose_own', 200, OK,
    effect=effect(deleted=[gone('loose_own')], entries=[document_audit('loose_own')]))
add('unreferenced-two-creation-entries', 'owner', 'loose_twice_own', 200, OK,
    effect=effect(deleted=[gone('loose_twice_own')], entries=[document_audit('loose_twice_own')]))
for name in ('attachment', 'attachment_deleted', 'attachment_group', 'attachment_unowned_created'):
    add('payment-' + name.replace('_', '-'), 'owner', name, 200, OK, effect=effect(
        deleted=[gone(name), unlinked(PAYMENT, PAYMENTS[name], 'attachments', name)], entries=[document_audit(name)]))
add('invoice-pdf', 'owner', 'invoice_pdf', 200, OK, effect=effect(
    deleted=[gone('invoice_pdf')], cleared=[(INVOICE, INVOICE_PDF, 'document_pdf_id')], entries=[document_audit('invoice_pdf')]))
add('invoice-attached', 'owner', 'invoice_attached', 200, OK, effect=effect(
    deleted=[gone('invoice_attached'), unlinked(INVOICE, INVOICE_ATTACHED, 'attached_documents', 'invoice_attached')],
    entries=[document_audit('invoice_attached')]))
add('supplier-invoice-pdf', 'owner', 'supplier_invoice', 200, OK, effect=effect(
    deleted=[gone('supplier_invoice')], cleared=[(SUPPLIER_INVOICE, SUPPLIER, 'document_pdf_id')], entries=[document_audit('supplier_invoice')]))
add('customer-invoice-pdf', 'owner', 'customer_invoice', 200, OK, effect=effect(
    deleted=[gone('customer_invoice')], cleared=[(CUSTOMER_INVOICE, CUSTOMER, 'pdf_id')], entries=[document_audit('customer_invoice')]))
# The subscription belongs to a group; the request names none, and one the caller does not have.
add('subscription-pdf', 'owner', 'subscription_pdf', 200, OK, headers={'X-Group-Id': IDS['foreign_group']}, effect=effect(
    deleted=[gone('subscription_pdf')], cleared=[(SUBSCRIPTION, SUBSCRIPTION_PDF, 'document_pdf_id')],
    entries=[document_audit('subscription_pdf')]))
add('certificate-of-own-subscription', 'owner', 'certificate', 200, OK, effect=effect(
    deleted=[gone('certificate'), (CERTIFICATE, {KEYS[CERTIFICATE]: CERTIFICATE_OWN})],
    cleared=[(SUBSCRIPTION, CERTIFICATE_SUBSCRIPTION, 'medical_id')],
    entries=[document_audit('certificate'),
             certificate_audit(CERTIFICATE_OWN, 'certificate', user=None, expiration='2027-01-31', competitive='False', notes=None)]))
add('certificate-without-subscription-created-for-the-association', 'owner', 'certificate_loose_created', 200, OK, effect=effect(
    deleted=[gone('certificate_loose_created'), (CERTIFICATE, {KEYS[CERTIFICATE]: CERTIFICATE_LOOSE})],
    entries=[document_audit('certificate_loose_created'),
             certificate_audit(CERTIFICATE_LOOSE, 'certificate_loose_created', user=IDS['athlete'], expiration=None, competitive='True',
                               notes='idoneo')]))
add('appointment', 'owner', 'appointment', 200, OK, effect=effect(
    deleted=[gone('appointment'), (APPOINTMENT, {KEYS[APPOINTMENT]: APPOINTMENT_ROW})],
    entries=[document_audit('appointment'),
             audit(APPOINTMENT, APPOINTMENT_ROW, {
                 'medical_appointments_id': [APPOINTMENT_ROW, 'None'], 'subscription': [APPOINTMENT_SUBSCRIPTION, 'None'],
                 'region': ['agonistica', 'None'], 'document': [DOCUMENTS['appointment']['document_id'], 'None'],
                 'date': ['2026-04-01', 'None'], 'sport': ['Nuoto', 'None'], 'meta': ['null', 'None'], 'address': ['Via Roma 1', 'None'],
                 'creation_date': ['2026-03-01 10:00:00', 'None']}, OWN, 'actor.sport_association')]))
add('subscription-file', 'owner', 'file', 200, OK, effect=effect(
    deleted=[gone('file'), (FILE, {KEYS[FILE]: FILE_ROW})], entries=[file_audit(FILE_ROW, 'file'), document_audit('file')]))
add('file-and-archive', 'owner', 'file_and_archive', 200, OK, effect=effect(
    deleted=[gone('file_and_archive'), (FILE, {KEYS[FILE]: BOTH_FILE}), (ARCHIVE, {KEYS[ARCHIVE]: BOTH_ARCHIVE})],
    entries=[archive_audit(BOTH_ARCHIVE, 'file_and_archive', '2026-03-09'), file_audit(BOTH_FILE, 'file_and_archive'),
             document_audit('file_and_archive')]))
for name, model, key, field in (('membership', MEMBERSHIP, MEMBERSHIP_ROW, 'attached_membership_documents'),
                                ('location', LOCATION, LOCATION_ROW, 'documents'), ('response', RESPONSE, RESPONSE_ROW, 'attachments'),
                                ('instructor_document', INSTRUCTOR, INSTRUCTOR_OWN, 'documents'),
                                ('instructor_unowned_document', INSTRUCTOR, INSTRUCTOR_UNOWNED, 'documents')):
    add(name.replace('_', '-'), 'owner', name, 200, OK, effect=effect(
        deleted=[gone(name), unlinked(model, key, field, name)], entries=[document_audit(name)]))
add('instructor-hours', 'owner', 'hours', 200, OK, effect=effect(
    deleted=[gone('hours')], cleared=[(HOURS, HOURS_OWN, 'document_id')], entries=[document_audit('hours')]))
add('hours-of-instructor-without-association-created-for-the-association', 'owner', 'hours_unowned_created', 200, OK, effect=effect(
    deleted=[gone('hours_unowned_created')], cleared=[(HOURS, HOURS_UNOWNED, 'document_id')],
    entries=[document_audit('hours_unowned_created')]))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'document delete: ownership walk, collector cascade and audit', 'ids': IDS, 'keys': KEYS,
    'rows': ROWS, 'links': LINKS, 'entries': ENTRIES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
