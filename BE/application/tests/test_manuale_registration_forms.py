"""Actual handlers and ORM regressions; these tests do not prove browser evidence."""
import io
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APIClient

from application.management.commands.seed_manuale import fixture_id
from application.models import Payment, SportAssociation, Subscription, User
from application.tests.base import BaseTestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_user


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ManualRegistrationFormsTests(BaseTestCase):
    def seed(self):
        # The only patch bypasses the disposable guard in a transaction-owned test DB.
        with tempfile.TemporaryDirectory() as temporary, patch('application.management.commands.seed_manuale.assert_disposable'):
            output = Path(temporary) / 'private-fixture.json'
            call_command('seed_manuale', output=str(output), origin='http://127.0.0.1:5010',
                         reference_date='2026-09-30', verbosity=0, stdout=io.StringIO(), stderr=io.StringIO())
            data = json.loads(output.read_text())
        self.assertEqual(data['fixture_version'], 8)
        self.assertEqual(data['fixture_profile'], 'baseline')
        return data

    def setUp(self):
        super().setUp()
        cache.clear()
        self.seed()
        self.owner = User.objects.get(pk=fixture_id('owner'))
        self.reader = User.objects.get(pk=fixture_id('reader'))
        self.association = SportAssociation.objects.get(pk=fixture_id('association'))
        self.owner_client = self.client_for(self.owner)
        self.reader_client = self.client_for(self.reader)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def configuration(self, client=None):
        response = (client or self.owner_client).get('/profile/info')
        self.assertEqual(response.status_code, 200, response.content)
        return response.data['user_data']['sport_association']

    def save(self, values, client=None):
        return (client or self.owner_client).patch('/profile/update/subscription/template',
                                                 {'sport_association': values}, format='json')

    def custom_configuration(self):
        values = deepcopy(self.configuration())
        values.update(enabled_for=['associate-membership'], subscription_fee='30.00',
            additional_sections=[{'name': 'Materiale per allenamento',
                'text': '<p>Porta borraccia e abbigliamento comodo.</p>',
                'show_to_members': False, 'show_to_both': True, 'show_to_athletes': False}],
            additional_fields=[{'type': 'text', 'icon': 'text', 'props': {
                'id': 'shirt-size', 'name': 'text_shirt_size', 'label': 'Taglia maglietta',
                'placeholder': 'Esempio: M', 'helperLabel': 'Indica la taglia desiderata.', 'required': False}}])
        return values

    def test_clearing_configured_fee_plans_preserves_other_fields_and_foreign_state(self):
        foreign = create_test_sport_association(subscription_fee_plans=[{
            'id': 'foreign-plan', 'name': 'Preserved', 'subscription_fee': 99}], multiple_subscription_fee=True)
        foreign_before = SportAssociation.objects.filter(pk=foreign.pk).values().get()
        members_before = list(Subscription._base_manager.order_by('pk').values())
        payments_before = list(Payment._base_manager.order_by('pk').values())
        configured = self.configuration()
        configured.update(multiple_subscription_fee=True, subscription_fee_plans=[
            {'id': 'ordinary-plan', 'name': 'Ordinaria', 'subscription_fee': '25.00'},
            {'id': 'supporter-plan', 'name': 'Sostenitore', 'subscription_fee': '45.00'}])
        saved = self.save(configured)
        self.assertEqual(saved.status_code, 200, saved.content)
        before_clear = self.configuration()
        self.assertEqual(len(before_clear['subscription_fee_plans']), 2)
        empty = deepcopy(before_clear)
        empty['subscription_fee_plans'] = []
        denied = self.save(empty, self.reader_client)
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(self.configuration(), before_clear)
        cleared = self.save(empty)
        self.assertEqual(cleared.status_code, 200, cleared.content)
        current = self.configuration(self.client_for(User.objects.get(pk=self.owner.pk)))
        self.assertEqual(current['subscription_fee_plans'], [])
        self.assertFalse(current['multiple_subscription_fee'])
        self.association.refresh_from_db()
        self.assertEqual(self.association.subscription_fee_plans, [])
        for key in before_clear.keys()-{'subscription_fee_plans', 'multiple_subscription_fee'}:
            self.assertEqual(current[key], before_clear[key], key)
        self.assertEqual(SportAssociation.objects.filter(pk=foreign.pk).values().get(), foreign_before)
        self.assertEqual(list(Subscription._base_manager.order_by('pk').values()), members_before)
        self.assertEqual(list(Payment._base_manager.order_by('pk').values()), payments_before)

    def test_owner_save_and_fresh_public_read_preserve_custom_configuration_without_business_writes(self):
        subscriptions = list(Subscription._base_manager.values())
        payments = list(Payment._base_manager.values())
        users = list(User._base_manager.values())
        original = self.configuration()
        values = self.custom_configuration()
        self.assertEqual(self.configuration(), original)  # Constructing a draft is not a write.
        response = self.save(values)
        self.assertEqual(response.status_code, 200, response.content)
        self.association.refresh_from_db()
        self.assertEqual(str(self.association.subscription_fee), '30.00')
        self.assertEqual(self.association.enabled_for, ['associate-membership'])
        section = self.association.additional_sections[0]
        self.assertEqual(section['name'], 'MATERIALE PER ALLENAMENTO')
        self.assertIn('Porta borraccia e abbigliamento comodo.', section['text'])
        self.assertTrue(section['show_to_both'])
        self.assertFalse(section['show_to_members'])
        self.assertFalse(section['show_to_athletes'])
        self.assertEqual(self.association.additional_fields, values['additional_fields'])
        fresh = self.configuration(self.client_for(User.objects.get(pk=self.owner.pk)))
        public = APIClient().get('/search/profile/' + self.owner.username.lower(), {'module_info': '1'})
        self.assertEqual(public.status_code, 200, public.content)
        published = public.data['data']['user']['sport_association']
        for key in ['enabled_for', 'subscription_fee', 'additional_fields', 'additional_sections']:
            self.assertEqual(published[key], fresh[key])
        self.assertEqual(list(Subscription._base_manager.values()), subscriptions)
        self.assertEqual(list(Payment._base_manager.values()), payments)
        self.assertEqual(list(User._base_manager.values()), users)

    def test_reader_and_unauthenticated_write_denied_without_changing_owner_or_foreign_association(self):
        foreign = create_test_sport_association(user=create_test_user(), additional_fields=[{'foreign': True}])
        before_owner = SportAssociation.objects.values().get(pk=self.association.pk)
        before_foreign = SportAssociation.objects.values().get(pk=foreign.pk)
        values = self.custom_configuration()
        values['sport_association_id'] = str(foreign.pk)
        denied = self.save(values, self.reader_client)
        self.assertEqual(denied.status_code, 403, denied.content)
        unauthenticated = self.save(values, APIClient())
        self.assertIn(unauthenticated.status_code, (401, 403), unauthenticated.content)
        self.assertEqual(SportAssociation.objects.values().get(pk=self.association.pk), before_owner)
        self.assertEqual(SportAssociation.objects.values().get(pk=foreign.pk), before_foreign)
        accepted = self.save(values)
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.association.refresh_from_db()
        self.assertEqual(self.association.enabled_for, ['associate-membership'])
        self.assertEqual(SportAssociation.objects.values().get(pk=foreign.pk), before_foreign)

    def test_handler_filters_empty_sections_uppercases_short_names_and_ignores_negative_simple_fee(self):
        values = self.configuration()
        values.update(subscription_fee='-1.00', additional_sections=[
            {'name': '', 'text': '<p>Non conservare</p>'}, {'name': 'Vuoto', 'text': ''},
            {'name': 'manca-testo'}, {'name': 'a', 'text': '<p>Breve ma presente.</p>'}],
            multiple_subscription_fee=True, subscription_fee_plans=[])
        response = self.save(values)
        self.assertEqual(response.status_code, 200, response.content)
        self.association.refresh_from_db()
        self.assertEqual(str(self.association.subscription_fee), '25.00')
        self.assertFalse(self.association.multiple_subscription_fee)
        self.assertEqual(len(self.association.additional_sections), 1)
        self.assertEqual(self.association.additional_sections[0]['name'], 'A')
        self.assertIn('<p>', self.association.additional_sections[0]['text'])
        # This handler is permissive about short names; do not invent a 3-character minimum.

    def test_reseed_resets_owned_fields_and_preserves_foreign_form_configuration(self):
        foreign = create_test_sport_association(user=create_test_user(), enabled_for=['membership'],
            additional_fields=[{'foreign-field': True}], additional_sections=[{'foreign-section': True}],
            regulation='Regolamento altra associazione', demand='Richiesta altra associazione',
            subscription_fee='87.00', show_regulation_to_members=False)
        before_foreign = SportAssociation.objects.values().get(pk=foreign.pk)
        response = self.save(self.custom_configuration())
        self.assertEqual(response.status_code, 200, response.content)
        SportAssociation.objects.filter(pk=self.association.pk).update(
            regulation='Modificato', demand='Modificata', show_regulation_to_members=False,
            show_regulation_to_both=False, show_regulation_to_athletes=False)
        self.seed()
        self.association.refresh_from_db()
        self.assertEqual(self.association.enabled_for, ['associate', 'associate-membership', 'membership'])
        self.assertEqual(self.association.additional_fields, [])
        self.assertEqual(self.association.additional_sections, [])
        self.assertEqual(self.association.regulation, 'Regolamento dimostrativo per le attività associative.')
        self.assertEqual(self.association.demand, 'Richiesta di iscrizione dimostrativa.')
        self.assertEqual(str(self.association.subscription_fee), '25.00')
        self.assertEqual(str(self.association.membership_fee), '0.00')
        self.assertTrue(self.association.show_regulation_to_members)
        self.assertTrue(self.association.show_regulation_to_both)
        self.assertTrue(self.association.show_regulation_to_athletes)
        self.assertEqual(SportAssociation.objects.values().get(pk=foreign.pk), before_foreign)
