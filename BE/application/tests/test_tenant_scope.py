from application.models import Group, User
from application.models.payment_models import SupplierAndCustomers
from application.serializers.payment_serializers import SupplierSerializer
from application.tenant_scope import clear_scope
from application.tests.base import BaseAPITestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_user
from communications.models import AutomationWorkflow


class TenantScopedRelationsTests(BaseAPITestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.own = create_test_sport_association(user=self.owner)
        self.other = create_test_sport_association(user=create_test_user(role=User.ASSOCIATION))
        self.own_group = Group.objects.create(name='own', sport_association=self.own)
        self.foreign_group = Group.objects.create(name='foreign', sport_association=self.other)
        self.client.force_authenticate(self.owner)

    def test_update_cannot_move_supplier_to_another_association(self):
        supplier = SupplierAndCustomers.objects.create(name='s', sport_association=self.own)
        response = self.client.patch(f'/supplier/{supplier.pk}/update', {'sport_association': str(self.other.pk)}, format='json')
        self.assertEqual(response.status_code, 403)
        supplier.refresh_from_db()
        self.assertEqual(supplier.sport_association_id, self.own.pk)

    def test_add_rejects_foreign_group_and_accepts_own(self):
        foreign = self.client.post('/supplier/add', {'name': 'x', 'group': str(self.foreign_group.pk)}, format='json')
        self.assertEqual(foreign.status_code, 403)
        self.assertFalse(SupplierAndCustomers.objects.filter(name='x').exists())
        own = self.client.post('/supplier/add', {'name': 'y', 'group': str(self.own_group.pk)}, format='json')
        self.assertEqual(own.status_code, 201)
        self.assertEqual(SupplierAndCustomers.objects.get(name='y').sport_association_id, self.own.pk)

    def test_own_association_reference_still_valid(self):
        supplier = SupplierAndCustomers.objects.create(name='s', sport_association=self.own)
        response = self.client.patch(f'/supplier/{supplier.pk}/update', {'sport_association': str(self.own.pk), 'name': 'n'}, format='json')
        self.assertEqual(response.status_code, 200)

    def test_workflow_update_cannot_move_to_another_association(self):
        workflow = AutomationWorkflow.objects.create(sport_association=self.own, name='w', automation_tree=[])
        response = self.client.patch(f'/communications/workflows/{workflow.pk}/update',
                                     {'sport_association': str(self.other.pk)}, format='json')
        self.assertEqual(response.status_code, 403)
        workflow.refresh_from_db()
        self.assertEqual(workflow.sport_association_id, self.own.pk)

    def test_no_request_scope_is_unrestricted(self):
        clear_scope()
        serializer = SupplierSerializer(data={'name': 'z', 'sport_association': str(self.other.pk)})
        self.assertTrue(serializer.is_valid(), serializer.errors)


class TenantScopeInstallTests(BaseAPITestCase):
    def test_every_identity_entry_point_is_wrapped(self):
        from application import impersonation
        from core import authentication
        self.assertTrue(getattr(impersonation.resolve_request_identity, 'tenant_scoped', False))
        self.assertIs(authentication.resolve_request_identity, impersonation.resolve_request_identity)
