"""Parent-only oauth2/signup, partial-signup, reset and delete-account HTTP runner; imports are inert.

Seeds users (password-reset and username-conflict rows), collaboration
invites, partial users, the own association's membership card configuration
(so login responses create none) and owner_without_association's delete_on,
with audit disabled; the reset view's Redis DB 2 gets the authored tokens for
the fixture's lifetime (all keys the run adds are removed afterwards). Django
hashes passwords with its default hashers (the contract settings prefer MD5),
the production PBKDF2-SHA256 hasher.

Every case checks the complete model state and Redis DB 2:
- partial: one new UserPartial (email, creation_date) with its create entry;
- signup: the deleted UserPartial / CollaborationInvites rows (delete entries,
  auditlog's diff of the instance read before the case), then at most one new
  User: every field (defaults for the ones create_user does not set), the
  password with check_password and the default hasher, its create entry; a
  '$signup' response is verified with SimpleJWT (claims of the stored user)
  and compared with Django's JWTTokenService.build_login_response for the
  stored user and the view's collaborator override;
- delete: delete_on of one user becomes the UTC date 30 days after the request
  and an update entry is written when it changed;
- reset-request: one new DB 2 key per user (32 hex digits, value user_id,
  TTL 3600 s); reset: the user's password and the consumed key.
The Celery messages are exactly the authored ones; e-mail bodies are rendered
here with render_to_string under the request's language.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import ipaddress
import re
import time

from TESTS.contracts import test_oauth_accounts_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/oauth_accounts_http.json', 'TESTS/contracts/test_oauth_accounts_http.py',
           'TESTS/adapters/django/oauth_accounts_http.py', 'BE/application/views/auth_views.py',
           'BE/application/services/jwt_token_service.py', 'BE/application/services/social_auth_service.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/disposable_email.py',
           'BE/application/impersonation.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/application/permissions_registry.py', 'BE/application/tasks.py',
           'BE/instance/integration_configuration.py', 'BE/instance/registration_features.py',
           'BE/application/templatetags/instance_branding.py', 'BE/templates/email/_base.html',
           'BE/templates/email/account/email_welcome_message.html', 'BE/templates/email/account/email_reset_message.html',
           'BE/templates/email/account/email_confirm_reset_message.html', 'BE/core/tasks.py', 'BE/core/celery.py',
           'BE/core/settings.py', 'BE/core/middleware.py', 'BE/instance/restore/task_base.py',
           'BE_GO/internal/service/oauth_disposable_domains.txt') + harness.SOURCES
DISPOSABLE = 'BE_GO/internal/service/oauth_disposable_domains.txt'
USER, PARTIAL, INVITES = 'application.User', 'application.UserPartial', 'application.CollaborationInvites'
MANAGED = (USER, PARTIAL, INVITES, harness.AUDIT, harness.INDEX)
RESET_DB = 2
RUNTIME = {}


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def seed_rows(source, created):
    from datetime import date
    from pathlib import Path
    from application.disposable_email import custom_email_domain_loader
    # The Go port embeds the checker's list: it must be Django's, entry for entry.
    listed = (Path(__file__).resolve().parents[3] / DISPOSABLE).read_text().split('\n')
    contract.require(listed[-1] == '' and listed[:-1] == custom_email_domain_loader(), 'fixture.disposable-list')
    from application.models import User
    from application.models.user_models import CollaborationInvites, SportAssociationMembershipCardConfiguration, UserPartial

    def put(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for values in source['users'].values():
        put(User, User(password='!synthetic', **values))
    for values in source['invites'].values():
        row = dict(values)
        expiration = row.pop('expiration_date')
        put(CollaborationInvites, CollaborationInvites(user_id=row.pop('user'), creation_date=moment('2026-01-01T08:00:00Z'),
                                                       expiration_date=moment(expiration) if expiration else None, **row))
    for values in source['partials'].values():
        put(UserPartial, UserPartial(creation_date=moment('2026-01-02T08:00:00Z'), **values))
    card = dict(source['card'])
    put(SportAssociationMembershipCardConfiguration,
        SportAssociationMembershipCardConfiguration(sport_association_id=card.pop('sport_association'), **card))
    User._base_manager.filter(pk=source['ids']['owner_without_association']).update(delete_on=date.fromisoformat(source['scheduled']))


def created_users():
    from application.models import User
    return User


def created_partials():
    from application.models.user_models import UserPartial
    return UserPartial


def reset_client(raw):
    import redis
    options = raw.connection_pool.connection_kwargs
    return redis.Redis(host=options['host'], port=options['port'], db=RESET_DB, password=options['password'],
                       socket_timeout=5, socket_connect_timeout=5)


def with_reset_tokens(fixture_context):
    """The reset view's Redis DB 2: authored tokens for the fixture's lifetime; every key added is removed."""
    @contextmanager
    def wrapped(apps, private_work, raw, *, cleanup_receipt=None):
        client = reset_client(raw)
        original = set(client.keys('*'))
        try:
            tokens = contract.load_source()['reset_tokens']
            contract.require(not any(key.encode() in original for key in tokens), 'fixture.reset-token-collision')
            for key, value in tokens.items():
                client.set(key, value, ex=3600)
            RUNTIME['reset'] = client
            with fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
                yield fixture
        finally:
            RUNTIME.pop('reset', None)
            for key in set(client.keys('*')) - original:
                client.delete(key)
            client.close()

    return wrapped


fixture_context = with_reset_tokens(harness.with_celery_broker(
    harness.fixture_context_for(contract, seed_rows, purge=(created_users, created_partials))))


@contextmanager
def service_context(report, log):
    """Django's default password hashers for the served Django (the Go server always writes PBKDF2-SHA256)."""
    from django.conf import global_settings
    from django.test.utils import override_settings
    with override_settings(PASSWORD_HASHERS=global_settings.PASSWORD_HASHERS):
        yield


