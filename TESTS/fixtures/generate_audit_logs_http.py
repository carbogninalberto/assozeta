"""Author the audit-logs list/detail HTTP sequence (audit-logs.md).

audit_log_list and audit_log_detail (BE/application/views/audit_views.py) are
DRF IsAuthenticated function views outside the permission registry. Both start
with SportAssociation.objects.get(user=request.user) (a miss answers 404
{"error": "Sport association not found"}). The detail needs an AuditLogIndex
row of that association for the entry (else 404 {"error": "Audit log not
found"}) and renders AuditLogEntrySerializer, without the list's noise filter.
The list reads the association's indexed entries minus accesstoken entries and
user entries whose changes have a last_login key; then the query[...] filters
(icontains search on object_repr and actor email/names; int(action), invalid
ignored; comma-separated lowercased content type models; actor_id through the
UUID field, a malformed one being an unhandled 500; exact object_pk; strptime
dates, 23:59:59 for date_to, invalid ignored), sort[field] in timestamp/action/
model (desc unless sort[sort] == 'asc') and KTDatatablePagination, whose meta
perpage is always the class page_size 10. minimal=true (any case) selects
AuditLogListMinimalSerializer.

AuditLogEntrySerializer resolves object descriptions and UUID values through
the default managers (group-aware with X-Group-Id, soft-delete aware), swallows
resolver exceptions (DoesNotExist of a field resolver is "[Eliminato: ...]"),
formats JSON fields (additional_fields, attendees, events, permissions) and
booleans in Italian, and lets formatter exceptions escape (a 500). Expected
bodies are authored from that source. The changes JSONField is jsonb: its keys
come back ordered by (length, bytes), which orders display_changes and the
"Modificato:" summary. The routes are reads: every case leaves the state
unchanged. Run with ``python3 -I``.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUTPUT = Path(__file__).with_name('audit_logs_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
_n = iter(range(996001, 996200))


def uid(prefix='a1000000'):
    return f'{prefix}-0000-4000-8000-{next(_n):012d}'


# Rows the resolvers read (seeded by the adapter, auditlog disabled).
R = {k: uid(p) for k, p in (
    ('user_named', 'a1000000'), ('user_blank', 'a1000000'), ('user_deleted', 'a1000000'),
    ('course', 'c0a5e001'), ('course_grouped', 'c0a5e002'), ('course_deleted', 'c0a5e003'), ('course_untitled', 'c0a5e004'),
    ('associate', 'a550c001'), ('associate_unnamed', 'a550c002'), ('family', 'fa000001'), ('tag', 'da000001'),
    ('subscription', '5b000001'), ('subscription_bare', '5b000002'),
    ('course_subscription', 'c5000001'), ('course_subscription_bare', 'c5000002'), ('course_subscription_deleted', 'c5000003'),
    ('payment', 'be000001'), ('instructor', '1e000001'), ('carnet', 'ca000001'), ('carnet_subscription', 'ca500001'),
    ('module', 'b0d00001'), ('registry', 'e1000001'), ('day', 'da100001'), ('calendar', 'ca1e0001'), ('reminder', 'e3000001'),
    ('camp', 'ca300001'), ('period', 'ca300002'), ('camp_subscription', 'ca300003'), ('membership', 'eb000001'),
    ('medical', 'ed000001'), ('invoice', 'f0000001'), ('message', 'e5000001'), ('message_bare', 'e5000002'),
    ('workflow', 'a0000001'), ('configuration', 'c0f00001'),
    ('event_registry', 'e7000001'), ('event_calendar', 'e7000002'), ('event_untitled', 'e7000003'), ('event_unknown', 'e7000004'),
    ('missing', 'deadbeef'), ('missing_two', 'feedface'), ('event_numeric', 'e7000005'))}
USERS = {
    'user_named': {'first_name': 'Mario', 'last_name': 'Rossi', 'email': 'mario.audit@example.test', 'deleted': False},
    'user_blank': {'first_name': '', 'last_name': '  ', 'email': 'blank.audit@example.test', 'deleted': False},
    'user_deleted': {'first_name': 'Gina', 'last_name': 'Neri', 'email': 'gina.audit@example.test', 'deleted': True},
}
EVENTS_REGISTRY = [{'event_id': R['event_registry'], 'title': 'Lezione', 'start': '2026-09-15T18:30:00Z'},
                   {'event_id': R['event_untitled'], 'start': 'domani'},
                   # fromisoformat ValueError (month 13): the non-str title itself is the resolved value.
                   {'event_id': R['event_numeric'], 'title': 5, 'start': '2026-13-01'}]
EVENTS_CALENDAR = [{'event_id': R['event_calendar'], 'title': 'Gara', 'start': '2026-10-01'}]
ROWS = {'ids': R, 'users': USERS, 'events_registry': EVENTS_REGISTRY, 'events_calendar': EVENTS_CALENDAR,
        'own_group': IDS['own_group'], 'own': OWN, 'foreign': FOREIGN}

MODEL_LABELS = {'course': 'Corso', 'associate': 'Associato', 'subscription': 'Iscrizione', 'coursesubscription': 'Iscrizione Corso',
                'payment': 'Pagamento', 'instructor': 'Istruttore', 'invoice': 'Ricevuta', 'attendanceday': 'Giornata Presenze',
                'attendanceregistry': 'Registro Presenze', 'globalcalendarevents': 'Evento Calendario', 'reminders': 'Promemoria',
                'campsandretreats': 'Campo/Ritiro', 'campsandretreatsperiod': 'Periodo Campo',
                'campsandretreatssubscription': 'Iscrizione Campo', 'subscriptionmembership': 'Tesseramento',
                'medicalcertificate': 'Certificato Medico', 'carnet': 'Carnet', 'carnetsubscription': 'Iscrizione Carnet',
                'module': 'Modulo', 'group': 'Gruppo', 'family': 'Famiglia', 'tags': 'Tag', 'user': 'Utente',
                'sportassociation': 'Associazione Sportiva', 'message': 'Messaggio', 'automationworkflow': 'Automazione',
                'communicationconfiguration': 'Configurazione Comunicazioni', 'accesstoken': 'Token Accesso',
                'associatetutorrelation': 'Associatetutorrelation', 'campsandretreatsperiodsservice': 'Campsandretreatsperiodsservice'}
ACTIONS = {0: 'Creazione', 1: 'Modifica', 2: 'Eliminazione'}
START = datetime(2026, 9, 28, 9, tzinfo=timezone.utc)


def jsonb_keys(changes):
    """jsonb object key order: shorter keys first, then bytewise."""
    return sorted(changes, key=lambda k: (len(k.encode()), k.encode()))


def show(label, key, old, new):
    return {'field': label, 'field_key': key, 'old': old, 'new': new, 'formatted': f'{label}: {old} → {new}'}


def single(label, key, value):
    return {'field': label, 'field_key': key, 'value': value, 'formatted': f'{label}: {value}'}


ENTRIES = {}
_id = iter(range(996000001, 996000200))
_slot = iter(range(200))


def entry(name, model, pk, *, action=1, repr_=None, changes=None, actor='user_named', actor_email='stale@example.test',
          remote='10.0.0.1', extra=None, association=OWN, app='application', at=None, description=None, enriched=None,
          display=None, indexed=True):
    slot = next(_slot)
    stamp = at or (START + timedelta(hours=3 * slot)).strftime('%Y-%m-%dT%H:%M:%SZ')
    ENTRIES[name] = {'id': next(_id), 'app': app, 'model': model, 'pk': pk,
                     'repr': f'{model.capitalize()} object ({pk})' if repr_ is None else repr_, 'action': action,
                     'changes': changes, 'actor': actor, 'actor_email': actor_email, 'remote': remote, 'extra': extra,
                     'association': association, 'timestamp': stamp, 'indexed': indexed,
                     'description': description, 'enriched': enriched, 'display': display}


N = 'None'
missing8 = lambda key: f'[Eliminato: {R[key][:8]}...]'
short = lambda key: R[key][:8]

# --- Entries in timestamp order (3 hours apart unless pinned) ---------------------------------------------
# events formatter with a non-str start: start.replace raises AttributeError (unhandled, 500); oldest entry.
entry('events-crash', 'attendanceregistry', R['registry'], changes={'events': [N, '[{"title": "X", "start": 5}]']},
      description='Registro Presenze - Nuoto è')
entry('course-create', 'course', R['course'], action=0,
      changes={'title': [N, 'Nuoto è'], 'deleted': [N, 'False'], 'course_id': [N, R['course']]},
      description='Corso - Nuoto è',
      enriched={'title': [N, 'Nuoto è'], 'deleted': ['N/D', 'No'], 'course_id': [N, 'Nuoto è']},
      display=[show('Titolo', 'title', 'N/D', 'Nuoto è'), show('Eliminato', 'deleted', 'N/D', 'No'),
               show('Course Id', 'course_id', 'N/D', 'Nuoto è')])
entry('course-grouped', 'course', R['course_grouped'],
      changes={'course': [N, R['course_grouped']], 'associate': [N, R['associate']], 'subscription': [N, R['subscription']]},
      description='Corso - Gruppo corso',
      enriched={'course': [N, 'Gruppo corso'], 'associate': [N, 'Luca Bianchi'], 'subscription': [N, 'Luca Bianchi - Iscrizione']},
      display=[show('Corso', 'course', 'N/D', 'Gruppo corso'), show('Associato', 'associate', 'N/D', 'Luca Bianchi'),
               show('Iscrizione', 'subscription', 'N/D', 'Luca Bianchi - Iscrizione')])
entry('course-deleted', 'course', R['course_deleted'], repr_='Vecchio corso', changes={'title': ['Vecchio', 'Vecchio 2']},
      description='Vecchio corso', enriched={'title': ['Vecchio', 'Vecchio 2']},
      display=[show('Titolo', 'title', 'Vecchio', 'Vecchio 2')])
entry('associate-update', 'associate', R['associate'],
      changes={'first_name': ['Luc', 'Luca'], 'family': [N, R['family']], 'group': [N, IDS['own_group']], 'tag_id': [R['tag'], R['missing']]},
      description='Luca Bianchi',
      enriched={'first_name': ['Luc', 'Luca'], 'family': [N, R['family']], 'group': [N, 'Scope own group'],
                'tag_id': [R['tag'], missing8('missing')]},
      display=[show('Gruppo', 'group', 'N/D', 'Scope own group'), show('Famiglia', 'family', 'N/D', R['family']),
               show('Tag Id', 'tag_id', R['tag'], missing8('missing')), show('Nome', 'first_name', 'Luc', 'Luca')])
entry('associate-delete', 'associate', R['associate_unnamed'], action=2, changes={'associate_id': [R['associate_unnamed'], N]},
      description='None Verdi', enriched={'associate_id': ['None Verdi', N]},
      display=[show('Associate Id', 'associate_id', 'None Verdi', 'N/D')], actor=None, actor_email='gone@example.test', remote=None)
entry('subscription', 'subscription', R['subscription'], action=0,
      changes={'associate': [N, R['associate']], 'subscription_type': [N, 'Annuale']},
      description='Iscrizione - Luca Bianchi',
      enriched={'associate': [N, 'Luca Bianchi'], 'subscription_type': [N, 'Annuale']},
      display=[show('Associato', 'associate', 'N/D', 'Luca Bianchi'), show('Tipo iscrizione', 'subscription_type', 'N/D', 'Annuale')])
entry('subscription-bare', 'subscription', R['subscription_bare'], changes={'subscription': [R['subscription_bare'], N]},
      description=f"Iscrizione #{short('subscription_bare')}", enriched={'subscription': [R['subscription_bare'], N]},
      display=[show('Iscrizione', 'subscription', R['subscription_bare'], 'N/D')], remote='2001:db8::1')
entry('course-subscription', 'coursesubscription', R['course_subscription'],
      changes={'paid': ['False', 'True'], 'course_subscription': [N, R['course_subscription']]},
      description='Luca Bianchi - Nuoto è',
      enriched={'paid': ['No', 'Sì'], 'course_subscription': [N, 'Luca Bianchi - Nuoto è']},
      display=[show('Pagato', 'paid', 'No', 'Sì'), show('Course Subscription', 'course_subscription', 'N/D', 'Luca Bianchi - Nuoto è')])
entry('course-subscription-bare', 'coursesubscription', R['course_subscription_bare'],
      changes={'course_subscription_id': [R['course_subscription_bare'], R['course_subscription_deleted']]},
      description=f"Iscrizione Corso #{short('course_subscription_bare')}",
      enriched={'course_subscription_id': [R['course_subscription_bare'], missing8('course_subscription_deleted')]},
      display=[show('Course Subscription Id', 'course_subscription_id', R['course_subscription_bare'], missing8('course_subscription_deleted'))])
entry('course-subscription-deleted', 'coursesubscription', R['course_subscription_deleted'], action=2, changes={},
      description=f"Iscrizione Corso #{short('course_subscription_deleted')}", enriched=None, display=None)
entry('payment', 'payment', R['payment'], changes={'amount': ['10.00', '12.50'], 'payment_id': [N, R['payment']]},
      description='Pagamento - N/D (€12.50)',
      enriched={'amount': ['10.00', '12.50'], 'payment_id': [N, 'Pagamento - €12.50']},
      display=[show('Importo', 'amount', '10.00', '12.50'), show('Payment Id', 'payment_id', 'N/D', 'Pagamento - €12.50')])
entry('instructor', 'instructor', R['instructor'], changes={'instructor': [N, R['instructor']]},
      description='Istruttore - Anna Gialli', enriched={'instructor': [N, 'Anna Gialli']},
      display=[show('Istruttore', 'instructor', 'N/D', 'Anna Gialli')], actor='user_blank')
entry('invoice', 'invoice', R['invoice'], changes={'invoice_id': [N, R['invoice']], 'payment': [R['missing_two'], N]},
      description=f"Ricevuta #{short('invoice')}",
      enriched={'invoice_id': [N, R['invoice']], 'payment': [missing8('missing_two'), N]},
      display=[show('Pagamento', 'payment', missing8('missing_two'), 'N/D'), show('Invoice Id', 'invoice_id', 'N/D', R['invoice'])])
attendees = json.dumps([{'course_subscription_id': R['course_subscription']}, {'course_subscription_id': R['course_subscription_bare']},
                        {'course_subscription_id': R['missing']}])
entry('attendance-day', 'attendanceday', R['day'],
      changes={'attendees': ['[]', attendees], 'associated_event': [N, R['event_registry']], 'expected_absences': [N, '[1, 2, 3, 4]']},
      description='Presenze - Giornata (15/09/2026)',
      enriched={'attendees': ['Nessun presente', 'Luca Bianchi, Atleta, Atleta'], 'associated_event': [N, 'Lezione - 15/09/2026 18:30'],
                'expected_absences': [N, '4 presenti']},
      display=[show('Presenti', 'attendees', 'Nessun presente', 'Luca Bianchi, Atleta, Atleta'),
               show('Evento associato', 'associated_event', 'N/D', 'Lezione - 15/09/2026 18:30'),
               show('Assenze previste', 'expected_absences', 'N/D', '4 presenti')])
events_new = json.dumps([{'title': 'Lezione', 'start': '2026-09-15T18:30:00Z'}, {'title': 'Libera'}])
entry('attendance-registry', 'attendanceregistry', R['registry'],
      changes={'events': ['[{}, {}, {}]', events_new], 'attendance_registry': [N, R['registry']]},
      description='Registro Presenze - Nuoto è',
      enriched={'events': ['3 eventi', 'Lezione (15/09/2026), Libera'], 'attendance_registry': [N, 'Registro - Nuoto è']},
      display=[show('Eventi', 'events', '3 eventi', 'Lezione (15/09/2026), Libera'),
               show('Registro presenze', 'attendance_registry', 'N/D', 'Registro - Nuoto è')])
# datetime.fromisoformat reads more than the editor's text: basic dates, ISO week dates, any separator, longer fractions, offsets.
for tag, events, text in (
        ('forms-a', [('Base', '20260916T1830'), ('Settimana', '2026-W38-3 10:00')], 'Base (16/09/2026), Settimana (16/09/2026)'),
        ('forms-b', [('Frazione', '2026-09-17x18:30:00.1234567+05:30'), ('Fuso', '2026-09-18 18:30+0530')],
         'Frazione (17/09/2026), Fuso (18/09/2026)'),
        ('forms-c', [('Mezzanotte', '2026-09-19T24:00'), ('Giorno', '2026-09-31')], 'Mezzanotte, Giorno'),
        ('forms-d', [('Cifre', '\uff12\uff10\uff12\uff16-09-20'), ('Vuota', '2026-09-21T')], 'Cifre, Vuota')):
    entry('registry-' + tag, 'attendanceregistry', R['registry'],
          changes={'events': [N, json.dumps([{'title': t, 'start': st} for t, st in events])]},
          description='Registro Presenze - Nuoto è', enriched={'events': [N, text]},
          display=[show('Eventi', 'events', 'N/D', text)])
entry('calendar', 'globalcalendarevents', R['calendar'],
      changes={'event_id': [R['event_unknown'], R['event_calendar']], 'global_calendar_id': [N, R['calendar']],
               'associated_event': [R['event_untitled'], N]},
      description=f"Calendario #{short('calendar')}",
      enriched={'event_id': [R['event_unknown'], 'Gara - 01/10/2026 00:00'], 'global_calendar_id': [N, f"Calendario #{short('calendar')}"],
                'associated_event': ['Evento', N]},
      display=[show('Event Id', 'event_id', R['event_unknown'], 'Gara - 01/10/2026 00:00'),
               show('Evento associato', 'associated_event', 'Evento', 'N/D'),
               show('Global Calendar Id', 'global_calendar_id', 'N/D', f"Calendario #{short('calendar')}")])
entry('event-numeric-title', 'attendanceday', R['day'], changes={'event_id': [N, R['event_numeric']]},
      description='Presenze - Giornata (15/09/2026)', enriched={'event_id': [N, 5]}, display=[show('Event Id', 'event_id', 'N/D', '5')])
entry('reminder', 'reminders', R['reminder'], changes={'reminders_id': [N, R['reminder']]},
      description='Promemoria - Promemoria gara', enriched={'reminders_id': [N, 'Promemoria gara - 02/10/2026']},
      display=[show('Reminders Id', 'reminders_id', 'N/D', 'Promemoria gara - 02/10/2026')])
entry('camp', 'campsandretreats', R['camp'], changes={'camps_and_retreats': [N, R['camp']]},
      description='Campo - Campo estivo', enriched={'camps_and_retreats': [N, 'Campo estivo']},
      display=[show('Camps And Retreats', 'camps_and_retreats', 'N/D', 'Campo estivo')])
entry('camp-period', 'campsandretreatsperiod', R['period'], changes={'camps_and_retreats_period_id': [N, R['period']]},
      description='Periodo - Campo estivo (01/07/2026)', enriched={'camps_and_retreats_period_id': [N, 'Campo estivo - 01/07/2026']},
      display=[show('Camps And Retreats Period Id', 'camps_and_retreats_period_id', 'N/D', 'Campo estivo - 01/07/2026')])
entry('camp-subscription', 'campsandretreatssubscription', R['camp_subscription'],
      changes={'camps_and_retreats_subscription_id': [R['camp_subscription'], R['missing']]},
      description=f"Iscrizione Campo #{short('camp_subscription')}",
      enriched={'camps_and_retreats_subscription_id': [R['camp_subscription'], missing8('missing')]},
      display=[show('Camps And Retreats Subscription Id', 'camps_and_retreats_subscription_id', R['camp_subscription'], missing8('missing'))])
entry('membership', 'subscriptionmembership', R['membership'], changes={'subscription_membership': [N, R['membership']]},
      description='Tesseramento - Luca Bianchi', enriched={'subscription_membership': [N, 'Luca Bianchi - Tesseramento']},
      display=[show('Subscription Membership', 'subscription_membership', 'N/D', 'Luca Bianchi - Tesseramento')])
entry('medical', 'medicalcertificate', R['medical'], changes={'medical_certificate_id': [N, R['medical']], 'expiration_date': [N, '2027-01-31']},
      description=f"Certificato #{short('medical')}",
      enriched={'medical_certificate_id': [N, R['medical']], 'expiration_date': [N, '2027-01-31']},
      display=[show('Data scadenza', 'expiration_date', 'N/D', '2027-01-31'), show('Medical Certificate Id', 'medical_certificate_id', 'N/D', R['medical'])])
entry('carnet', 'carnet', R['carnet'], changes={'carnet_id': [N, R['carnet']], 'carnet_subscription_id': [N, R['carnet_subscription']]},
      description='Carnet - Carnet 10',
      enriched={'carnet_id': [N, 'Carnet 10'], 'carnet_subscription_id': [N, R['carnet_subscription']]},
      display=[show('Carnet Id', 'carnet_id', 'N/D', 'Carnet 10'), show('Carnet Subscription Id', 'carnet_subscription_id', 'N/D', R['carnet_subscription'])])
entry('carnet-subscription', 'carnetsubscription', R['carnet_subscription'], repr_='Iscrizione carnet', action=0,
      changes={'module_id': [N, R['module']]}, description='Iscrizione carnet',
      enriched={'module_id': [N, 'Modulo privacy']}, display=[show('Module Id', 'module_id', 'N/D', 'Modulo privacy')])
fields_new = json.dumps([{'props': {'label': 'Taglia', 'value': 'XL'}, 'type': 'text'}, {'props': {'label': 'Vuoto', 'value': ''}},
                         {'props': {'value': 7}}, 'x'])
entry('module', 'module', R['module'], changes={'additional_fields': [N, fields_new], 'module_id': [R['missing'], R['module']]},
      description='Modulo - Modulo privacy',
      enriched={'additional_fields': [N, 'Taglia: XL, Campo: 7'], 'module_id': [missing8('missing'), 'Modulo privacy']},
      display=[show('Module Id', 'module_id', missing8('missing'), 'Modulo privacy'),
               show('Campi aggiuntivi', 'additional_fields', 'N/D', 'Taglia: XL, Campo: 7')])
entry('group', 'group', IDS['own_group'], changes={'name': ['Vecchio', 'Scope own group']}, description='Gruppo - Scope own group',
      enriched={'name': ['Vecchio', 'Scope own group']}, display=[show('Nome', 'name', 'Vecchio', 'Scope own group')])
entry('family', 'family', R['family'], repr_='', action=0, changes={'family_id': [N, R['family']]},
      description=f"Famiglia #{R['family']}", enriched={'family_id': [N, R['family']]},
      display=[show('Family Id', 'family_id', 'N/D', R['family'])])
entry('tag', 'tags', R['tag'], repr_=f"Tags object ({R['tag']})", changes={'tag': [N, R['tag']]},
      description=f"Tag #{short('tag')}", enriched={'tag': [N, R['tag']]}, display=[show('Tag', 'tag', 'N/D', R['tag'])])
permissions = json.dumps(['association.courses.read', 'bookeeping.payments.read', 'custom.thing.EXPORT_all'])
entry('user-named', 'user', R['user_named'],
      changes={'collaborator_permissions': ['[]', permissions], 'archived': ['false', 'TRUE'], 'active': [False, True]},
      description='Utente - Mario Rossi',
      enriched={'collaborator_permissions': ['Nessun permesso', 'Lettura corsi, Lettura pagamenti, Export_all'],
                'archived': ['No', 'Sì'], 'active': ['No', 'Sì']},
      display=[show('Attivo', 'active', 'No', 'Sì'), show('Archiviato', 'archived', 'No', 'Sì'),
               show('Permessi collaboratore', 'collaborator_permissions', 'Nessun permesso', 'Lettura corsi, Lettura pagamenti, Export_all')])
entry('user-blank', 'user', R['user_blank'], action=0, changes={'email': [N, 'blank.audit@example.test']},
      description='Utente - blank.audit@example.test', enriched={'email': [N, 'blank.audit@example.test']},
      display=[show('Email', 'email', 'N/D', 'blank.audit@example.test')], actor='user_deleted')
entry('user-deleted', 'user', R['user_deleted'], repr_='Gina Neri', changes=None, description='Gina Neri', enriched=None, display=None)
entry('user-login', 'user', R['user_named'], changes={'last_login': [N, '2026-09-30 10:00:00']}, description='Utente - Mario Rossi',
      enriched={'last_login': [N, '2026-09-30 10:00:00']}, display=[show('Last Login', 'last_login', 'N/D', '2026-09-30 10:00:00')])
entry('association', 'sportassociation', OWN, changes={'denomination': ['Vecchia', 'Message reads own']},
      description='Associazione - Message reads own', enriched={'denomination': ['Vecchia', 'Message reads own']},
      display=[show('Denomination', 'denomination', 'Vecchia', 'Message reads own')])
entry('message', 'message', R['message'], app='communications', action=0, changes={'subject': [N, 'Avviso']},
      description='Messaggio - Avviso', enriched={'subject': [N, 'Avviso']}, display=[show('Subject', 'subject', 'N/D', 'Avviso')])
entry('message-bare', 'message', R['message_bare'], app='communications', action=3, changes={'type': ['EMAIL', 'SMS']},
      description='Messaggio SMS', enriched={'type': ['EMAIL', 'SMS']}, display=[show('Tipo', 'type', 'EMAIL', 'SMS')])
entry('workflow', 'automationworkflow', R['workflow'], app='communications', changes={'enabled': ['1', 'no']},
      description='Automazione - Flusso benvenuto', enriched={'enabled': ['Sì', 'No']}, display=[show('Abilitato', 'enabled', 'Sì', 'No')])
entry('configuration', 'communicationconfiguration', R['configuration'], app='communications', changes={'meta': ['{}', '[]']},
      description='Configurazione Email', enriched={'meta': [{}, '(vuoto)']}, display=[show('Metadati', 'meta', '0 campi', '(vuoto)')])
entry('unmapped-model', 'associatetutorrelation', R['missing'], repr_='Sconto 50%_x', changes=['a', 'b'],
      description='Sconto 50%_x', enriched=['a', 'b'], display=[])
entry('access-token', 'accesstoken', R['missing'], app='oauth2_provider', action=0, repr_='token', changes={'token': [N, 'x']},
      description='token', enriched={'token': [N, 'x']}, display=[show('Token', 'token', 'N/D', 'x')])
mixed = {
    'notes': ['', '""'],
    'scores': ['[1, 2.50, true, null]', '["a", "b"]'],
    'labelled': ['{"label": "L", "value": 3}', '{"course": "' + R['course'].upper() + '", "active": "yes", "": "' + R['course'] + '"}'],
    'numbers': [5, 7.0],
    'flags': [True, False],
    'sport_association': [N, OWN],
    'broken': ['[not json', '[]'],
    'm2m': {'type': 'm2m', 'operation': 'add', 'objects': ['a', 'b', 'c', 'd', 'e', 'f']},
    'single': ['solo'],
    # UUID_PATTERN's $ accepts a trailing newline; the UUID field then rejects it (not DoesNotExist): unchanged.
    'course_id': [N, R['course'] + '\n'],
}
entry('mixed', 'campsandretreatsperiodsservice', R['missing_two'], changes=mixed, extra={'reason': 'import', 'count': 2},
      description=f"Campsandretreatsperiodsservice #{short('missing_two')}",
      enriched={'notes': ['', '""'], 'scores': [[1, 2.5, 'Sì', None], ['a', 'b']],
                'labelled': [{'label': 'L', 'value': 3}, {'course': 'Nuoto è', 'active': 'Sì', '': R['course']}],
                'numbers': [5, 7.0], 'flags': ['Sì', 'No'], 'sport_association': [N, OWN], 'broken': ['[not json', '(vuoto)'],
                'm2m': {'type': 'm2m', 'operation': 'add', 'objects': ['a', 'b', 'c', 'd', 'e', 'f']}, 'single': ['solo'],
                'course_id': [N, R['course'] + '\n']},
      display=[single('M2M', 'm2m', '3 campi'), show('Flags', 'flags', 'Sì', 'No'), show('Note', 'notes', '(vuoto)', '(vuoto)'),
               show('Broken', 'broken', '[not json', '(vuoto)'), show('Scores', 'scores', '4 elementi', 'a, b'),
               single('Single', 'single', 'solo'), show('Numbers', 'numbers', '5', '7.0'),
               show('Labelled', 'labelled', 'L: 3', '3 campi'), show('Course Id', 'course_id', 'N/D', R['course'] + '\n'),
               show('Associazione', 'sport_association', 'N/D', OWN)])
entry('date-edge-included', 'course', R['course_untitled'], repr_='Edge in', changes={'course': [N, R['course_untitled']]},
      at='2026-09-29T23:59:59Z', description='Corso - None', enriched={'course': [N, R['course_untitled']]},
      display=[show('Corso', 'course', 'N/D', R['course_untitled'])])
entry('date-edge-excluded', 'course', R['course_untitled'], repr_='Edge out', changes={'course': [N, R['course_untitled']]},
      at='2026-09-29T23:59:59.500000Z', description='Corso - None', enriched={'course': [N, R['course_untitled']]},
      display=[show('Corso', 'course', 'N/D', R['course_untitled'])])
# Not visible to the owner.
entry('foreign', 'course', R['course'], association=FOREIGN, repr_='Corso estero', changes={'title': ['A', 'B']},
      description='Corso - Nuoto è', enriched={'title': ['A', 'B']}, display=[show('Titolo', 'title', 'A', 'B')])
entry('unindexed', 'course', R['course'], repr_='Senza indice', changes=None, indexed=False, description='Corso - Nuoto è')
# Formatter exceptions Django does not catch (500), pinned before every other entry.
entry('props-crash', 'module', R['module'], changes={'additional_fields': [N, '[{"props": "x"}]']}, at='2026-09-20T10:00:00Z',
      description='Modulo - Modulo privacy')
entry('permissions-crash', 'user', R['user_named'], changes={'collaborator_permissions': [N, '[1]']}, at='2026-09-20T11:00:00Z',
      description='Utente - Mario Rossi')
entry('events-title-crash', 'attendanceregistry', R['registry'], changes={'events': [N, '[{"title": 5}]']}, at='2026-09-20T12:00:00Z',
      description='Registro Presenze - Nuoto è')
# json.loads reads NaN, Infinity and lone surrogate escapes: a value that reaches enriched_changes fails the strict renderer.
entry('nan-meta-crash', 'module', R['module'], changes={'meta': [N, '{"v": NaN}']}, at='2026-09-20T13:00:00Z',
      description='Modulo - Modulo privacy')
entry('infinity-meta-crash', 'module', R['module'], changes={'meta': [N, '[1, -Infinity]']}, at='2026-09-20T14:00:00Z',
      description='Modulo - Modulo privacy')
entry('surrogate-meta-crash', 'module', R['module'], changes={'meta': [N, '{"v": "\\ud800"}']}, at='2026-09-20T15:00:00Z',
      description='Modulo - Modulo privacy')
# Read by the additional_fields formatter, which only prints label and value: the answer is a plain string.
entry('nan-additional-fields', 'module', R['module'],
      changes={'additional_fields': [N, '[{"props": {"label": "Peso", "value": NaN}}]']}, at='2026-09-20T16:00:00Z',
      description='Modulo - Modulo privacy', enriched={'additional_fields': [N, 'Peso: nan']},
      display=[show('Campi aggiuntivi', 'additional_fields', 'N/D', 'Peso: nan')])
entry('surrogate-unused-additional-fields', 'module', R['module'],
      changes={'additional_fields': [N, '[{"props": {"label": "A", "value": "B"}, "note": "\\ud800"}]']}, at='2026-09-20T17:00:00Z',
      description='Modulo - Modulo privacy', enriched={'additional_fields': [N, 'A: B']},
      display=[show('Campi aggiuntivi', 'additional_fields', 'N/D', 'A: B')])
ENTRIES['events-crash']['timestamp'] = '2026-09-20T09:00:00Z'
CRASHES = ('events-crash', 'props-crash', 'permissions-crash', 'events-title-crash', 'nan-meta-crash', 'infinity-meta-crash',
           'surrogate-meta-crash')

for e in ENTRIES.values():
    if e['changes'] is not None and isinstance(e['changes'], dict):
        assert set(jsonb_keys(e['changes'])) == set(e['changes'])
# display rows follow jsonb key order; check the authored lists against it.
for name, e in ENTRIES.items():
    if isinstance(e['changes'], dict) and e['changes'] and e['display'] is not None:
        assert [row['field_key'] for row in e['display']] == jsonb_keys(e['changes']), name


def actor_fields(e):
    if e['actor'] is None:
        return None, e['actor_email']
    user = USERS[e['actor']]
    name = f"{user['first_name']} {user['last_name']}".strip()
    return name or user['email'], user['email']


def summary(e):
    changes = e['changes']
    if not changes:
        return None
    if e['action'] == 0:
        return 'Record creato'
    if e['action'] == 2:
        return 'Record eliminato'
    if e['action'] == 1:
        keys = jsonb_keys(changes) if isinstance(changes, dict) else []
        if not keys:
            return 'Nessuna modifica'
        return f"Modificato: {', '.join(keys)}" if len(keys) <= 3 else f'Modificati {len(keys)} campi'
    return None


def full(name):
    e = ENTRIES[name]
    actor_name, actor_email = actor_fields(e)
    return {'id': e['id'], 'action': e['action'], 'action_label': ACTIONS.get(e['action'], 'Sconosciuto'), 'timestamp': e['timestamp'],
            'object_pk': e['pk'], 'object_repr': e['repr'], 'object_description': e['description'], 'model_name': e['model'],
            'model_verbose_name': MODEL_LABELS[e['model']], 'actor_name': actor_name, 'actor_email': actor_email,
            'remote_addr': e['remote'], 'changes': e['changes'], 'enriched_changes': e['enriched'] if e['changes'] else None,
            'display_changes': e['display'] if e['changes'] else None, 'changes_summary': summary(e), 'additional_data': e['extra']}


def minimal(name):
    e = ENTRIES[name]
    return {'id': e['id'], 'action': e['action'], 'action_label': ACTIONS.get(e['action'], 'Sconosciuto'), 'timestamp': e['timestamp'],
            'object_pk': e['pk'], 'object_repr': e['repr'], 'model_verbose_name': MODEL_LABELS[e['model']], 'actor_name': actor_fields(e)[0]}


def stamp_of(e):
    """Comparable 'YYYY-MM-DDTHH:MM:SS.ffffff' of an entry timestamp."""
    text = e['timestamp'].removesuffix('Z')
    return text if '.' in text else text + '.000000'


def listed(association=OWN):
    """The list's base queryset: indexed to the association, minus the noise filter (a NULL changes is kept)."""
    rows = [n for n, e in ENTRIES.items() if e['indexed'] and e['association'] == association and e['model'] != 'accesstoken'
            and not (e['model'] == 'user' and isinstance(e['changes'], dict) and 'last_login' in e['changes'])]
    return sorted(rows, key=lambda n: stamp_of(ENTRIES[n]), reverse=True)


