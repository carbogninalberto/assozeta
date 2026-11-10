"""Parent-only association/impersonation/users and administration/impersonation/users HTTP runner; imports are inert.

seed_rows inserts (audit disabled) a self-hosted InstanceConfiguration whose
primary association is the owner's (so 'owner' is the instance owner and
'foreign_owner' is not), the authored users (athletes, an inactive and a
deleted athlete, a collaborator of the foreign owner), a live Associate row
linking one athlete to the foreign association, the foreign association's own
Associate row (user = its owner), the authored subscriptions that point at
athletes and the further member/subscription links (some deleted) of athletes
linked to both associations. The routes only read: every case must leave the complete state
unchanged.
"""
from datetime import datetime, timezone

from TESTS.contracts import test_impersonation_users_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/impersonation_users_http.json', 'TESTS/contracts/test_impersonation_users_http.py',
           'TESTS/adapters/django/impersonation_users_http.py', 'BE/application/views/admin_views.py', 'BE/application/impersonation.py',
           'BE/instance/permissions.py', 'BE/instance/models.py', 'BE/application/models/user_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/permissions_registry.py', 'BE/core/middleware.py',
           'BE/application/urls.py') + harness.SOURCES


def seed_rows(source, created):
    from application.models import Associate, Subscription, User
    from instance.models import InstanceConfiguration
    ids = source['ids']

    def insert(model, row):
        contract.require(row.pk is None or not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    insert(InstanceConfiguration, InstanceConfiguration(domain='impersonation-users.example.test', name='Impersonation users fixture',
                                                        self_hosted=True, primary_association_id=ids['own_association']))
    for label, spec in source['users'].items():
        insert(User, User(user_id=spec['id'], username=spec['username'], email=spec['email'], first_name=spec['first_name'],
                          last_name=spec['last_name'], role=spec['role'], is_active=spec['active'], deleted=spec['deleted'],
                          password='!synthetic', connected_user_id=ids['foreign_owner'] if spec['role'] == 3 else None,
                          collaborator_role=User.FULL))
    insert(Associate, Associate(associate_id=source['owner_member'], user_id=ids['foreign_owner'], sport_association_id=ids['foreign_association'],
                                first_name='Mario', last_name='Bianchi'))
    for label, identifier in source['members'].items():
        spec = source['users'][label]
        insert(Associate, Associate(associate_id=identifier, user_id=spec['id'], sport_association_id=ids[spec['link'][1]],
                                    first_name=spec['first_name'], last_name=spec['last_name']))
    start = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
    for label, identifier in source['subscriptions'].items():
        spec = source['users'][label]
        insert(Subscription, Subscription(subscription_id=identifier, user_id=spec['id'], sport_association_id=ids[spec['link'][1]],
                                          associate_id=source['owner_member'], deleted=spec['link'][2], start_date=start,
                                          end_date=datetime(2027, 8, 31, 8, tzinfo=timezone.utc), creation_date=start))
    links = source['more_links']
    contract.require(bool(links), 'fixture.more-links')
    for label, kind, association, deleted, identifier in links:
        spec = source['users'][label]
        if kind == 'member':
            insert(Associate, Associate(associate_id=identifier, user_id=spec['id'], sport_association_id=ids[association],
                                        first_name=spec['first_name'], last_name=spec['last_name'], deleted=deleted))
        else:
            insert(Subscription, Subscription(subscription_id=identifier, user_id=spec['id'], sport_association_id=ids[association],
                                              associate_id=source['owner_member'], deleted=deleted, start_date=start,
                                              end_date=datetime(2027, 8, 31, 8, tzinfo=timezone.utc), creation_date=start))


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
