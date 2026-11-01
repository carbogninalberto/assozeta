"""Parent-only personas reads HTTP runner; imports are inert.

Seeds two families, the associates (own, grouped, deleted and foreign), their
tutor relations and subscriptions (audit disabled), and pins the born and
creation dates of the shared fixture's associate. The routes are reads (the
authored DELETE cases all fail before any write): every case must leave the
complete model state unchanged.
"""
from datetime import date, datetime

from TESTS.contracts import test_personas_reads_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/personas_reads_http.json', 'TESTS/contracts/test_personas_reads_http.py',
           'TESTS/adapters/django/personas_reads_http.py', 'BE/application/views/personas_views.py',
           'BE/application/serializers/personas_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/utils/subscriptions_utils.py',
           'BE/application/utils/api_utils.py', 'BE/application/mixin.py', 'BE/application/permissions_registry.py',
           'BE/application/impersonation_scope.py', 'BE/application/urls.py') + harness.SOURCES

FIELDS = ('picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_city', 'address_city', 'address', 'address_cap',
          'nationality', 'nationality_residence', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label', 'phone_2_label',
          'phone_3_label', 'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role', 'id_number', 'migrated', 'notes')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import Associate, AssociateTutorRelation, Family, Subscription
    ids = source['ids']

    def put(model, **values):
        row = model(**values)
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    for family in source['families']:
        put(Family, family_id=family)
    for row in source['associates'].values():
        put(Associate, associate_id=row['id'], user_id=row['user'], sport_association_id=row['association'], group_id=row['group'],
            family_id=row['family'], born_date=day(row['born_date']), creation_date=moment(row['created']),
            **{key: row[key] for key in FIELDS})
    base = source['fixture_associate']
    contract.require(Associate._base_manager.filter(pk=base['id']).update(born_date=day(base['born_date']),
                                                                         creation_date=moment(base['created'])) == 1,
                     'fixture.shared-associate')
    for associate, tutor, primary in source['relations']:
        relation = AssociateTutorRelation._base_manager.create(associate_id=associate, tutor_id=tutor, is_primary=primary)
        created.append((AssociateTutorRelation, relation.pk))
    for key, row in source['subscriptions'].items():
        put(Subscription, subscription_id=key, sport_association_id=row.get('association', ids['own_association']),
            user_id=ids['owner'], associate_id=row['associate'], start_date=day(row['start']), end_date=day(row['end']),
            status_flag=row.get('status_flag', 1), type=row.get('type', 2), archived=row.get('archived', False),
            deleted=row.get('deleted', False), group_id=row.get('group'))


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