def reset_state():
    client = RUNTIME['reset']
    return {key.decode(): client.get(key).decode() for key in client.keys('*')}


def prepare(source):
    from application.models.user_models import CollaborationInvites, UserPartial
    return {'started': time.time(), 'reset': reset_state(),
            'partials': {row.pk: row for row in UserPartial.objects.all()},
            'invites': {row.pk: row for row in CollaborationInvites.objects.all()}}


# --- e-mail placeholders ------------------------------------------------------------------------

def resolve(value, values):
    if isinstance(value, str):
        if value in values:
            return values[value]
        for key in sorted(values, key=len, reverse=True):
            if key in value and isinstance(values[key], str):
                value = value.replace(key, values[key])
        return value
    if isinstance(value, list):
        return [resolve(item, values) for item in value]
    if isinstance(value, dict):
        if '$render' in value:
            from django.template.loader import render_to_string
            from django.utils import translation
            with translation.override(value['language']):
                return render_to_string(value['$render'], resolve(value['context'], values))
        return {key: resolve(item, values) for key, item in value.items()}
    return value


def bindings(extra=None):
    from django.conf import settings
    return {'$app_url': settings.APP_URL, '$whitelabel': settings.WHITELABEL_NAME, '$is_whitelabel': settings.IS_WHITELABEL,
            '$from_email': settings.DEFAULT_FROM_EMAIL, **(extra or {})}


# --- audit --------------------------------------------------------------------------------------

def check_event(case, event, *, model, instance, action, changes, actor, index, indexes, context, ids):
    from django.contrib.contenttypes.models import ContentType
    from application.models import User
    contract.require(event.content_type_id == ContentType.objects.get_for_model(model).id and event.action == action
                     and event.object_pk == str(instance.pk)
                     and event.object_id == (instance.pk if isinstance(instance.pk, int) else None) and event.object_repr == str(instance),
                     'audit.identity:' + model.__name__)
    contract.require(event.changes == changes, 'audit.changes:' + model.__name__)
    actor_id = ids[actor] if actor else None
    email = User._base_manager.get(pk=actor_id).email if actor_id else None
    contract.require((str(event.actor_id) if event.actor_id else None) == actor_id and event.actor_email == email, 'audit.actor')
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': context['fixture']['sessions'][label], 'actor_id': actor_id,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None if label.startswith('admin_') else ids['own_association']}
    contract.require(event.cid == case['name'] and event.additional_data == additional and event.changes_text == ''
                     and event.serialized_data is None, 'audit.context')
    ipaddress.ip_address(event.remote_addr)
    contract.require(context['remote'].setdefault('addr', event.remote_addr) == event.remote_addr, 'audit.remote-consistent')
    row = indexes.get(str(event.pk))
    if index is None:
        contract.require(row is None, 'index.absent')
    else:
        contract.require(row is not None and str(row['sport_association_id']) == index[0] and row['resolution_path'] == index[1],
                         'index.fields')


