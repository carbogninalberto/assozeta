"""Parent-only DefaultRouter templates HTTP runner; imports are inert.

Seeds module templates (audit disabled); creates, partial updates and
deletes are checked against the complete model state with one auditlog
entry each (none for an unchanged update); everything else must leave the
state unchanged.
"""
from TESTS.contracts import test_router_templates_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import router_stock

SOURCES = ('TESTS/fixtures/router_templates_http.json', 'TESTS/contracts/test_router_templates_http.py', 'TESTS/contracts/router_stock.py',
           'TESTS/adapters/django/router_templates_http.py', 'BE/application/urls.py', 'BE/application/views/archive_views.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/models/user_models.py', 'BE/application/tenant_scope.py',
           'BE/application/audit_resolvers.py') + router_stock.SOURCES + harness.SOURCES
FIELDS = ('name', 'template', 'footer', 'header', 'custom_footer_header', 'custom_footer', 'custom_header')


def model():
    from application.models import SportAssociationModuleTemplates
    return SportAssociationModuleTemplates


def seed_rows(source, created):
    for values in source['seed'].values():
        model()._base_manager.bulk_create([model()(sport_association_module_templates_id=values['id'],
                                                   sport_association_id=values['association'], **{f: values[f] for f in FIELDS})])
        created.append((model(), values['id']))


effect, prepare = router_stock.single_model(contract, label='application.SportAssociationModuleTemplates', load=model,
                                            pk='sport_association_module_templates_id')
fixture_context = harness.fixture_context_for(contract, seed_rows, purge=(model,))
run_workload = harness.run_workload_for(contract, effect, prepare=prepare)