def page(names, number=1, size=10, render=minimal):
    total = len(names)
    pages = max(1, -(-total // size))
    chunk = names[(number - 1) * size:number * size]
    return {'data': [render(n) for n in chunk], 'meta': {'total': total, 'page': number, 'pages': pages, 'perpage': 10}}


cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None):
    cases.append({'name': f'audit-logs-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response})


LIST = '/audit-logs/list'
detail = lambda name: f"/audit-logs/{ENTRIES[name]['id']}/detail"
NO_ASSOCIATION = {'error': 'Sport association not found'}
NOT_FOUND = {'error': 'Audit log not found'}
INVALID_PAGE = {'detail': 'Invalid page.'}
unmapped = lambda path: {'detail': f"Access denied: No permission mapping for endpoint '{path.strip('/')}'. Contact administrator to grant access."}
ALL = listed()
by = lambda pred: [n for n in ALL if pred(ENTRIES[n])]

# --- identity and transport (both routes) ---
for kind, path in (('list', LIST), ('detail', detail('course-create'))):
    add(f'{kind}-anonymous', 'anonymous', path, 401, {'detail': 'Authentication credentials were not provided.'})
    add(f'{kind}-admin-without-session', 'admin', path, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add(f'{kind}-restricted-unmapped', 'restricted_collaborator', path, 403, unmapped(path))
    add(f'{kind}-owner-scoped-athlete-denied', 'owner', path, 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
        headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
    add(f'{kind}-owner-without-association', 'owner_without_association', path, 404, NO_ASSOCIATION)
    add(f'{kind}-athlete-without-association', 'athlete', path, 404, NO_ASSOCIATION)
    add(f'{kind}-wrong-method', 'owner', path, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
    add(f'{kind}-delete-method', 'owner', path, 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('list-options', 'owner', LIST, 200, {'name': 'Audit Log List', 'description': (
    'API endpoint to list audit logs for the current sport association.\n\nQuery parameters:\n'
    '- pagination[page]: Page number (default: 1)\n- pagination[perpage]: Items per page (default: 10)\n'
    '- query[generalSearch]: Search in object_repr, actor name/email\n'
    '- query[action]: Filter by action type (0=CREATE, 1=UPDATE, 2=DELETE)\n'
    '- query[model]: Filter by model name(s), comma-separated\n- query[actor_id]: Filter by actor user ID\n'
    '- query[object_id]: Filter by specific object ID (UUID) - shows all changes to that object\n'
    '- query[date_from]: Filter from date (YYYY-MM-DD)\n- query[date_to]: Filter to date (YYYY-MM-DD)\n'
    '- sort[field]: Sort field (timestamp, action, model)\n- sort[sort]: Sort direction (asc, desc)\n'
    '- minimal: If "true", return minimal fields only (default: false)'),
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')
add('detail-options', 'owner', detail('course-create'), 200, {'name': 'Audit Log Detail',
    'description': 'API endpoint to get detailed information about a single audit log entry.', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# --- list: actors and scoping ---
add('list-owner-full', 'owner', LIST, 200, page(ALL, render=full))
add('list-owner-minimal', 'owner', LIST + '?minimal=true', 200, page(ALL))
add('list-full-collaborator', 'full_collaborator', LIST + '?minimal=TRUE', 200, page(ALL))
add('list-admin-impersonating-owner', 'admin', LIST + '?minimal=True', 200, page(ALL),
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('list-foreign-owner', 'foreign_owner', LIST, 200, page(listed(FOREIGN), render=full))
add('list-minimal-one-is-full', 'owner', LIST + '?minimal=1&pagination[perpage]=2', 200, page(ALL, size=2, render=full))

# --- list: pagination (meta perpage is always the class page_size) ---
add('list-page-2', 'owner', LIST + '?minimal=true&pagination[page]=2', 200, page(ALL, 2))
add('list-page-last', 'owner', LIST + '?minimal=true&pagination[page]=last', 200, page(ALL, -(-len(ALL) // 10)))
add('list-page-empty-is-first', 'owner', LIST + '?minimal=true&pagination[page]=', 200, page(ALL))
add('list-perpage-3', 'owner', LIST + '?minimal=true&pagination[perpage]=3&pagination[page]=2', 200, page(ALL, 2, 3))
add('list-perpage-python-int', 'owner', LIST + '?minimal=true&pagination[perpage]=%202_0%20', 200, page(ALL, 1, 20))
add('list-perpage-zero-default', 'owner', LIST + '?minimal=true&pagination[perpage]=0', 200, page(ALL))
add('list-perpage-negative-default', 'owner', LIST + '?minimal=true&pagination[perpage]=-4', 200, page(ALL))
add('list-perpage-text-default', 'owner', LIST + '?minimal=true&pagination[perpage]=abc', 200, page(ALL))
add('list-perpage-beyond-int64', 'owner', LIST + '?minimal=true&pagination[perpage]=99999999999999999999999', 200,
    page(ALL, 1, 10 ** 23))
add('list-page-unicode-digit', 'owner', LIST + '?minimal=true&pagination[page]=%EF%BC%92', 200, page(ALL, 2))
add('list-page-zero', 'owner', LIST + '?minimal=true&pagination[page]=0', 404, INVALID_PAGE)
add('list-page-beyond', 'owner', LIST + '?minimal=true&pagination[page]=99', 404, INVALID_PAGE)
add('list-page-text', 'owner', LIST + '?minimal=true&pagination[page]=abc', 404, INVALID_PAGE)
add('list-page-float', 'owner', LIST + '?minimal=true&pagination[page]=1.0', 404, INVALID_PAGE)
add('list-repeated-page-last-wins', 'owner', LIST + '?minimal=true&pagination[page]=9&pagination[page]=2', 200, page(ALL, 2))
add('list-full-last-page-formatter-crash', 'owner', LIST + '?pagination[page]=last', 500, SERVER)

# --- list: filters ---
add('list-action-update', 'owner', LIST + '?minimal=true&query[action]=%2B1&pagination[perpage]=50', 200,
    page(by(lambda e: e['action'] == 1), size=50))
add('list-action-delete', 'owner', LIST + '?minimal=true&query[action]=2', 200, page(by(lambda e: e['action'] == 2)))
add('list-action-unicode-digit', 'owner', LIST + '?minimal=true&query[action]=%EF%BC%92', 200, page(by(lambda e: e['action'] == 2)))
add('list-action-invalid-ignored', 'owner', LIST + '?minimal=true&query[action]=x', 200, page(ALL))
add('list-action-empty-ignored', 'owner', LIST + '?minimal=true&query[action]=', 200, page(ALL))
add('list-action-out-of-range', 'owner', LIST + '?minimal=true&query[action]=99999999999999999999', 200, page([]))
add('list-action-negative', 'owner', LIST + '?minimal=true&query[action]=-1', 200, page([]))
add('list-model-csv', 'owner', LIST + '?minimal=true&query[model]=%20Course%20,PAYMENT,nothing', 200,
    page(by(lambda e: e['model'] in ('course', 'payment'))))
add('list-model-unknown', 'owner', LIST + '?minimal=true&query[model]=nothing', 200, page([]))
add('list-model-communications', 'owner', LIST + '?minimal=true&query[model]=message', 200, page(by(lambda e: e['model'] == 'message')))
add('list-actor', 'owner', LIST + f"?minimal=true&query[actor_id]={R['user_blank']}", 200, page(by(lambda e: e['actor'] == 'user_blank')))
add('list-actor-braced-hex', 'owner', LIST + f"?minimal=true&query[actor_id]=%7B{R['user_deleted'].replace('-', '').upper()}%7D", 200,
    page(by(lambda e: e['actor'] == 'user_deleted')))
add('list-actor-malformed', 'owner', LIST + '?minimal=true&query[actor_id]=nope', 500, SERVER)
add('list-object', 'owner', LIST + f"?minimal=true&query[object_id]={R['course']}", 200, page(by(lambda e: e['pk'] == R['course'])))
add('list-search-actor-name', 'owner', LIST + '?minimal=true&query[generalSearch]=rOSSi&pagination[perpage]=50', 200,
    page(by(lambda e: e['actor'] == 'user_named' or 'rossi' in e['repr'].lower()), size=50))
add('list-search-actor-email', 'owner', LIST + '?minimal=true&query[generalSearch]=BLANK.audit', 200,
    page(by(lambda e: e['actor'] == 'user_blank')))
add('list-search-backslash-literal', 'owner', LIST + '?minimal=true&query[generalSearch]=%5C', 200, page([]))
# Malformed UTF-8 in a query parameter decodes with replacement characters (one per ill-formed subpart): no row matches.
add('list-search-malformed-utf8', 'owner', LIST + '?minimal=true&query[generalSearch]=%FF%E2%82x&pagination[perpage]=50', 200, page([]))
add('list-search-invalid-sequence', 'owner', LIST + '?minimal=true&query[generalSearch]=%C3%28&pagination[perpage]=50', 200, page([]))
add('list-search-repr', 'owner', LIST + '?minimal=true&query[generalSearch]=vecchio', 200,
    page(by(lambda e: 'vecchio' in e['repr'].lower())))
add('list-search-percent-literal', 'owner', LIST + '?minimal=true&query[generalSearch]=0%25_', 200,
    page(by(lambda e: '0%_' in e['repr'])))
add('list-search-underscore-literal', 'owner', LIST + '?minimal=true&query[generalSearch]=%25_x', 200,
    page(by(lambda e: '%_x' in e['repr'])))
add('list-date-from', 'owner', LIST + '?minimal=true&query[date_from]=2026-10-02', 200,
    page(by(lambda e: stamp_of(e) >= '2026-10-02T00:00:00.000000')))
add('list-date-to-end-of-day', 'owner', LIST + '?minimal=true&query[date_to]=2026-09-29&pagination[perpage]=50', 200,
    page(by(lambda e: stamp_of(e) <= '2026-09-29T23:59:59.000000'), size=50))
add('list-date-single-digits', 'owner', LIST + '?minimal=true&query[date_from]=2026-9-30&query[date_to]=2026-10-1', 200,
    page(by(lambda e: '2026-09-30T00:00:00.000000' <= stamp_of(e) <= '2026-10-01T23:59:59.000000')))
add('list-date-unicode-digits', 'owner', LIST + '?minimal=true&query[date_from]=%D9%A2%D9%A0%D9%A2%D9%A6-10-02', 200,
    page(by(lambda e: stamp_of(e) >= '2026-10-02T00:00:00.000000')))
add('list-date-unicode-month-rejected', 'owner', LIST + '?minimal=true&query[date_from]=2026-%D9%A1%D9%A0-02', 200, page(ALL))
add('list-date-invalid-ignored', 'owner', LIST + '?minimal=true&query[date_from]=2026-02-30&query[date_to]=2026-10-01x', 200, page(ALL))
add('list-sort-action-asc', 'owner', LIST + '?minimal=true&sort[field]=action&sort[sort]=asc&query[model]=associate,message', 200,
    page(sorted(by(lambda e: e['model'] in ('associate', 'message')), key=lambda n: ENTRIES[n]['action'])))
add('list-sort-action-default-desc', 'owner', LIST + '?minimal=true&sort[field]=action&query[model]=associate,message', 200,
    page(sorted(by(lambda e: e['model'] in ('associate', 'message')), key=lambda n: ENTRIES[n]['action'], reverse=True)))
add('list-sort-model-asc', 'owner', LIST + '?minimal=true&sort[field]=model&sort[sort]=asc&query[action]=0', 200,
    page(sorted(by(lambda e: e['action'] == 0), key=lambda n: ENTRIES[n]['model'])))
add('list-sort-model-desc', 'owner', LIST + '?minimal=true&sort[field]=model&sort[sort]=desc&query[action]=0', 200,
    page(sorted(by(lambda e: e['action'] == 0), key=lambda n: ENTRIES[n]['model'], reverse=True)))
add('list-sort-timestamp-asc', 'owner', LIST + '?minimal=true&sort[field]=timestamp&sort[sort]=asc', 200, page(ALL[::-1]))
add('list-sort-unknown-field-and-case', 'owner', LIST + '?minimal=true&sort[field]=object_repr&sort[sort]=ASC', 200, page(ALL))
add('list-combined', 'owner', LIST + f"?query[model]=course&query[action]=1&query[actor_id]={R['user_named']}&query[date_from]=2026-09-28"
    '&query[generalSearch]=obj', 200,
    page(by(lambda e: e['model'] == 'course' and e['action'] == 1 and e['actor'] == 'user_named' and 'obj' in e['repr'].lower()), render=full))

# --- detail: every resolver and formatter path ---
for name in ENTRIES:
    if name in ('foreign', 'unindexed') + CRASHES:
        continue
    add(f'detail-{name}', 'owner', detail(name), 200, {'data': full(name)})
for name in CRASHES:
    add(f'detail-{name}', 'owner', detail(name), 500, SERVER)
add('detail-foreign-entry', 'owner', detail('foreign'), 404, NOT_FOUND)
add('detail-foreign-owner', 'foreign_owner', detail('foreign'), 200, {'data': full('foreign')})
add('detail-unindexed', 'owner', detail('unindexed'), 404, NOT_FOUND)
add('detail-unknown-id', 'owner', '/audit-logs/996000999/detail', 404, NOT_FOUND)
add('detail-huge-id', 'owner', '/audit-logs/99999999999999999999999/detail', 404, NOT_FOUND)
add('detail-leading-zeros', 'owner', f"/audit-logs/000{ENTRIES['payment']['id']}/detail", 200, {'data': full('payment')})
add('detail-full-collaborator', 'full_collaborator', detail('payment'), 200, {'data': full('payment')})
add('detail-admin-impersonating-owner', 'admin', detail('payment'), 200, {'data': full('payment')},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})

# --- X-Group-Id: the group-aware managers filter the resolvers ---
grouped = full('course-grouped')
add('detail-group-own', 'owner', detail('course-grouped'), 200, {'data': {**grouped,
    'enriched_changes': {'course': [N, 'Gruppo corso'], 'associate': [N, missing8('associate')], 'subscription': [N, missing8('subscription')]},
    'display_changes': [show('Corso', 'course', 'N/D', 'Gruppo corso'), show('Associato', 'associate', 'N/D', missing8('associate')),
                        show('Iscrizione', 'subscription', 'N/D', missing8('subscription'))]}}, headers={'X-Group-Id': IDS['own_group']})
add('detail-group-foreign', 'owner', detail('course-grouped'), 200, {'data': {**grouped,
    'object_description': f"Corso #{short('course_grouped')}",
    'enriched_changes': {'course': [N, missing8('course_grouped')], 'associate': [N, missing8('associate')],
                         'subscription': [N, missing8('subscription')]},
    'display_changes': [show('Corso', 'course', 'N/D', missing8('course_grouped')), show('Associato', 'associate', 'N/D', missing8('associate')),
                        show('Iscrizione', 'subscription', 'N/D', missing8('subscription'))]}}, headers={'X-Group-Id': IDS['foreign_group']})
add('detail-group-malformed-ignored', 'owner', detail('course-grouped'), 200, {'data': grouped}, headers={'X-Group-Id': 'abc'})
add('detail-group-ungrouped-course-hidden', 'owner', detail('course-create'), 200, {'data': {**full('course-create'),
    'object_description': f"Corso #{short('course')}", 'enriched_changes': {'title': [N, 'Nuoto è'], 'deleted': ['N/D', 'No'],
    'course_id': [N, missing8('course')]}, 'display_changes': [show('Titolo', 'title', 'N/D', 'Nuoto è'),
    show('Eliminato', 'deleted', 'N/D', 'No'), show('Course Id', 'course_id', 'N/D', missing8('course'))]}},
    headers={'X-Group-Id': IDS['own_group']})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Audit log list and detail authored sequence', 'ids': IDS, 'rows': ROWS,
                              'entries': ENTRIES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