def deletion_index(actor, ids):
    """Index of a deleted row's entry: no resolver row, no sport_association change, so the actor's association."""
    from application.models import SportAssociation
    if not actor:
        return None
    association = SportAssociation.objects.filter(user_id=ids[actor]).first()
    return [str(association.pk), 'actor.sport_association'] if association else None


def stable_rows(rows, key):
    return {str(row[key]['value'] if isinstance(row[key], dict) else row[key]): row for row in rows}


# --- per kind -----------------------------------------------------------------------------------

def check_users(before, after, *, created=(), changed=None):
    """Existing users are unchanged except changed = (id, {field: value or callable}); returns the new rows."""
    old, new = stable_rows(before[USER], 'user_id'), stable_rows(after[USER], 'user_id')
    contract.require(set(old) <= set(new), 'state.users-kept')
    added = sorted(set(new) - set(old))
    contract.require(len(added) == len(created), 'state.user-count')
    for key, row in old.items():
        if changed and key == changed[0]:
            fields = changed[1]
            contract.require({k: v for k, v in row.items() if k not in fields} == {k: v for k, v in new[key].items() if k not in fields},
                             'state.user-other-fields')
            continue
        contract.require(new[key] == row, 'state.user-unchanged')
    return added


def check_partial(case, before, after, context, ids, events, indexes):
    from application.models.user_models import UserPartial
    effect = case['effect']
    old, new = stable_rows(before[PARTIAL], 'id'), stable_rows(after[PARTIAL], 'id')
    contract.require(set(old) <= set(new) and all(new[k] == v for k, v in old.items()), 'state.partials-kept')
    added = sorted(set(new) - set(old))
    contract.require(len(added) == 1, 'state.partial-count')
    row = UserPartial.objects.get(pk=int(added[0]))
    since, until = context['prepared']['started'], time.time()
    contract.require(row.email == effect['email'] and since - 1 <= row.creation_date.timestamp() <= until + 1, 'state.partial')
    check_users(before, after)
    contract.require(before[INVITES] == after[INVITES], 'state.invites-unchanged')
    from auditlog.diff import model_instance_diff
    changes = {key: list(value) for key, value in model_instance_diff(None, row).items()}
    contract.require(len(events) == 1, 'audit.count')
    check_event(case, events.pop(0), model=UserPartial, instance=row, action=0, changes=changes, actor=effect['actor'],
                index=effect['index'], indexes=indexes, context=context, ids=ids)
    contract.require(len(indexes) == (1 if effect['index'] else 0), 'index.count')


def removed_rows(case, before, after, context, ids, events, indexes, effect, source):
    """The partial/invite rows deleted by the case, with their delete entries (in that order) consumed from events."""
    from auditlog.diff import model_instance_diff
    from application.models.user_models import CollaborationInvites, UserPartial
    prepared = context['prepared']
    for label, model, key, rows, seeded, store in (
            (PARTIAL, UserPartial, 'id', 'partials', source['partials'], 'partial'),
            (INVITES, CollaborationInvites, 'collaboration_invite_id', 'invites', source['invites'], 'invite')):
        old, new = stable_rows(before[label], key), stable_rows(after[label], key)
        gone = sorted(set(old) - set(new))
        contract.require(set(new) <= set(old) and all(new[k] == old[k] for k in new), 'state.kept:' + label)
        expected = effect.get(store)
        target = str(seeded[expected][key]) if expected else None
        contract.require(gone == ([target] if target else []), 'state.removed:' + label)
        if target:
            instance = prepared[rows][int(target) if key == 'id' else __import__('uuid').UUID(target)]
            changes = {k: list(v) for k, v in model_instance_diff(instance, None).items()}
            contract.require(events, 'audit.count')
            check_event(case, events.pop(0), model=model, instance=instance, action=2, changes=changes, actor=case_actor(case),
                        index=deletion_index(case_actor(case), ids), indexes=indexes, context=context, ids=ids)


