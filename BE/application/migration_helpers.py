import base64
import binascii
import hashlib
import posixpath
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import models


LEGACY_SIGNATURE_COLUMN = 'signature'
SIGNATURE_STORAGE_KEY_COLUMN = 'signature_storage_key'
SIGNATURE_URL_COLUMN = 'signature_url'
SUBSCRIPTION_PK_COLUMN = 'subscription_id'
SUBSCRIPTION_STATUS_COLUMN = 'status_flag'
LEGACY_AWS_LOCATION = 'storage'
SIGNED_SUBSCRIPTION_STATUSES = {2, 3, 4, 5}


class SignatureMigrationError(RuntimeError):
    pass


def _is_blank(value):
    return value is None or str(value).strip() == ''


def _is_url(value):
    cleaned = str(value).strip().lower()
    return cleaned.startswith('http://') or cleaned.startswith('https://')


def _unique(values):
    seen = set()
    result = []
    for value in values:
        cleaned = str(value or '').strip().strip('/')
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _storage_locations(storage):
    return _unique([
        getattr(storage, 'location', ''),
        getattr(settings, 'AWS_LOCATION', ''),
        LEGACY_AWS_LOCATION,
    ])


def _subscription_prefix(subscription_id):
    return posixpath.join('subscriptions', str(subscription_id))


def _storage_key_exists(storage, key, subscription_id):
    if _is_blank(key):
        return False

    try:
        return bool(storage.exists(str(key).strip('/')))
    except Exception as exc:
        raise SignatureMigrationError(
            f'Unable to inspect signature object storage for subscription {subscription_id}'
        ) from exc


def _candidate_is_for_subscription(candidate, subscription_id, storage):
    if _is_blank(subscription_id):
        return True

    cleaned = str(candidate or '').strip('/')
    expected = f'{_subscription_prefix(subscription_id)}/'
    if cleaned.startswith(expected):
        return True

    for location in _storage_locations(storage):
        if cleaned.startswith(f'{location}/{expected}'):
            return True

    return False


def _url_path_candidates(signature_url, subscription_id, storage):
    parsed = urlparse(str(signature_url).strip())
    path = unquote(parsed.path or '').strip('/')
    if not path:
        return []

    raw_paths = [path]
    bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')
    if bucket_name and path.startswith(f'{bucket_name}/'):
        raw_paths.append(path[len(bucket_name) + 1:])

    candidates = []
    for raw_path in raw_paths:
        parts = raw_path.split('/')
        for index, part in enumerate(parts):
            if part != 'subscriptions':
                continue
            if subscription_id and len(parts) > index + 1 and parts[index + 1] != str(subscription_id):
                continue
            candidates.append('/'.join(parts[index:]))

        for location in _storage_locations(storage):
            if raw_path.startswith(f'{location}/'):
                candidates.append(raw_path[len(location) + 1:])

        candidates.append(raw_path)

    return [
        candidate
        for candidate in _unique(candidates)
        if _candidate_is_for_subscription(candidate, subscription_id, storage)
    ]


def derive_existing_signature_storage_key(signature_url, subscription_id, storage=None):
    """
    Derive a storage key from a legacy/public URL only when that exact candidate
    exists in the currently configured storage. The URL itself remains the
    fallback and is never modified by this helper.
    """
    if _is_blank(signature_url):
        return None

    storage = storage or default_storage
    for candidate in _url_path_candidates(signature_url, subscription_id, storage):
        if _storage_key_exists(storage, candidate, subscription_id):
            return candidate

    return None


def _orphan_directories(subscription_id, storage):
    prefix = _subscription_prefix(subscription_id)
    directories = [prefix]
    directories.extend(posixpath.join(location, prefix) for location in _storage_locations(storage))
    return _unique(directories)


def _join_listed_file(directory, filename):
    directory = str(directory or '').strip('/')
    filename = str(filename or '').strip('/')
    if not directory:
        return filename
    if filename == directory or filename.startswith(f'{directory}/'):
        return filename
    return posixpath.join(directory, filename)


def _is_signature_png(path):
    basename = posixpath.basename(str(path or ''))
    return basename.startswith('signature_') and basename.endswith('.png')


def recover_orphan_signature_key(subscription_id, storage=None):
    """
    Recover old uploaded signature objects that were left without DB pointers.
    Only paths returned by storage.listdir() and confirmed by storage.exists()
    are considered; no key is fabricated from a UUID alone.
    """
    storage = storage or default_storage
    candidates = []

    for directory in _orphan_directories(subscription_id, storage):
        try:
            _, files = storage.listdir(directory)
        except FileNotFoundError:
            continue
        except Exception as exc:
            raise SignatureMigrationError(
                f'Unable to list signature object storage for subscription {subscription_id}'
            ) from exc

        for filename in files:
            candidate = _join_listed_file(directory, filename)
            if not _is_signature_png(candidate):
                continue
            if _storage_key_exists(storage, candidate, subscription_id):
                candidates.append(candidate)

    if not candidates:
        return None
    return sorted(set(candidates))[-1]


