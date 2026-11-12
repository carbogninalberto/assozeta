"""Author the collaborators/<str:uid>/delete HTTP sequence (collaborator-delete-go.md).

collaborators_delete is an @api_view(['DELETE']) function view with
IsAuthenticated only (Allow: DELETE, OPTIONS). Permissions (registry key
other.users.collaborators.delete) run before method dispatch; the view body:

1. request.user.is_sport_association(raise_exception=False): an athlete gets
   PermissionDenied('User is not a sport association') (403);
2. is_valid_uuid(uid): 400 ["badly formed hexadecimal UUID string"];
3. inside atomic_audit_mutation (one transaction, audit-index errors fatal):
   lock the owner row; the live user connected to the owner with that id
   (ActiveUserManager, .first()); otherwise the owner's invitation with that
   id, else 400 ["Collaborator not found"]; for an accepted invitation the
   live user with the invitation's exact email connected to the owner
   (lowest key first) is deleted first; then the user or invitation.
   Both deletes are Django's Collector: CASCADE closure, SET_NULL updates,
   PROTECT (ProtectedError: 500), DO_NOTHING (the deferred foreign key fails
   at commit: 500), fast deletes, pre_delete receivers (Payment:
   delete_installment_of_payment; CourseSubscription:
   delete_related_course_payment; CourseSubscriptionInstallment:
   delete_related_payment, which delete further rows through their own
   collectors) and auditlog's post_delete entry per instance, indexed through
   changes['sport_association'] when that association exists, else the
   actor's association.
   Response: 200 {'msg': 'collaborator deleted.'}.

Each graph case names the rows it deletes, the kept rows whose foreign key
is set to NULL and the audit entries (a multiset: rows deleted twice by the
receivers are logged twice) with their index source. Request bodies are
never read. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('collaborator_delete_go_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
counter = iter(range(1, 10000))
SERVER = '<stable-server-error-page>'
OK = {'msg': 'collaborator deleted.'}
NOT_FOUND = ['Collaborator not found']
UNKNOWN = 'c1000000-0000-4000-8000-000000989999'
rows, cases = [], []
labels = {}


def row(label, model, *, auto=False, **values):
    """One seeded row; '@label' values reference another seeded row's key,
    '$name' a base fixture id."""
    assert label not in labels, label
    key = None if auto else f'c1000000-0000-4000-8000-{980000 + next(counter):012d}'
    labels[label] = key
    rows.append({'label': label, 'model': model, 'id': key, 'values': values})
    return label


def user(label, email=None, *, connected='$owner', role=3, deleted=False, **extra):
    return row(label, 'application.User', username=f'cdgo-{label}', email=email or f'cdgo-{label}@example.test',
               password='!synthetic', first_name=label.title(), last_name='Delete', role=role,
               connected_user_id=connected, deleted=deleted, **extra)


def through(label, model, **values):
    return row(label, model, auto=True, **values)


SA = '$own_association'
WHEN = '2026-03-01T09:30:00.250000+00:00'

# Kept rows the graphs point at.
row('doc', 'docmanager.Document', filename='collaborator-delete.pdf')
row('feature', 'application.PreviewAndCustomFeatures', name='cdgo-feature', description='Preview')
row('auth_group', 'auth.Group', auto=True, name='cdgo-group')
row('course', 'application.Course', sport_association_id=SA, title='Delete course')
row('tag', 'application.Tags', sport_association_id=SA, tag_name='cdgo-tag')
row('product', 'application.ProductsAndServices', sport_association_id=SA, product_sku='CDGO', product_name='Kit')
row('module', 'application.Module', sport_association_id=SA, title='Delete module')
row('camps', 'application.CampsAndRetreats', sport_association_id=SA, title='Camp')
row('camps_period', 'application.CampsAndRetreatsPeriod', camps_and_retreats_id='@camps', start_date=WHEN, end_date=WHEN)
row('camps_service', 'application.CampsAndRetreatsPeriodsService', camps_and_retreats_period_id='@camps_period', title='Lunch')
row('broadcast', 'notifications.NotificationsBroadcast', broadcast_name='cdgo')
row('keep_subscription', 'application.Subscription', sport_association_id=SA, user_id='$athlete')
row('keep_course_subscription', 'application.CourseSubscription', course_id='@course', subscription_id='@keep_subscription')


def add(name, actor, target, status, response, *, method='DELETE', headers=None, body=None, raw_body=None, content_type=None,
        effect=None):
    path_id = labels.get(target, target)
    case = {'name': f'collaborator-delete-go-{name}', 'actor': actor, 'method': method,
            'path': f'/collaborators/{path_id}/delete', 'headers': headers or {}, 'status': status, 'response': response,
            'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def effect(deleted, audit, nulled=()):
    """deleted: row labels; audit: (label, index) with index 'sa' (the row's
    sport_association), 'actor' (the actor's association) or None."""
    return {'deleted': list(deleted), 'nulled': [list(item) for item in nulled], 'audit': [list(item) for item in audit]}


# --- admission ------------------------------------------------------------------
user('minimal')
add('anonymous', 'anonymous', 'minimal', 401, {'detail': 'Authentication credentials were not provided.'})
add('admin-without-session', 'admin', 'minimal', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('restricted-collaborator', 'restricted_collaborator', 'minimal', 403, {'detail': 'Missing permission: other.users.collaborators.delete'})
add('restricted-collaborator-wrong-method', 'restricted_collaborator', 'minimal', 403,
    {'detail': 'Missing permission: other.users.collaborators.delete'}, method='GET')
add('athlete', 'athlete', 'minimal', 403, {'detail': 'User is not a sport association'})
add('athlete-invalid-uuid', 'athlete', 'not-a-uuid', 403, {'detail': 'User is not a sport association'})
add('athlete-wrong-method', 'athlete', 'minimal', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('owner-scoped-to-athlete', 'owner', 'minimal', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
for verb in ('GET', 'POST', 'PUT', 'PATCH'):
    add(f'wrong-method-{verb.lower()}', 'owner', 'minimal', 405, {'detail': f'Method "{verb}" not allowed.'}, method=verb, body={})
add('options', 'owner', 'minimal', 200, {'name': 'Collaborators Delete', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('invalid-uuid', 'owner', 'not-a-uuid', 400, ['badly formed hexadecimal UUID string'])
add('unknown', 'owner', UNKNOWN, 400, NOT_FOUND)
user('foreign_collaborator', connected='$foreign_owner')
add('foreign-collaborator', 'owner', 'foreign_collaborator', 400, NOT_FOUND)
user('soft_deleted', deleted=True)
add('soft-deleted-user', 'owner', 'soft_deleted', 400, NOT_FOUND)
add('owner-self', 'owner', IDS['owner'], 400, NOT_FOUND)
add('owner-without-association', 'owner_without_association', 'minimal', 400, NOT_FOUND)
row('foreign_invite', 'application.CollaborationInvites', user_id='$foreign_owner', email='cdgo-foreign-invite@example.test')
add('foreign-invitation', 'owner', 'foreign_invite', 400, NOT_FOUND)

# --- invitations -----------------------------------------------------------------
row('pending_invite', 'application.CollaborationInvites', user_id='$owner', email='cdgo-pending@example.test')
# The body is never parsed.
add('pending-invitation-malformed-body', 'owner', 'pending_invite', 200, OK, raw_body='{', content_type='application/json',
    effect=effect(['pending_invite'], [('pending_invite', 'actor')]))
row('noaccount_invite', 'application.CollaborationInvites', user_id='$owner', email='cdgo-noaccount@example.test', accepted=True)
add('accepted-invitation-without-account', 'owner', 'noaccount_invite', 200, OK,
    effect=effect(['noaccount_invite'], [('noaccount_invite', 'actor')]))
user('account_user', 'cdgo-account@example.test')
row('account_onboarding', 'application.UsersOnboarding', user_id='@account_user')
row('account_invite', 'application.CollaborationInvites', user_id='$owner', email='cdgo-account@example.test', accepted=True)
add('accepted-invitation-with-account', 'owner', 'account_invite', 200, OK,
    effect=effect(['account_user', 'account_onboarding', 'account_invite'],
                  [('account_onboarding', 'actor'), ('account_user', 'actor'), ('account_invite', 'actor')]))
user('duplicate_first', 'cdgo-duplicate@example.test')
user('duplicate_second', 'cdgo-duplicate@example.test')
row('duplicate_invite', 'application.CollaborationInvites', user_id='$owner', email='cdgo-duplicate@example.test', accepted=True)
add('accepted-invitation-duplicate-email', 'owner', 'duplicate_invite', 200, OK,
    effect=effect(['duplicate_first', 'duplicate_invite'], [('duplicate_first', 'actor'), ('duplicate_invite', 'actor')]))
user('foreign_account', 'cdgo-foreign-account@example.test', connected='$foreign_owner')
row('foreign_account_invite', 'application.CollaborationInvites', user_id='$owner', email='cdgo-foreign-account@example.test',
    accepted=True)
add('accepted-invitation-foreign-account', 'owner', 'foreign_account_invite', 200, OK,
    effect=effect(['foreign_account_invite'], [('foreign_account_invite', 'actor')]))
user('case_account', 'cdgo-case@example.test')
row('case_invite', 'application.CollaborationInvites', user_id='$owner', email='CDGO-case@example.test', accepted=True)
add('accepted-invitation-email-case', 'owner', 'case_invite', 200, OK, effect=effect(['case_invite'], [('case_invite', 'actor')]))
user('soft_account', 'cdgo-soft@example.test', deleted=True)
row('soft_invite', 'application.CollaborationInvites', user_id='$owner', email='cdgo-soft@example.test', accepted=True)
add('accepted-invitation-soft-deleted-account', 'owner', 'soft_invite', 200, OK, effect=effect(['soft_invite'], [('soft_invite', 'actor')]))

# --- direct users ---------------------------------------------------------------
add('minimal-user', 'owner', 'minimal', 200, OK, effect=effect(['minimal'], [('minimal', 'actor')]))
user('upper')
add('uppercase-uuid', 'owner', labels['upper'].upper(), 200, OK, effect=effect(['upper'], [('upper', 'actor')]))
user('hex')
add('hex-uuid', 'owner', labels['hex'].replace('-', ''), 200, OK, effect=effect(['hex'], [('hex', 'actor')]))

# Recursive connected users: one model, deleted and logged in descending key order.
user('chain')
user('chain_child', connected='@chain')
user('chain_grandchild', connected='@chain_child')
add('connected-chain', 'owner', 'chain', 200, OK,
    effect=effect(['chain', 'chain_child', 'chain_grandchild'], [('chain', 'actor'), ('chain_child', 'actor'), ('chain_grandchild', 'actor')]))

# A broad personal graph: through tables, cascades two levels deep, a row
# reached twice, fast deletes, SET_NULL on kept rows, unaudited models.
user('broad')
through('broad_feature', 'application.User_preview_and_custom_features', user_id='@broad', previewandcustomfeatures_id='@feature')
through('broad_group', 'application.User_groups', user_id='@broad', group_id='@auth_group')
through('broad_permission', 'application.User_user_permissions', user_id='@broad', permission_id='$permission:view_logentry')
row('broad_onboarding', 'application.UsersOnboarding', user_id='@broad')
row('broad_invite', 'application.CollaborationInvites', user_id='@broad', email='cdgo-broad-invite@example.test')
row('broad_instructor', 'application.Instructor', user_id='@broad', first_name='Ina', last_name='Struttrice')
through('broad_instructor_doc', 'application.Instructor_documents', instructor_id='@broad_instructor', document_id='@doc')
row('broad_payment', 'application.Payment', sport_association_id=SA, user_id='@broad', paid=True, amount='12.50')
through('broad_payment_doc', 'application.Payment_attachments', payment_id='@broad_payment', document_id='@doc')
row('broad_hours', 'application.InstructorHours', instructor_id='@broad_instructor', hours='2.50', payment_id='@broad_payment')
row('broad_instructor_reminder', 'application.Reminders', user_id='@broad', instructor_id='@broad_instructor', event_title='Turno',
    send_at=WHEN, sport_association_id=SA)
row('broad_medical', 'application.MedicalCertificate', user_id='@broad', notes='Visita')
row('broad_subscription', 'application.Subscription', sport_association_id=SA, user_id='@broad', custom_data={'b': [1, 2.5], 'a': None})
row('broad_appointment', 'application.MedicalAppointments', subscription_id='@broad_subscription', region='Veneto')
through('broad_subscription_tag', 'application.Subscription_tags', subscription_id='@broad_subscription', tags_id='@tag')
row('broad_membership', 'application.SubscriptionMembership', subscription_id='@broad_subscription', sport_association_id=SA)
through('broad_membership_doc', 'application.SubscriptionMembership_attached_membership_documents',
        subscriptionmembership_id='@broad_membership', document_id='@doc')
row('broad_sold', 'application.SoldProducts', subscription_id='@broad_subscription', product_id='@product')
row('broad_token', 'application.SubscriptionToken', subscription_id='@broad_subscription')
row('broad_file', 'application.SubscriptionFile', subscription_id='@broad_subscription', document_id='@doc')
row('broad_transfer', 'application.SubscriptionTransfer', subscription_id='@broad_subscription', requester_id='$owner')
row('broad_camp', 'application.CampsAndRetreatsSubscription', camps_and_retreats_id='@camps', subscription_id='@broad_subscription')
row('broad_camp_period', 'application.CampsAndRetreatsSubscriptionPeriod', camps_and_retreats_period_id='@camps_period',
    camps_and_retreats_subscription_id='@broad_camp')
through('broad_camp_service', 'application.CampsAndRetreatsSubscriptionPeriod_camps_and_retreats_period_services',
        campsandretreatssubscriptionperiod_id='@broad_camp_period', campsandretreatsperiodsservice_id='@camps_service')
row('broad_carnet_subscription', 'application.CarnetSubscription', subscription_id='@broad_subscription')
row('broad_signature', 'application.Signature', user_id='@broad', signature='data:image/png;base64,AAAA')
row('broad_requested', 'application.SubscriptionTransfer', subscription_id='@keep_subscription', requester_id='@broad')
row('broad_received', 'application.SubscriptionTransfer', subscription_id='@keep_subscription', requester_id='$owner', recipient_id='@broad')
row('broad_reminder', 'application.Reminders', user_id='@broad', event_title='Promemoria', send_at=WHEN, sport_association_id=SA)
row('broad_carnet', 'application.Carnet', user_id_id='@broad', sport_association_id=SA, title='Carnet', fee='30.00')
row('broad_carnet_use', 'application.CarnetSubscription', carnet_id_id='@broad_carnet')
row('broad_own_carnet_subscription', 'application.CarnetSubscription', user_id_id='@broad')
row('broad_response', 'application.ModuleResponses', module_id='@module', user_id='@broad', response={'q': 'r'})
through('broad_response_doc', 'application.ModuleResponses_attachments', moduleresponses_id='@broad_response', document_id='@doc')
row('broad_report', 'application.SavedReport', sport_association_id=SA, created_by_id='@broad', name='Report', tool_name='payments')
row('broad_board', 'communications.StaffBoardMessage', sport_association_id=SA, author_id='@broad', content='Ciao')
row('broad_notification', 'notifications.UserNotificationsBroadcast', user_id='@broad', broadcast_id='@broadcast')
row('broad_detachment', 'instance.DataRestoreUserDetachment', auto=True, user_id='@broad', owner_id='$owner', association_id=SA)
row('broad_social', 'social_django.UserSocialAuth', auto=True, user_id='@broad', provider='google-oauth2', uid='cdgo-broad')
row('broad_logentry', 'auditlog.LogEntry', auto=True, actor_id='@broad', content_type_id='$contenttype:application.user',
    object_pk='seed', object_repr='seed', action=0, changes_text='')
row('broad_nurturing', 'application.NurturingEmails', user_id='@broad')
user('broad_child', connected='@broad')
row('broad_child_onboarding', 'application.UsersOnboarding', user_id='@broad_child')
# Kept rows pointing into the graph through SET_NULL relations.
row('keep_payment', 'application.Payment', sport_association_id=SA, user_id='$athlete', signature_id='@broad_signature', paid=True)
row('keep_pointer_subscription', 'application.Subscription', sport_association_id=SA, user_id='$athlete',
    medical_id='@broad_medical', payment_id='@broad_payment', instructor_id='@broad_instructor')
BROAD_DELETED = ['broad', 'broad_feature', 'broad_group', 'broad_permission', 'broad_onboarding', 'broad_invite', 'broad_instructor',
                 'broad_instructor_doc', 'broad_payment', 'broad_payment_doc', 'broad_hours', 'broad_instructor_reminder', 'broad_medical',
                 'broad_subscription', 'broad_appointment', 'broad_subscription_tag', 'broad_membership', 'broad_membership_doc',
                 'broad_sold', 'broad_token', 'broad_file', 'broad_transfer', 'broad_camp', 'broad_camp_period', 'broad_camp_service',
                 'broad_carnet_subscription', 'broad_signature', 'broad_requested', 'broad_reminder', 'broad_carnet', 'broad_carnet_use',
                 'broad_own_carnet_subscription', 'broad_response', 'broad_response_doc', 'broad_report', 'broad_notification',
                 'broad_detachment', 'broad_social', 'broad_child', 'broad_child_onboarding']
BROAD_AUDIT = [('broad', 'actor'), ('broad_onboarding', 'actor'), ('broad_invite', 'actor'), ('broad_instructor', 'actor'),
               ('broad_payment', 'sa'), ('broad_hours', 'actor'), ('broad_instructor_reminder', 'sa'), ('broad_medical', 'actor'),
               ('broad_subscription', 'sa'), ('broad_appointment', 'actor'), ('broad_membership', 'sa'), ('broad_sold', 'actor'),
               ('broad_token', 'actor'), ('broad_file', 'actor'), ('broad_transfer', 'actor'), ('broad_camp', 'actor'),
               ('broad_camp_period', 'actor'), ('broad_carnet_subscription', 'actor'), ('broad_signature', 'actor'),
               ('broad_requested', 'actor'), ('broad_reminder', 'sa'), ('broad_carnet', 'sa'), ('broad_carnet_use', 'actor'),
               ('broad_own_carnet_subscription', 'actor'), ('broad_response', 'actor'), ('broad_notification', 'actor'),
               ('broad_child', 'actor'), ('broad_child_onboarding', 'actor')]
BROAD_NULLED = [('keep_payment', 'signature_id'), ('keep_pointer_subscription', 'medical_id'), ('keep_pointer_subscription', 'payment_id'),
                ('keep_pointer_subscription', 'instructor_id'), ('broad_received', 'recipient_id'), ('broad_board', 'author_id'),
                ('broad_logentry', 'actor_id'), ('broad_nurturing', 'user_id')]
add('broad-graph', 'owner', 'broad', 200, OK, effect=effect(BROAD_DELETED, BROAD_AUDIT, BROAD_NULLED))

# Financial receivers. Ascending keys follow the authoring order.
user('finance')
row('finance_subscription', 'application.Subscription', sport_association_id=SA, user_id='@finance')
for name, paid, deleted in (('finance_pa1', False, False), ('finance_pa2', True, False), ('finance_pa3', False, False),
                            ('finance_pc', True, False), ('finance_pm1', False, False), ('finance_pm2', True, False),
                            ('finance_pm3', False, True)):
    row(name, 'application.Payment', sport_association_id=SA, user_id='$athlete', paid=paid, deleted=deleted, amount='5.00')
row('finance_cs_a', 'application.CourseSubscription', course_id='@course', subscription_id='@finance_subscription', type=1)
row('finance_ia1', 'application.CourseSubscriptionInstallment', course_subscription_id='@finance_cs_a', payment_id='@finance_pa1',
    payment_date=WHEN, amount='5.00')
row('finance_ia2', 'application.CourseSubscriptionInstallment', course_subscription_id='@finance_cs_a', payment_id='@finance_pa2',
    payment_date=WHEN, paid=True)
row('finance_ia3', 'application.CourseSubscriptionInstallment', course_subscription_id='@finance_cs_a', payment_id='@finance_pa3',
    payment_date=WHEN, deleted=True)
row('finance_ia4', 'application.CourseSubscriptionInstallment', course_subscription_id='@finance_cs_a', payment_date=WHEN)
row('finance_cs_b', 'application.CourseSubscription', course_id='@course', subscription_id='@finance_subscription', type=3,
    membership_fee='15.00')
for name in ('finance_pm1', 'finance_pm2', 'finance_pm3'):
    through(name + '_link', 'application.CourseSubscription_membership_payments', coursesubscription_id='@finance_cs_b',
            payment_id='@' + name)
row('finance_cs_c', 'application.CourseSubscription', course_id='@course', subscription_id='@finance_subscription', type=1,
    payment_id='@finance_pc', multiple_quote={'rate': [1, 2]})
row('finance_pu', 'application.Payment', sport_association_id=SA, user_id='@finance', paid=False, amount='7.00')
row('finance_iu', 'application.CourseSubscriptionInstallment', course_subscription_id='@keep_course_subscription',
    payment_id='@finance_pu', payment_date=WHEN)
# Payment pre_delete deletes Pu's installment, whose receiver deletes Pu again
# (receiver disconnected); installment receivers delete their unpaid payments
# (Pa1 through a nested collector pair, Pa3 directly: its installment is soft
# deleted); course-subscription receivers re-delete live installments and the
# unpaid live membership payment; a course subscription with a payment
# raises FieldError inside its receiver, which logs it.
add('financial-receivers', 'owner', 'finance', 200, OK, effect=effect(
    ['finance', 'finance_subscription', 'finance_cs_a', 'finance_cs_b', 'finance_cs_c', 'finance_ia1', 'finance_ia2', 'finance_ia3',
     'finance_ia4', 'finance_pa1', 'finance_pa3', 'finance_pm1', 'finance_pu', 'finance_iu', 'finance_pm1_link', 'finance_pm2_link',
     'finance_pm3_link'],
    [('finance_pu', 'sa'), ('finance_iu', 'actor'), ('finance_pa1', 'sa'), ('finance_ia1', 'actor'), ('finance_pa1', 'sa'),
     ('finance_pa3', 'sa'), ('finance_ia4', 'actor'), ('finance_ia2', 'actor'), ('finance_pm1', 'sa'), ('finance_pu', 'sa'),
     ('finance_subscription', 'sa'), ('finance_ia4', 'actor'), ('finance_ia3', 'actor'), ('finance_ia2', 'actor'), ('finance_ia1', 'actor'),
     ('finance', 'actor'), ('finance_cs_c', 'actor'), ('finance_cs_b', 'actor'), ('finance_cs_a', 'actor')]))

# The group header filters the receivers' managers, not the cascade.
for prefix, group in (('grouped', '$own_group'), ('ungrouped', None)):
    user(prefix)
    row(prefix + '_payment', 'application.Payment', sport_association_id=SA, user_id='@' + prefix, paid=False, group_id='$own_group')
    row(prefix + '_in_group', 'application.CourseSubscriptionInstallment', course_subscription_id='@keep_course_subscription',
        payment_id=f'@{prefix}_payment', payment_date=WHEN, group_id='$own_group')
    row(prefix + '_no_group', 'application.CourseSubscriptionInstallment', course_subscription_id='@keep_course_subscription',
        payment_id=f'@{prefix}_payment', payment_date=WHEN)
add('group-header-filters-receivers', 'owner', 'grouped', 200, OK, headers={'X-Group-Id': IDS['own_group']}, effect=effect(
    ['grouped', 'grouped_payment', 'grouped_in_group'],
    [('grouped_payment', 'sa'), ('grouped_in_group', 'actor'), ('grouped_payment', 'sa'), ('grouped', 'actor')],
    [('grouped_no_group', 'payment_id')]))
# Without the header both installments go; the second one's payment is gone
# when its receiver runs (logged).
add('no-group-header', 'owner', 'ungrouped', 200, OK, effect=effect(
    ['ungrouped', 'ungrouped_payment', 'ungrouped_in_group', 'ungrouped_no_group'],
    [('ungrouped_payment', 'sa'), ('ungrouped_no_group', 'actor'), ('ungrouped_in_group', 'actor'), ('ungrouped_payment', 'sa'),
     ('ungrouped', 'actor')]))

# PROTECT raises before any write; DO_NOTHING fails at the deferred commit.
user('restore_owner')
row('restore', 'instance.DataRestore', owner_id='@restore_owner', association_id=SA, sha256='0' * 64)
add('protected-data-restore', 'owner', 'restore_owner', 500, SERVER)
user('billing_subscriber')
row('billing_subscription', 'application.BillingSubscription', user_id='@billing_subscriber')
add('protected-billing-subscription', 'owner', 'billing_subscriber', 500, SERVER)
user('billing_payer')
row('billing_payment', 'application.BillingPayment', user_id='@billing_payer', amount='9.00')
add('protected-billing-payment', 'owner', 'billing_payer', 500, SERVER)
user('associate_user')
row('associate', 'application.Associate', user_id='@associate_user', sport_association_id=SA, first_name='Socio')
add('associate-reference-fails-at-commit', 'owner', 'associate_user', 500, SERVER)

# A connected association user owning an association: with an audited child
# the child's index row points at the association deleted after it (commit
# fails); alone, the association is logged through the actor.
user('owning_with_group', role=1)
row('owned_association_with_group', 'application.SportAssociation', user_id='@owning_with_group', denomination='Owned with group',
    tax_code='79800000001')
row('owned_group', 'application.Group', sport_association_id='@owned_association_with_group', name='Owned group')
add('owned-association-with-child-fails-at-commit', 'owner', 'owning_with_group', 500, SERVER)
user('owning_alone', role=1)
row('owned_association_alone', 'application.SportAssociation', user_id='@owning_alone', denomination='Owned alone', tax_code='79800000002')
add('owned-association', 'owner', 'owning_alone', 200, OK, effect=effect(
    ['owning_alone', 'owned_association_alone'], [('owned_association_alone', 'actor'), ('owning_alone', 'actor')]))

# An owned association whose collected children all have a nullable
# association key: no dependency orders them first, so the association is
# deleted before them and their entries index through the actor (one payment
# of the owner's association, reached through a deleted account, indexes
# through its own association). Kept rows lose their SET_NULL keys.
SB = '@owned_tree'
user('owning_tree', role=1)
row('owned_tree', 'application.SportAssociation', user_id='@owning_tree', denomination='Owned tree', tax_code='79800000003')
row('tree_course_tag', 'application.CourseTags', sport_association_id=SA, tag_name='cdgo-tree-tag')
row('tree_course', 'application.Course', sport_association_id=SB, title='Tree course')
through('tree_course_tag_link', 'application.Course_tags', course_id='@tree_course', coursetags_id='@tree_course_tag')
row('tree_location', 'application.CourseLocation', sport_association_id=SB)
through('tree_course_location', 'application.Course_locations', course_id='@tree_course', courselocation_id='@tree_location')
through('tree_location_doc', 'application.CourseLocation_documents', courselocation_id='@tree_location', document_id='@doc')
row('tree_registry', 'application.AttendanceRegistry', course_id='@tree_course')
row('tree_day', 'application.AttendanceDay', attendance_registry_id='@tree_registry', date=WHEN)
row('tree_course_subscription', 'application.CourseSubscription', course_id='@tree_course', type=1)
row('tree_installment', 'application.CourseSubscriptionInstallment', course_subscription_id='@tree_course_subscription',
    payment_date=WHEN, amount='3.30')
row('tree_payment', 'application.Payment', sport_association_id=SB, user_id='$athlete', paid=False)
row('tree_account', 'application.CustomAccounts', sport_association_id=SB, name='Cassa', initial_balance='100.00', account_code='C1')
row('tree_account_two', 'application.CustomAccounts', sport_association_id=SB, name='Banca', initial_balance='0.00', account_code='B1')
row('tree_transfer', 'application.CustomAccountsTransfer', sport_association_id=SB, custom_account_from_id='@tree_account',
    custom_account_to_id='@tree_account_two', amount='10.00')
row('tree_account_payment', 'application.Payment', sport_association_id=SA, user_id='$athlete', paid=True, custom_accounts_id='@tree_account')
row('tree_balance', 'application.BalanceSheet', sport_association_id=SB)
row('tree_supplier', 'application.SupplierAndCustomers', sport_association_id=SB, name='Fornitore')
row('tree_kept_supplier_payment', 'application.Payment', sport_association_id=SA, user_id='$athlete', supplier_id='@tree_supplier')
row('tree_category', 'application.PaymentCategory', sport_association_id=SB, name='Categoria')
user('tree_category_user', default_payment_category_id='@tree_category')
row('tree_draft', 'application.AssociateImportDraft', sport_association_id=SB)
row('tree_draft_status', 'application.AssociateImportDraftStatus', sport_association_id=SB)
row('tree_configuration', 'communications.CommunicationConfiguration', sport_association_id=SB)
row('tree_kept_associate', 'application.Associate', sport_association_id=SB, first_name='Rimane')
row('tree_email_log', 'application.EmailLog', sport_association_id=SB, subject='Oggetto', result='ok')
row('tree_calendar', 'application.GlobalCalendarEvents', sport_association_id=SB)
row('tree_reminders', 'application.Reminders', sport_association_id=SB, user_id='$athlete', event_title='Evento', send_at=WHEN)
row('tree_carnet', 'application.Carnet', sport_association_id=SB, fee='20.00')
row('tree_carnet_subscription', 'application.CarnetSubscription', carnet_id_id='@tree_carnet')
row('tree_module', 'application.Module', sport_association_id=SB)
row('tree_module_response', 'application.ModuleResponses', module_id='@tree_module')
TREE_AUDITED = ['owning_tree', 'owned_tree', 'tree_course', 'tree_location', 'tree_registry', 'tree_day', 'tree_course_subscription',
                'tree_payment', 'tree_account', 'tree_account_two', 'tree_transfer', 'tree_balance', 'tree_supplier', 'tree_category',
                'tree_draft', 'tree_draft_status', 'tree_configuration', 'tree_calendar', 'tree_reminders', 'tree_carnet',
                'tree_carnet_subscription', 'tree_module', 'tree_module_response']
add('owned-association-tree', 'owner', 'owning_tree', 200, OK, effect=effect(
    TREE_AUDITED + ['tree_installment', 'tree_account_payment', 'tree_course_tag_link', 'tree_course_location', 'tree_location_doc',
                    'tree_email_log'],
    [(label, 'actor') for label in TREE_AUDITED] + [('tree_installment', 'actor'), ('tree_installment', 'actor'),
                                                    ('tree_account_payment', 'sa')],
    [('tree_kept_supplier_payment', 'supplier_id'), ('tree_category_user', 'default_payment_category_id'),
     ('tree_kept_associate', 'sport_association_id')]))

# NOT NULL children without audit entries (folders, products) are deleted
# before the association; the audited sold product indexes through the actor;
# fast deletes remove the association's testimonial, report, board message
# and an existing audit index row.
SC = '@owned_plain'
user('owning_plain', role=1)
row('owned_plain', 'application.SportAssociation', user_id='@owning_plain', denomination='Owned plain', tax_code='79800000004')
row('plain_folder', 'application.Folder', auto=True, name='Radice', sport_association_id=SC, lft=1, rght=4, tree_id=980001, level=0)
row('plain_child_folder', 'application.Folder', auto=True, name='Figlia', parent_id='@plain_folder', sport_association_id=SC, lft=2, rght=3,
    tree_id=980001, level=1)
row('plain_product', 'application.ProductsAndServices', sport_association_id=SC, product_sku='PLAIN', product_name='Maglia')
row('plain_sold', 'application.SoldProducts', product_id='@plain_product', quantity=2)
row('plain_testimonial', 'application.Testimonial', sport_association_id=SC, text='Bene', score=4.5)
row('plain_report', 'application.SavedReport', sport_association_id=SC, created_by_id='$athlete', name='R', tool_name='payments')
row('plain_board', 'communications.StaffBoardMessage', sport_association_id=SC, content='Nota')
row('plain_logentry', 'auditlog.LogEntry', auto=True, content_type_id='$contenttype:application.sportassociation',
    object_pk='seed-plain', object_repr='seed', action=1, changes_text='')
row('plain_index', 'application.AuditLogIndex', log_entry_id='@plain_logentry', sport_association_id=SC, resolution_path='direct')
add('owned-association-unaudited-children', 'owner', 'owning_plain', 200, OK, effect=effect(
    ['owning_plain', 'owned_plain', 'plain_folder', 'plain_child_folder', 'plain_product', 'plain_sold', 'plain_testimonial',
     'plain_report', 'plain_board', 'plain_index'],
    [('owning_plain', 'actor'), ('owned_plain', 'actor'), ('plain_sold', 'actor')]))

# Actors: a full collaborator acts for the owner (its own association: none);
# an administrator impersonating the owner (index: the administrator's).
user('by_collaborator')
add('full-collaborator-actor', 'full_collaborator', 'by_collaborator', 200, OK, effect=effect(['by_collaborator'], [('by_collaborator', None)]))
user('by_admin')
add('impersonating-admin', 'admin', 'by_admin', 200, OK, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']},
    effect=effect(['by_admin'], [('by_admin', 'actor')]))
# Last: the acting collaborator deletes itself; its delete entries reference
# the deleted actor (commit fails).
add('full-collaborator-self', 'full_collaborator', IDS['full_collaborator'], 500, SERVER)

OUTPUT.write_text(json.dumps({'matrix_version': 1, 'ids': {**IDS, 'unknown': UNKNOWN}, 'rows': rows, 'cases': cases},
                             indent=1, ensure_ascii=False) + '\n')
print(len(cases), 'cases', len(rows), 'rows')