def case_actor(case):
    return None if case['actor'] == 'anonymous' else case['actor']


def check_signup(case, before, after, context, ids, events, indexes, source):
    from django.contrib.auth.hashers import check_password, get_hashers, identify_hasher
    from auditlog.diff import model_instance_diff
    from application.models import User
    effect = case['effect']
    removed_rows(case, before, after, context, ids, events, indexes, effect, source)
    if effect['kind'] == 'removed':
        check_users(before, after)
        return None
    (key,) = check_users(before, after, created=[True])
    user = User._base_manager.get(pk=key)
    expected = effect['user']
    for field, value in expected.items():
        actual = getattr(user, field)
        actual = str(actual) if field == 'connected_user_id' and actual is not None else actual
        contract.require(type(actual) is type(value) and actual == value, 'signup.user:' + field)
    since, until = context['prepared']['started'], time.time()
    contract.require(user.is_active is True and user.is_staff is False and user.is_superuser is False and user.last_login is None
                     and since - 1 <= user.date_joined.timestamp() <= until + 1, 'signup.user-flags')
    for field in User._meta.concrete_fields:
        if field.attname in {'user_id', 'password', 'date_joined', 'is_active', 'is_staff', 'is_superuser', 'last_login', *expected}:
            continue
        contract.require(getattr(user, field.attname) == field.get_default(), 'signup.default:' + field.attname)
    algorithm, iterations, salt, digest = user.password.split('$')
    default = get_hashers()[0]
    contract.require(default.algorithm == 'pbkdf2_sha256' and identify_hasher(user.password).algorithm == default.algorithm
                     and iterations == str(default.iterations) == '1000000' and len(salt) >= 22 and salt.isalnum()
                     and len(digest) == 44, 'signup.password-format')
    contract.require(check_password(effect['password'], user.password)
                     and (effect['password'].strip() == effect['password'] or not check_password(effect['password'].strip(), user.password)),
                     'signup.password')
    changes = {k: list(v) for k, v in model_instance_diff(None, user).items()}
    contract.require(len(events) == 1, 'audit.count')
    check_event(case, events.pop(0), model=User, instance=user, action=0, changes=changes, actor=case_actor(case),
                index=effect['index'], indexes=indexes, context=context, ids=ids)
    if effect['response']:
        check_login_response(user)
    return user


