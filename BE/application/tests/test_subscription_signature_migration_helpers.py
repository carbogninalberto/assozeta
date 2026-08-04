import base64
from io import BytesIO
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from application.migration_helpers import (
    SIGNATURE_STORAGE_KEY_COLUMN,
    SIGNATURE_URL_COLUMN,
    SignatureMigrationError,
    derive_existing_signature_storage_key,
    process_legacy_signature_value,
    recover_missing_signature_reference,
    recover_orphan_signature_key,
)


class FakeMigrationStorage:
    def __init__(self, objects=None, location='storage', save_error=False):
        self.objects = set(objects or [])
        self.location = location
        self.save_error = save_error
        self.saved = []

    def exists(self, name):
        return str(name).strip('/') in self.objects

    def listdir(self, path):
        prefix = str(path).strip('/').rstrip('/')
        if prefix:
            prefix = f'{prefix}/'

        directories = set()
        files = []
        for object_key in self.objects:
            if not object_key.startswith(prefix):
                continue
            remainder = object_key[len(prefix):]
            if not remainder:
                continue
            if '/' in remainder:
                directories.add(remainder.split('/', 1)[0])
            else:
                files.append(remainder)
        return sorted(directories), sorted(files)

    def save(self, name, content):
        if self.save_error:
            raise RuntimeError('storage unavailable')
        data = content.read() if hasattr(content, 'read') else bytes(content)
        key = str(name).strip('/')
        self.saved.append(SimpleNamespace(name=key, data=data))
        self.objects.add(key)
        return key

    def open(self, name, mode='rb'):
        return BytesIO(b'')


@override_settings(AWS_LOCATION='storage', AWS_STORAGE_BUCKET_NAME='bucket')
class SubscriptionSignatureMigrationHelperTests(SimpleTestCase):
    subscription_id = '00000000-0000-0000-0000-000000000123'

    def test_orphan_recovery_uses_latest_existing_listed_signature(self):
        storage = FakeMigrationStorage(objects={
            f'subscriptions/{self.subscription_id}/signature_1.png',
            f'subscriptions/{self.subscription_id}/signature_3.png',
            f'subscriptions/{self.subscription_id}/notes.txt',
        })

        storage_key = recover_orphan_signature_key(self.subscription_id, storage=storage)

        self.assertEqual(
            storage_key,
            f'subscriptions/{self.subscription_id}/signature_3.png',
        )

    def test_orphan_recovery_accepts_legacy_storage_prefix_when_listed(self):
        storage = FakeMigrationStorage(objects={
            f'storage/subscriptions/{self.subscription_id}/signature_1.png',
        }, location='')

        storage_key = recover_orphan_signature_key(self.subscription_id, storage=storage)

        self.assertEqual(
            storage_key,
            f'storage/subscriptions/{self.subscription_id}/signature_1.png',
        )

    def test_url_key_derivation_prefers_existing_relative_candidate(self):
        storage = FakeMigrationStorage(objects={
            f'subscriptions/{self.subscription_id}/signature_2.png',
        })
        url = f'https://cdn.example.com/storage/subscriptions/{self.subscription_id}/signature_2.png?token=ignored'

        storage_key = derive_existing_signature_storage_key(url, self.subscription_id, storage=storage)

        self.assertEqual(
            storage_key,
            f'subscriptions/{self.subscription_id}/signature_2.png',
        )

    def test_url_key_derivation_does_not_fabricate_missing_key(self):
        storage = FakeMigrationStorage()
        url = f'https://cdn.example.com/storage/subscriptions/{self.subscription_id}/signature_2.png'

        storage_key = derive_existing_signature_storage_key(url, self.subscription_id, storage=storage)

        self.assertIsNone(storage_key)

    def test_legacy_url_populates_blank_url_without_clearing_fallback(self):
        storage = FakeMigrationStorage()
        url = f'https://cdn.example.com/storage/subscriptions/{self.subscription_id}/signature_2.png'

        updates = process_legacy_signature_value(
            self.subscription_id,
            url,
            signature_url='',
            signature_storage_key='',
            storage=storage,
        )

        self.assertEqual(updates, {SIGNATURE_URL_COLUMN: url})

    def test_legacy_base64_uploads_and_populates_storage_key(self):
        storage = FakeMigrationStorage()
        payload = b'png-bytes'
        legacy_signature = 'data:image/png;base64,' + base64.b64encode(payload).decode('ascii')

        updates = process_legacy_signature_value(
            self.subscription_id,
            legacy_signature,
            signature_url=None,
            signature_storage_key=None,
            storage=storage,
            timestamp='123',
        )

        expected_key = f'subscriptions/{self.subscription_id}/signature_migrated_123.png'
        self.assertEqual(updates, {SIGNATURE_STORAGE_KEY_COLUMN: expected_key})
        self.assertEqual(storage.saved[0].data, payload)

    def test_legacy_base64_retry_reuses_deterministic_object(self):
        storage = FakeMigrationStorage()
        legacy_signature = base64.b64encode(b'png-bytes').decode('ascii')

        first = process_legacy_signature_value(
            self.subscription_id,
            legacy_signature,
            signature_url=None,
            signature_storage_key=None,
            storage=storage,
        )
        second = process_legacy_signature_value(
            self.subscription_id,
            legacy_signature,
            signature_url=None,
            signature_storage_key=None,
            storage=storage,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(storage.saved), 1)

    def test_invalid_nonempty_legacy_value_aborts(self):
        with self.assertRaises(SignatureMigrationError):
            process_legacy_signature_value(
                self.subscription_id,
                'not a url or base64 value',
                signature_url=None,
                signature_storage_key=None,
                storage=FakeMigrationStorage(),
            )

    def test_storage_error_aborts_legacy_base64(self):
        legacy_signature = base64.b64encode(b'png-bytes').decode('ascii')

        with self.assertRaises(SignatureMigrationError):
            process_legacy_signature_value(
                self.subscription_id,
                legacy_signature,
                signature_url=None,
                signature_storage_key=None,
                storage=FakeMigrationStorage(save_error=True),
            )

    def test_missing_reference_recovers_orphan_only_when_key_blank(self):
        storage = FakeMigrationStorage(objects={
            f'subscriptions/{self.subscription_id}/signature_9.png',
        })

        updates = recover_missing_signature_reference(
            self.subscription_id,
            signature_url='',
            signature_storage_key='',
            storage=storage,
        )

        self.assertEqual(updates, {
            SIGNATURE_STORAGE_KEY_COLUMN: f'subscriptions/{self.subscription_id}/signature_9.png',
        })