def _decode_legacy_base64_signature(legacy_signature, subscription_id):
    value = str(legacy_signature).strip()
    encoded = value.split(',', 1)[1] if ',' in value else value
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureMigrationError(
            f'Legacy signature for subscription {subscription_id} is neither a URL nor valid base64'
        ) from exc

    if not decoded:
        raise SignatureMigrationError(
            f'Legacy signature for subscription {subscription_id} decoded to empty data'
        )

    return decoded


def _migration_storage_path(subscription_id, decoded_signature, timestamp=None):
    if timestamp is None:
        suffix = hashlib.sha256(decoded_signature).hexdigest()[:20]
    else:
        suffix = str(timestamp).replace('/', '_')
    return posixpath.join(
        'subscriptions',
        str(subscription_id),
        f'signature_migrated_{suffix}.png',
    )


def process_legacy_signature_value(
    subscription_id,
    legacy_signature,
    signature_url,
    signature_storage_key,
    storage=None,
    timestamp=None,
):
    """
    Return DB field updates for one non-state legacy signature column value.

    Nonempty values must be recoverable before the migration is allowed to drop
    the physical legacy column. URL values preserve URL fallback. Base64 values
    are uploaded through Django's configured default storage.
    """
    if _is_blank(legacy_signature):
        return {}

    storage = storage or default_storage
    legacy_signature = str(legacy_signature).strip()
    updates = {}

    if _is_url(legacy_signature):
        fallback_url = signature_url
        if _is_blank(signature_url):
            updates[SIGNATURE_URL_COLUMN] = legacy_signature
            fallback_url = legacy_signature
        if _is_blank(signature_storage_key):
            storage_key = derive_existing_signature_storage_key(
                fallback_url,
                subscription_id,
                storage=storage,
            )
            if storage_key:
                updates[SIGNATURE_STORAGE_KEY_COLUMN] = storage_key
        return updates

    decoded_signature = _decode_legacy_base64_signature(legacy_signature, subscription_id)
    if not _is_blank(signature_storage_key) and _storage_key_exists(
        storage,
        signature_storage_key,
        subscription_id,
    ):
        return updates

    migration_path = _migration_storage_path(
        subscription_id,
        decoded_signature,
        timestamp=timestamp,
    )
    if _storage_key_exists(storage, migration_path, subscription_id):
        saved_path = migration_path
    else:
        try:
            saved_path = storage.save(
                migration_path,
                ContentFile(decoded_signature),
            )
        except Exception as exc:
            raise SignatureMigrationError(
                f'Unable to store migrated legacy signature for subscription {subscription_id}'
            ) from exc

    if _is_blank(saved_path):
        raise SignatureMigrationError(
            f'Storage returned an empty key for subscription {subscription_id}'
        )

    updates[SIGNATURE_STORAGE_KEY_COLUMN] = str(saved_path).strip('/')
    return updates


def recover_missing_signature_reference(
    subscription_id,
    signature_url,
    signature_storage_key,
    storage=None,
):
    if not _is_blank(signature_storage_key):
        return {}

    storage = storage or default_storage
    if not _is_blank(signature_url):
        storage_key = derive_existing_signature_storage_key(
            signature_url,
            subscription_id,
            storage=storage,
        )
    else:
        storage_key = recover_orphan_signature_key(subscription_id, storage=storage)

    if storage_key:
        return {SIGNATURE_STORAGE_KEY_COLUMN: storage_key}
    return {}


def _table_columns(connection, table_name):
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, table_name)
    return {column.name for column in description}


def _signature_rows(connection, table_name, has_legacy_column):
    selected_columns = [
        SUBSCRIPTION_PK_COLUMN,
        SUBSCRIPTION_STATUS_COLUMN,
        SIGNATURE_URL_COLUMN,
        SIGNATURE_STORAGE_KEY_COLUMN,
    ]
    if has_legacy_column:
        selected_columns.append(LEGACY_SIGNATURE_COLUMN)

    quote_name = connection.ops.quote_name
    quoted_columns = ', '.join(quote_name(column) for column in selected_columns)
    quoted_table = quote_name(table_name)
    quoted_pk = quote_name(SUBSCRIPTION_PK_COLUMN)

    with connection.cursor() as cursor:
        cursor.execute(f'SELECT {quoted_columns} FROM {quoted_table} ORDER BY {quoted_pk}')
        column_names = [column[0] for column in cursor.description]
        return [dict(zip(column_names, row)) for row in cursor.fetchall()]