def check_login_response(user):
    import json
    from django.conf import settings
    from rest_framework.renderers import JSONRenderer
    from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
    from application.models import User
    from application.serializers.auth_serializers import SportAssociationSerializer
    from application.services.jwt_token_service import JWTTokenService
    actual = contract.RUNTIME['response']
    access, refresh = AccessToken(actual['access_token']), RefreshToken(actual['refresh_token'])
    lifetime = int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds())
    for token, kind, span in ((access, 'access', settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME']),
                              (refresh, 'refresh', settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'])):
        claims = token.payload
        contract.require(claims['token_type'] == kind and claims['user_id'] == str(user.pk) and claims['role'] == user.role
                         and claims['email'] == user.email and claims['iss'] == 'https://bakney.com' and claims['aud'] == 'bakney'
                         and claims['exp'] - claims['iat'] == int(span.total_seconds()) and abs(claims['iat'] - time.time()) < 120
                         and re.fullmatch(r'[0-9a-f]{32}', claims['jti']), 'signup.token:' + kind)
    contract.require(access['jti'] != refresh['jti'], 'signup.token-ids')
    tokens = {'access_token': actual['access_token'], 'refresh_token': actual['refresh_token'], 'expires_in': lifetime,
              'token_type': 'Bearer'}
    content = JWTTokenService.build_login_response(user, tokens)
    if user.role == User.COLLABORATOR:
        association = user.connected_user.sport_association
        content['user_data']['sport_association'] = SportAssociationSerializer(association).data
        if association.regulation is None or association.regulation == '' or association.demand is None or association.demand == '':
            content['user_data']['sport_association']['empty_sections'] = True
        content['user_data']['requires_welcome'] = False
    expected = json.loads(JSONRenderer().render(content))
    contract.require(actual == expected, 'signup.response')


def check_delete(case, before, after, context, ids, events, indexes):
    from application.models import User
    effect = case['effect']
    user = User._base_manager.get(pk=effect['user'])
    since, until = context['prepared']['started'], time.time()
    dates = {(datetime.fromtimestamp(moment_, timezone.utc).date() + timedelta(days=30)) for moment_ in (since, until)}
    contract.require(user.delete_on in dates, 'delete.date')
    previous = stable_rows(before[USER], 'user_id')[effect['user']]['delete_on']
    if effect['changed']:
        contract.require(str(previous) == str(effect['before']) and previous != user.delete_on, 'delete.previous')
        check_users(before, after, changed=(effect['user'], {'delete_on'}))
        contract.require(len(events) == 1, 'audit.count')
        check_event(case, events.pop(0), model=User, instance=user, action=1,
                    changes={'delete_on': [str(previous), str(user.delete_on)]}, actor=effect['actor'], index=effect['index'],
                    indexes=indexes, context=context, ids=ids)
    else:
        contract.require(previous == user.delete_on, 'delete.unchanged-date')
        check_users(before, after)
        contract.require(not events, 'audit.count')
    for label in (PARTIAL, INVITES):
        contract.require(before[label] == after[label], 'state.unchanged:' + label)
    return {'$deletion_date': user.delete_on.strftime('%d/%m/%Y')}


def check_reset_request(case, context):
    effect = case['effect']
    old, new = context['prepared']['reset'], reset_state()
    contract.require(all(new.get(k) == v for k, v in old.items()), 'reset.kept')
    added = {k: v for k, v in new.items() if k not in old}
    contract.require(sorted(added.values()) == sorted(effect['users']) and len(added) == len(effect['users']), 'reset.tokens')
    client = RUNTIME['reset']
    tokens = {}
    for key, value in added.items():
        ttl = client.ttl(key)
        contract.require(re.fullmatch(r'[0-9a-f]{32}', key) is not None and 3500 < ttl <= 3600, 'reset.token-shape')
        tokens[f'$token:{effect["users"].index(value)}'] = key
    return tokens


def check_reset(case, before, after, context):
    from django.contrib.auth.hashers import check_password, get_hashers, identify_hasher
    from application.models import User
    effect = case['effect']
    old, new = context['prepared']['reset'], reset_state()
    contract.require(effect['token'] in old and new == {k: v for k, v in old.items() if k != effect['token']}, 'reset.consumed')
    user = User._base_manager.get(pk=effect['user'])
    default = get_hashers()[0]
    contract.require(identify_hasher(user.password).algorithm == default.algorithm == 'pbkdf2_sha256'
                     and user.password.split('$')[1] == '1000000' and check_password(effect['password'], user.password),
                     'reset.password')
    check_users(before, after, changed=(effect['user'], {'password'}))


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    fixture, ids = context['fixture'], source['ids']
    since, until = context['prepared']['started'], time.time()
    effect = case['effect']
    from auditlog.models import LogEntry
    added, indexes = harness.audit_delta(contract, before, after)
    events = list(LogEntry.objects.filter(pk__in=[row['id'] for row in added]).order_by('pk'))
    extra = {}
    kind = effect['kind'] if effect else None
    if kind not in ('reset-request', 'reset'):
        contract.require(reset_state() == context['prepared']['reset'], 'reset.unchanged')
    if kind is None:
        contract.require(all(before[label] == after[label] for label in MANAGED), 'state.no-effect')
    elif kind == 'partial':
        check_partial(case, before, after, context, ids, events, indexes)
    elif kind in ('signup', 'removed'):
        check_signup(case, before, after, context, ids, events, indexes, source)
    elif kind == 'delete':
        extra = check_delete(case, before, after, context, ids, events, indexes)
    elif kind == 'reset-request':
        extra = check_reset_request(case, context)
        contract.require(all(before[label] == after[label] for label in MANAGED), 'state.no-effect')
    elif kind == 'reset':
        check_reset(case, before, after, context)
        contract.require(all(before[label] == after[label] for label in MANAGED if label != USER), 'state.no-effect')
    else:
        raise AssertionError('effect.kind')
    if kind in ('partial', 'signup', 'removed', 'delete'):
        contract.require(not events, 'audit.unexpected')
    celery = resolve(effect['celery'], bindings(extra)) if effect and 'celery' in effect else []
    harness.celery_published(contract, fixture, celery, since=since, until=until)


run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
