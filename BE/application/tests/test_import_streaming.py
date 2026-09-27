"""Regression tests for the memory-bounded setup import path."""
import codecs
import io
import json
import tempfile
import uuid
import zipfile
from unittest import mock

from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from storages.backends.s3 import S3File

from application.models import Group, User
from application.services.import_service import AssociationImportService, ImportOptions
from application.services.json_stream import iter_json_array
from application.tasks import _stage_stored_archive
from docmanager.models import Document


class JsonArrayStreamTests(TestCase):
    def test_decodes_values_across_small_chunks(self):
        records = [
            {'name': 'àèìòù', 'nested': {'list': [1, 2, 3]}},
            [1, 2, {'nested': True}],
            None,
            12.5,
            'text',
        ]
        payload = json.dumps(records, ensure_ascii=False).encode('utf-8')

        self.assertEqual(list(iter_json_array(io.BytesIO(payload), chunk_size=1)), records)

    def test_accepts_whitespace_bom_and_empty_arrays(self):
        payload = codecs.BOM_UTF8 + b'\n [ ] \t'
        self.assertEqual(list(iter_json_array(io.BytesIO(payload), chunk_size=2)), [])

        payload = b'  [ {"a": 1} , {"b": 2} ]  '
        self.assertEqual(
            list(iter_json_array(io.BytesIO(payload), chunk_size=3)),
            [{'a': 1}, {'b': 2}],
        )

    def test_accepts_the_same_encodings_as_json_loads(self):
        source = '[{"name": "città", "n": 1}, null]'
        for encoding in ('utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'utf-32'):
            with self.subTest(encoding=encoding):
                payload = source.encode(encoding)
                for chunk_size in (1, 3, 64):
                    self.assertEqual(
                        list(iter_json_array(io.BytesIO(payload), chunk_size=chunk_size)),
                        json.loads(payload),
                    )

    def test_raw_surrogate_bytes_match_json_loads(self):
        cases = {
            'utf-8': b'["\xed\xa0\x80", {"a": 1}, "\xed\xb0\x80"]',
            'utf-16': '["\ud800", {"a": 1}, "\udc00"]'.encode('utf-16', 'surrogatepass'),
            'utf-16-le': '["\ud800", {"a": 1}, "\udc00"]'.encode('utf-16-le', 'surrogatepass'),
            'utf-16-be': '["\ud800", {"a": 1}, "\udc00"]'.encode('utf-16-be', 'surrogatepass'),
            'utf-32': '["\ud800", {"a": 1}, "\udc00"]'.encode('utf-32', 'surrogatepass'),
            'utf-32-le': '["\ud800", {"a": 1}, "\udc00"]'.encode('utf-32-le', 'surrogatepass'),
            'utf-32-be': '["\ud800", {"a": 1}, "\udc00"]'.encode('utf-32-be', 'surrogatepass'),
        }
        for encoding, payload in cases.items():
            expected = json.loads(payload)
            for chunk_size in (1, 2, 3, 5, 7):
                with self.subTest(encoding=encoding, chunk_size=chunk_size):
                    self.assertEqual(
                        list(iter_json_array(io.BytesIO(payload), chunk_size=chunk_size)),
                        expected,
                    )

    def test_rejects_malformed_input(self):
        for payload in (b'', b'{}', b'[1, 2', b'[1, 2] trailing', b'[1, ]'):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    list(iter_json_array(io.BytesIO(payload), chunk_size=2))


class StageArchiveTests(TestCase):
    def test_stage_stored_archive_never_reads_the_whole_file(self):
        payload = b'archive-bytes' * 1024

        class StrictStream(io.BytesIO):
            def read(self, size=-1):
                if size is None or size < 0:
                    raise AssertionError('the stored archive was read in one piece')
                return super().read(size)

        with mock.patch(
            'application.tasks.default_storage.open',
            return_value=StrictStream(payload),
        ):
            with tempfile.TemporaryFile() as destination:
                _stage_stored_archive('temp/imports/example.zip', destination)
                destination.seek(0)
                self.assertEqual(destination.read(), payload)

    def test_stage_streams_s3_objects_straight_to_the_destination(self):
        payload = b'x' * (4 * 1024 * 1024)
        storage = mock.Mock()
        storage.location = ''
        storage.max_memory_size = 0
        storage.gzip = False
        storage.transfer_config = None
        storage.get_object_parameters.return_value = {}
        obj = storage.bucket.Object.return_value
        obj.content_length = len(payload)
        downloads = []

        def download(destination, **kwargs):
            for offset in range(0, len(payload), 1024 * 1024):
                destination.write(payload[offset:offset + 1024 * 1024])
            downloads.append(destination)

        obj.download_fileobj.side_effect = download
        storage.open.side_effect = lambda name, mode='rb': S3File(name, mode, storage)

        with mock.patch('application.tasks.default_storage', storage):
            with tempfile.TemporaryFile() as destination:
                _stage_stored_archive('import.zip', destination)
                destination.seek(0)
                self.assertEqual(destination.read(), payload)

        self.assertEqual(len(downloads), 1)
        self.assertIs(downloads[0], destination)

        # S3File itself buffers the whole object in memory on the first read;
        # the staging copy must bypass that behaviour (no external writes).
        buffered = S3File('import.zip', 'rb', storage)
        try:
            self.assertEqual(buffered.read(1), b'x')
            self.assertFalse(buffered.file._rolled)
            self.assertEqual(buffered.file._file.getbuffer().nbytes, len(payload))
        finally:
            buffered.close()
        self.assertEqual(len(downloads), 2)
        self.assertIsNot(downloads[1], destination)


class ImportStreamingTests(TestCase):
    def _write_archive(self, path, users, groups, files=None):
        association_id = str(uuid.uuid4())
        owner_id = str(users[0]['user_id'])
        manifest = {
            'version': '1.0.0',
            'export_format': 'bakney_sport_export_v1',
            'export_date': '2026-09-26T00:00:00Z',
            'association': {
                'sport_association_id': association_id,
                'denomination': 'Streaming ASD',
                'tax_code': '12345678901',
            },
            'models_exported': [
                {'name': 'SportAssociation', 'file': 'data/01_sport_association.json', 'count': 1},
                {'name': 'User', 'file': 'data/02_users.json', 'count': len(users)},
                {'name': 'Group', 'file': 'data/03_groups.json', 'count': len(groups)},
            ],
        }
        association = {
            'sport_association_id': association_id,
            'user_id': owner_id,
            'denomination': 'Streaming ASD',
            'tax_code': '12345678901',
        }
        for index, group in enumerate(groups):
            group['sport_association_id'] = association_id

        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('manifest.json', json.dumps(manifest))
            zf.writestr('data/01_sport_association.json', json.dumps([association]))
            zf.writestr('data/02_users.json', json.dumps(users))
            zf.writestr('data/03_groups.json', json.dumps(groups))
            for archive_name, content in (files or {}).items():
                zf.writestr(archive_name, content)

    @staticmethod
    def _user(user_id, username, **extra):
        record = {
            'user_id': str(user_id),
            'username': username,
            'email': f'{username}@example.com',
            'role': User.COLLABORATOR,
            'is_active': True,
            'is_staff': False,
            'is_superuser': False,
            'two_fa': False,
        }
        record.update(extra)
        return record

    def test_import_streams_data_and_media_members(self):
        owner_id, other_id = uuid.uuid4(), uuid.uuid4()
        document_id = uuid.uuid4()
        users = [self._user(owner_id, 'owner'), self._user(other_id, 'member')]
        groups = [{'group_id': str(uuid.uuid4()), 'name': 'One', 'description': None},
                  {'group_id': str(uuid.uuid4()), 'name': 'Two', 'description': None}]

        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, users, groups, files={
                f'files/documents/{document_id}/payload.bin': b'streamed-media',
            })
            whole_read = zipfile.ZipFile.read

            def guarded_read(zf, name, *args, **kwargs):
                if name.startswith('files/') or name in (
                    'data/02_users.json', 'data/03_groups.json',
                ):
                    raise AssertionError(f'archive member was read in one piece: {name}')
                return whole_read(zf, name, *args, **kwargs)

            with tempfile.TemporaryDirectory() as storage_dir:
                with override_settings(
                    STORAGES={
                        'default': {
                            'BACKEND': 'django.core.files.storage.FileSystemStorage',
                            'OPTIONS': {'location': storage_dir},
                        },
                        'staticfiles': {
                            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
                        },
                    },
                    AWS_S3_PUBLIC_BASE_URL='',
                ), mock.patch.object(zipfile.ZipFile, 'read', guarded_read):
                    service = AssociationImportService(
                        archive.name,
                        ImportOptions(owner_password='Recovery!123'),
                    )
                    service.import_all()
                    document = Document.objects.get(document_id=document_id)
                    with default_storage.open(document.filepath, 'rb') as stored:
                        stored_media = stored.read()

        self.assertEqual(service.stats.get('Group'), 2)
        self.assertEqual(service.stats.get('files_imported'), 1)
        self.assertEqual(Group.objects.count(), 2)
        self.assertEqual(stored_media, b'streamed-media')

    def test_forward_user_self_reference_is_still_resolved(self):
        owner_id, first_id, second_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        users = [
            self._user(owner_id, 'owner', role=User.ASSOCIATION),
            self._user(first_id, 'first', connected_user_id=str(second_id)),
            self._user(second_id, 'second'),
        ]

        with tempfile.NamedTemporaryFile(suffix='.zip') as archive:
            self._write_archive(archive.name, users, [])
            service = AssociationImportService(
                archive.name,
                ImportOptions(owner_password='Recovery!123'),
            )
            service.import_all()

        first = User.original_objects.get(user_id=first_id)
        second = User.original_objects.get(user_id=second_id)
        self.assertEqual(first.connected_user_id, second.pk)