def _apply_signature_updates(connection, table_name, subscription_id, updates):
    allowed_updates = {
        column: updates[column]
        for column in (SIGNATURE_URL_COLUMN, SIGNATURE_STORAGE_KEY_COLUMN)
        if column in updates
    }
    if not allowed_updates:
        return

    quote_name = connection.ops.quote_name
    assignments = ', '.join(f'{quote_name(column)} = %s' for column in allowed_updates)
    parameters = list(allowed_updates.values()) + [subscription_id]

    with connection.cursor() as cursor:
        cursor.execute(
            f'UPDATE {quote_name(table_name)} SET {assignments} '
            f'WHERE {quote_name(SUBSCRIPTION_PK_COLUMN)} = %s',
            parameters,
        )


def _drop_legacy_signature_column(apps, schema_editor):
    Subscription = apps.get_model('application', 'Subscription')
    table_name = Subscription._meta.db_table
    if LEGACY_SIGNATURE_COLUMN not in _table_columns(schema_editor.connection, table_name):
        return

    legacy_field = models.TextField(null=True)
    legacy_field.set_attributes_from_name(LEGACY_SIGNATURE_COLUMN)
    legacy_field.model = Subscription
    schema_editor.remove_field(Subscription, legacy_field)


def restore_legacy_signature_column(apps, schema_editor):
    Subscription = apps.get_model('application', 'Subscription')
    table_name = Subscription._meta.db_table
    if LEGACY_SIGNATURE_COLUMN in _table_columns(schema_editor.connection, table_name):
        return

    legacy_field = models.TextField(null=True)
    legacy_field.set_attributes_from_name(LEGACY_SIGNATURE_COLUMN)
    legacy_field.model = Subscription
    schema_editor.add_field(Subscription, legacy_field)


def migrate_subscription_signature_compatibility(apps, schema_editor):
    Subscription = apps.get_model('application', 'Subscription')
    connection = schema_editor.connection
    table_name = Subscription._meta.db_table
    columns = _table_columns(connection, table_name)
    expected_columns = {
        SUBSCRIPTION_PK_COLUMN,
        SUBSCRIPTION_STATUS_COLUMN,
        SIGNATURE_URL_COLUMN,
        SIGNATURE_STORAGE_KEY_COLUMN,
    }
    missing_columns = expected_columns - columns
    if missing_columns:
        missing = ', '.join(sorted(missing_columns))
        raise SignatureMigrationError(f'Missing subscription columns required for signature migration: {missing}')

    has_legacy_column = LEGACY_SIGNATURE_COLUMN in columns
    created_storage_keys = []
    try:
        for row in _signature_rows(connection, table_name, has_legacy_column):
            subscription_id = row[SUBSCRIPTION_PK_COLUMN]
            effective_url = row.get(SIGNATURE_URL_COLUMN)
            effective_key = row.get(SIGNATURE_STORAGE_KEY_COLUMN)
            updates = {}

            if has_legacy_column:
                legacy_value = row.get(LEGACY_SIGNATURE_COLUMN)
                legacy_updates = process_legacy_signature_value(
                    subscription_id,
                    legacy_value,
                    effective_url,
                    effective_key,
                )
                updates.update(legacy_updates)
                effective_url = updates.get(SIGNATURE_URL_COLUMN, effective_url)
                effective_key = updates.get(SIGNATURE_STORAGE_KEY_COLUMN, effective_key)
                if (
                    not _is_blank(legacy_value)
                    and not _is_url(legacy_value)
                    and SIGNATURE_STORAGE_KEY_COLUMN in legacy_updates
                ):
                    created_storage_keys.append(legacy_updates[SIGNATURE_STORAGE_KEY_COLUMN])

            should_recover_orphan = row.get(SUBSCRIPTION_STATUS_COLUMN) in SIGNED_SUBSCRIPTION_STATUSES
            if not _is_blank(effective_url) or should_recover_orphan:
                recovery_updates = recover_missing_signature_reference(
                    subscription_id,
                    effective_url,
                    effective_key,
                )
                updates.update(recovery_updates)
            _apply_signature_updates(connection, table_name, subscription_id, updates)

        if has_legacy_column:
            _drop_legacy_signature_column(apps, schema_editor)
    except Exception:
        for storage_key in created_storage_keys:
            try:
                default_storage.delete(storage_key)
            except Exception:
                pass
        raise
