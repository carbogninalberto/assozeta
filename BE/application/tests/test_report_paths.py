"""Tests for the field and relation paths a report may read.

Only model metadata is exercised: no database access, no QueryBuilder() construction.
"""
from django.apps import apps
from django.test import SimpleTestCase

from application.mcp_server.query_builder import (
    QueryBuilder,
    _allowed_order_by,
    _build_aggregations,
    _split_joins,
    _validate_group_by,
)
from application.mcp_server.schema import get_model_schema
from application.mcp_server.security import REPORT_ALLOWLIST, is_path_allowed, validate_filter_fields


def model(name):
    return apps.get_model('application', name)


def builder():
    return QueryBuilder.__new__(QueryBuilder)


class PathAllowlistTests(SimpleTestCase):
    def test_own_field_is_readable(self):
        self.assertTrue(is_path_allowed(model('Associate'), 'first_name'))

    def test_relation_chain_is_readable(self):
        self.assertTrue(is_path_allowed(model('CourseSubscription'), 'subscription__associate__email'))
        self.assertTrue(is_path_allowed(model('Associate'), 'subscription__coursesubscription__course__title'))

    def test_relation_id_column_is_readable(self):
        self.assertTrue(is_path_allowed(model('Subscription'), 'associate_id'))

    def test_json_key_path_is_readable(self):
        self.assertTrue(is_path_allowed(model('CarnetSubscription'), 'meta__lessons_left__gt'))

    def test_lookup_after_relation_is_readable(self):
        self.assertTrue(is_path_allowed(model('CourseSubscription'), 'course__in'))

    def test_unknown_name_stays_readable(self):
        self.assertTrue(is_path_allowed(model('Tags'), 'colour'))

    def test_blocked_name_as_first_segment_is_not_readable(self):
        cases = [
            ('Tags', 'password'),
            ('Tags', 'password__icontains'),
            ('Tags', 'token'),
        ]
        for model_name, path in cases:
            with self.subTest(model=model_name, path=path):
                self.assertFalse(is_path_allowed(model(model_name), path))

    def test_json_key_after_readable_field_stays_readable(self):
        self.assertTrue(is_path_allowed(model('CarnetSubscription'), 'meta__token'))

    def test_relation_to_account_model_is_not_readable(self):
        cases = [
            ('Instructor', 'user__password'),
            ('SportAssociation', 'user__email'),
            ('Subscription', 'user__username'),
            ('Payment', 'user__is_superuser'),
            ('MedicalCertificate', 'user'),
            ('Reminders', 'user_id'),
        ]
        for model_name, path in cases:
            with self.subTest(model=model_name, path=path):
                self.assertFalse(is_path_allowed(model(model_name), path))

    def test_second_hop_to_account_model_is_not_readable(self):
        self.assertFalse(is_path_allowed(model('Subscription'), 'associate__user__email'))
        self.assertFalse(is_path_allowed(model('CourseSubscription'), 'subscription__user__password'))

    def test_relation_to_unlisted_model_is_not_readable(self):
        self.assertFalse(is_path_allowed(model('Subscription'), 'supplier__name'))
        self.assertFalse(is_path_allowed(model('Subscription'), 'document_pdf'))

    def test_carnet_account_relation_is_not_readable(self):
        self.assertFalse(is_path_allowed(model('Carnet'), 'user_id'))
        self.assertFalse(is_path_allowed(model('Carnet'), 'user_id_id'))

    def test_dropped_columns_are_not_readable(self):
        cases = [
            ('Instructor', 'associated_user_id'),
            ('Payment', 'payment_intent_id'),
            ('SportAssociation', 'crm_user_id'),
        ]
        for model_name, path in cases:
            with self.subTest(model=model_name, path=path):
                self.assertFalse(is_path_allowed(model(model_name), path))

    def test_non_string_path_raises_attribute_error(self):
        with self.assertRaises(AttributeError):
            is_path_allowed(model('Associate'), None)


class FilterPathTests(SimpleTestCase):
    def test_filter_validation_drops_blocked_path_and_keeps_readable_one(self):
        validated = validate_filter_fields(
            model('Instructor'),
            {'user__email': 'a@b.c', 'email': 'a@b.c', 'subscription__status_flag': 2},
        )
        self.assertEqual(validated, {'email': 'a@b.c', 'subscription__status_flag': 2})

    def test_exclude_and_or_filter_paths_are_validated_the_same_way(self):
        for key in ('user__email', 'subscription__user__password'):
            with self.subTest(key=key):
                self.assertEqual(validate_filter_fields(model('Instructor'), {key: 'x'}), {})

    def test_filter_validation_drops_blocked_name_that_is_not_a_field(self):
        validated = validate_filter_fields(model('Tags'), {'password': 'x', 'tag_name': 'a'})
        self.assertEqual(validated, {'tag_name': 'a'})


