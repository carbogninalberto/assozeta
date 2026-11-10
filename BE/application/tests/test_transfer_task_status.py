"""Status answers of the association import and export tasks."""
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django_celery_results.models import TaskResult
from rest_framework.test import APIClient

from application.export_progress import (
    IMPORT_TASK_NAME,
    TASK_FAILED_MESSAGE,
    build_export_snapshot,
    export_active_cache_key,
    export_task_cache_key,
    import_task_cache_key,
    set_active_export,
)
from application.models.user_models import User
from application.tests.fixtures.factories import (
    create_test_sport_association,
    create_test_user,
)

STORED_FAILURE = (
    '{"exc_type": "ValueError", "exc_message": ["stored failure detail"], '
    '"exc_module": "builtins"}'
)
SETUP_TOKEN = 'unit-setup-token'


def store(task_id, status, result, task_name=None):
    return TaskResult.objects.create(
        task_id=task_id, status=status, result=result, task_name=task_name,
        content_type='application/json', content_encoding='utf-8', meta='{"children": []}',
    )


@override_settings(INSTANCE_SETUP_TOKEN=SETUP_TOKEN)
class ImportStatusTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.client.credentials(HTTP_X_SETUP_TOKEN=SETUP_TOKEN)

    def tearDown(self):
        cache.clear()

    def status(self, task_id):
        response = self.client.get(f'/association/import/status?task_id={task_id}')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def register(self, task_id):
        cache.set(import_task_cache_key(task_id), {'task': IMPORT_TASK_NAME})

    def test_unregistered_task_answers_like_an_unknown_id(self):
        store('other-task', 'FAILURE', STORED_FAILURE)
        store('other-done', 'SUCCESS', '{"rows": 3}')

        for task_id in ('other-task', 'other-done', 'no-row'):
            self.assertEqual(
                self.status(task_id),
                {'task_id': task_id, 'status': 'PENDING', 'ready': False},
            )

    def test_registered_id_with_a_row_of_another_task_answers_like_an_unknown_id(self):
        store('reused-id', 'SUCCESS', '{"rows": 3}', task_name='cleanup_old_exports')
        self.register('reused-id')

        self.assertEqual(
            self.status('reused-id'),
            {'task_id': 'reused-id', 'status': 'PENDING', 'ready': False},
        )

    def test_registered_import_answers_its_result(self):
        store('import-done', 'SUCCESS', '{"success": true}', task_name=IMPORT_TASK_NAME)
        store('import-done-unnamed', 'SUCCESS', '{"success": true}')
        for task_id in ('import-done', 'import-done-unnamed'):
            self.register(task_id)
            self.assertEqual(
                self.status(task_id),
                {'task_id': task_id, 'status': 'SUCCESS', 'ready': True, 'result': {'success': True}},
            )

    def test_registered_import_without_a_row_is_pending(self):
        self.register('import-queued')

        self.assertEqual(
            self.status('import-queued'),
            {'task_id': 'import-queued', 'status': 'PENDING', 'ready': False},
        )

    def test_failed_import_answers_the_fixed_message_and_logs_the_stored_result(self):
        for task_id, state, stored in (
            ('import-failed', 'FAILURE', STORED_FAILURE),
            ('import-revoked', 'REVOKED', STORED_FAILURE),
            ('import-failed-text', 'FAILURE', '"stored failure detail"'),
        ):
            store(task_id, state, stored)
            self.register(task_id)
            with self.assertLogs('application.views.export_views', level='ERROR') as logs:
                body = self.status(task_id)
            self.assertEqual(
                body,
                {'task_id': task_id, 'status': state, 'ready': True, 'error': TASK_FAILED_MESSAGE},
            )
            self.assertIn('stored failure detail', '\n'.join(logs.output))

    def test_retrying_import_is_not_ready(self):
        store('import-retry', 'RETRY', STORED_FAILURE)
        self.register('import-retry')

        self.assertEqual(
            self.status('import-retry'),
            {'task_id': 'import-retry', 'status': 'RETRY', 'ready': False},
        )

    @patch('application.views.export_views.default_storage')
    @patch('application.views.export_views.import_association_data.apply_async')
    def test_start_registers_the_task_before_dispatch(self, apply_async, storage):
        storage.save.return_value = 'temp/imports/import_x.zip'
        registered = []
        apply_async.side_effect = lambda **options: registered.append(
            cache.get(import_task_cache_key(options['task_id']))
        )

        response = self.client.post(
            '/association/import/start',
            {'file': SimpleUploadedFile('export.zip', b'PK'), 'owner_password': 'secret'},
            format='multipart',
        )

        self.assertEqual(response.status_code, 202)
        task_id = response.json()['task_id']
        apply_async.assert_called_once_with(
            kwargs={'zip_file_path': 'temp/imports/import_x.zip', 'owner_password': 'secret'},
            task_id=task_id,
        )
        self.assertEqual(registered, [{'task': IMPORT_TASK_NAME}])
        self.assertEqual(
            self.status(task_id),
            {'task_id': task_id, 'status': 'PENDING', 'ready': False},
        )

    @patch('application.views.export_views.default_storage')
    @patch(
        'application.views.export_views.import_association_data.apply_async',
        side_effect=RuntimeError('broker unavailable'),
    )
    def test_start_drops_the_registration_when_dispatch_fails(self, apply_async, storage):
        storage.save.return_value = 'temp/imports/import_x.zip'

        response = self.client.post(
            '/association/import/start',
            {'file': SimpleUploadedFile('export.zip', b'PK')},
            format='multipart',
        )

        self.assertEqual(response.status_code, 500)
        task_id = apply_async.call_args.kwargs['task_id']
        self.assertIsNone(cache.get(import_task_cache_key(task_id)))
        storage.delete.assert_called_once_with('temp/imports/import_x.zip')


class ExportTaskStatusTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = create_test_user(role=User.ASSOCIATION)
        self.association = create_test_sport_association(user=self.owner)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def tearDown(self):
        cache.clear()

    def test_failed_export_status_answers_the_fixed_message(self):
        store('export-failed', 'FAILURE', STORED_FAILURE)
        cache.set(export_task_cache_key('export-failed'), {
            'sport_association_id': str(self.association.pk),
            'user_id': str(self.owner.pk),
        })

        with self.assertLogs('application.views.export_views', level='ERROR') as logs:
            response = self.client.get('/association/export/status?task_id=export-failed')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'task_id': 'export-failed', 'status': 'FAILURE', 'ready': True,
            'error': TASK_FAILED_MESSAGE,
        })
        self.assertIn('stored failure detail', '\n'.join(logs.output))

    def test_active_export_reads_the_stored_state_only(self):
        for task_id, state, stored, active in (
            ('export-failed-text', 'FAILURE', '"stored failure detail"', False),
            ('export-retry', 'RETRY', STORED_FAILURE, True),
            ('export-running', 'PROGRESS', '{"percent": 4}', True),
        ):
            store(task_id, state, stored)
            set_active_export(build_export_snapshot(
                task_id=task_id,
                sport_association_id=str(self.association.pk),
                user_id=str(self.owner.pk),
                status='PROGRESS',
            ))

            response = self.client.get('/association/export/active')

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['active'], active)
            self.assertEqual(
                cache.get(export_active_cache_key(self.association.pk)) is not None,
                active,
            )