class FieldListTests(SimpleTestCase):
    def test_given_fields_drop_blocked_paths(self):
        valid = builder()._validate_fields_for_model(
            model('Instructor'),
            ['first_name', 'user__email', 'associated_user_id', 'subscription__status_flag'],
        )
        self.assertEqual(valid, ['first_name', 'subscription__status_flag'])

    def test_default_field_list_has_no_unlisted_column(self):
        for model_name, blocked in (('SportAssociation', 'crm_user_id'), ('Instructor', 'associated_user_id')):
            with self.subTest(model=model_name):
                names = builder()._validate_fields_for_model(model(model_name), None)
                self.assertNotIn(blocked, names)
                self.assertTrue(names)
                self.assertTrue(set(names) <= set(REPORT_ALLOWLIST[f'application.{model_name}']['fields']))


class GroupByAndOrderingTests(SimpleTestCase):
    def test_group_by_drops_blocked_path_and_keeps_readable_ones(self):
        valid = _validate_group_by(model('Instructor'), ['email', 'user__email', 'user', 'subscription__status_flag'])
        self.assertEqual(valid, ['email', 'subscription__status_flag'])

    def test_group_by_keeps_date_part_of_readable_field(self):
        self.assertEqual(
            _validate_group_by(model('Payment'), ['payment_date__year', 'subscription__user__email']),
            ['payment_date__year'],
        )

    def test_order_by_drops_blocked_path_and_keeps_minus_prefix(self):
        valid = _allowed_order_by(
            model('Instructor'),
            ['-first_name', 'user__email', '-subscription__status_flag', 'user'],
        )
        self.assertEqual(valid, ['-first_name', '-subscription__status_flag'])


class JoinTests(SimpleTestCase):
    def test_joins_are_split_and_blocked_paths_are_skipped(self):
        select_related, prefetch_related = _split_joins(
            model('Instructor'),
            ['payment', 'user', 'user__email', 'subscription'],
        )
        self.assertEqual(select_related, [])
        self.assertEqual(prefetch_related, ['payment', 'subscription'])

    def test_joins_split_keeps_forward_relation_in_select_related(self):
        select_related, prefetch_related = _split_joins(
            model('CourseSubscription'),
            ['subscription', 'carnetsubscription', 'subscription__user__password', 'course'],
        )
        self.assertEqual(select_related, ['subscription', 'course'])
        self.assertEqual(prefetch_related, ['carnetsubscription'])

    def test_unknown_join_keeps_previous_handling(self):
        select_related, prefetch_related = _split_joins(model('Tags'), ['colour'])
        self.assertEqual(select_related, [])
        self.assertEqual(prefetch_related, ['colour'])


class AggregationTests(SimpleTestCase):
    def test_blocked_aggregation_field_is_skipped(self):
        agg_kwargs = _build_aggregations(
            model('Instructor'),
            [
                {'function': 'count', 'field': '*'},
                {'function': 'count', 'field': 'user__email'},
                {'function': 'sum', 'field': 'default_hourly_billing', 'alias': 'total'},
            ],
        )
        self.assertIn('count_*', agg_kwargs)
        self.assertIn('total', agg_kwargs)
        self.assertNotIn('count_user__email', agg_kwargs)
        self.assertEqual(len(agg_kwargs), 2)

    def test_aggregation_on_readable_relation_chain_is_kept(self):
        agg_kwargs = _build_aggregations(
            model('CourseSubscription'),
            [{'function': 'max', 'field': 'subscription__start_date', 'alias': 'latest'}],
        )
        self.assertIn('latest', agg_kwargs)

    def test_non_string_aggregation_field_raises_attribute_error(self):
        with self.assertRaises(AttributeError):
            _build_aggregations(model('Instructor'), [{'function': 'sum', 'field': 1}])


class FieldValuesTests(SimpleTestCase):
    def test_blocked_path_raises_before_querying(self):
        with self.assertRaises(ValueError) as ctx:
            builder().get_field_values('Instructor', 'user__email')
        self.assertEqual(str(ctx.exception), "Field 'user__email' is not allowed")


class ModelSchemaTests(SimpleTestCase):
    def test_schema_lists_only_readable_members(self):
        schema = get_model_schema('application.Instructor')
        field_names = [f['name'] for f in schema['fields']]
        relation_names = [r['name'] for r in schema['relations']]
        self.assertNotIn('user', relation_names)
        self.assertNotIn('associated_user_id', field_names)
        self.assertEqual(relation_names, ['payment', 'subscription', 'reminders'])
        self.assertEqual(set(field_names), set(REPORT_ALLOWLIST['application.Instructor']['fields']))
        self.assertEqual(len(field_names), len(set(field_names)))

    def test_schema_relations_point_to_listed_models(self):
        for model_label in REPORT_ALLOWLIST:
            with self.subTest(model=model_label):
                schema = get_model_schema(model_label)
                for relation in schema['relations']:
                    self.assertIn(relation['related_model'], REPORT_ALLOWLIST)
                allowed = REPORT_ALLOWLIST[model_label]
                self.assertEqual({r['name'] for r in schema['relations']}, set(allowed['relations']))
                self.assertEqual({f['name'] for f in schema['fields']}, set(allowed['fields']))
