"""
Export/Import Validators

This module contains validation utilities for the export/import system.
"""
import base64
import hashlib
import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from django.contrib.auth.hashers import identify_hasher, is_password_usable
from django.core.files.storage import default_storage
from django.core.serializers.json import DjangoJSONEncoder

logger = logging.getLogger(__name__)


def is_restorable_password_hash(encoded: Any) -> bool:
    """Return whether Django can authenticate against an archived password hash."""
    if not isinstance(encoded, str) or not encoded or not is_password_usable(encoded):
        return False

    try:
        hasher = identify_hasher(encoded)
        decoded = hasher.decode(encoded)
    except Exception:
        return False

    if not decoded.get('salt'):
        return False

    digest = decoded.get('hash', decoded.get('checksum'))
    if not digest:
        return False

    cost_limits = (
        ('iterations', 'iterations'),
        ('work_factor', 'work_factor' if hasher.algorithm == 'scrypt' else 'rounds'),
        ('block_size', 'block_size'),
        ('parallelism', 'parallelism'),
        ('time_cost', 'time_cost'),
        ('memory_cost', 'memory_cost'),
    )
    for decoded_key, hasher_attr in cost_limits:
        value = decoded.get(decoded_key)
        limit = getattr(hasher, hasher_attr, None)
        if value is not None and (value <= 0 or limit is None or value > limit):
            return False

    if hasher.algorithm.startswith('pbkdf2_'):
        expected_size = hasher.digest().digest_size
        try:
            return len(base64.b64decode(digest, validate=True)) == expected_size
        except ValueError:
            return False

    if hasher.algorithm == 'scrypt':
        work_factor = decoded['work_factor']
        if work_factor <= 1 or work_factor & (work_factor - 1):
            return False
        try:
            return len(base64.b64decode(digest, validate=True)) == 64
        except ValueError:
            return False

    if hasher.algorithm == 'argon2':
        try:
            padded_digest = digest + ('=' * (-len(digest) % 4))
            return len(base64.b64decode(padded_digest, validate=True)) == decoded['params'].hash_len
        except (KeyError, ValueError):
            return False

    if hasher.algorithm in ('bcrypt', 'bcrypt_sha256'):
        bcrypt_chars = './ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        salt = decoded.get('salt', '')
        checksum = decoded.get('checksum', '')
        return (
            decoded.get('algostr') in ('2a', '2b', '2y')
            and decoded.get('work_factor', 0) >= 4
            and len(salt) == 22
            and len(checksum) == 31
            and all(char in bcrypt_chars for char in salt + checksum)
        )

    return True


@dataclass
class ValidationResult:
    """Result of a validation operation."""
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: Dict[str, Any] = field(default_factory=dict)

    def add_error(self, error: str):
        """Add an error and mark as invalid."""
        self.is_valid = False
        self.errors.append(error)

    def add_warning(self, warning: str):
        """Add a warning (doesn't affect validity)."""
        self.warnings.append(warning)

    def merge(self, other: 'ValidationResult'):
        """Merge another validation result into this one."""
        if not other.is_valid:
            self.is_valid = False
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.info.update(other.info)


class ExportValidator:
    """Validates export data before creating ZIP."""

    @staticmethod
    def validate_model_data(model_name: str, records: List[Dict]) -> ValidationResult:
        """
        Validate that all records are JSON-serializable.

        Args:
            model_name: Name of the model being validated
            records: List of record dictionaries

        Returns:
            ValidationResult
        """
        result = ValidationResult()
        result.info['model'] = model_name
        result.info['record_count'] = len(records)

        for idx, record in enumerate(records):
            try:
                json.dumps(record, cls=DjangoJSONEncoder)
            except (TypeError, ValueError) as e:
                result.add_error(f"{model_name}[{idx}]: Failed to serialize - {e}")

        return result

    @staticmethod
    def validate_file_exists(filepath: str) -> ValidationResult:
        """
        Validate that a file exists in storage.

        Args:
            filepath: Path to check in storage

        Returns:
            ValidationResult
        """
        result = ValidationResult()

        if not filepath:
            result.add_warning("Empty filepath provided")
            return result

        if not default_storage.exists(filepath):
            result.add_error(f"File not found in storage: {filepath}")

        return result

    @staticmethod
    def validate_association_exists(sport_association_id: str) -> ValidationResult:
        """
        Validate that an association exists.

        Args:
            sport_association_id: UUID of the association

        Returns:
            ValidationResult
        """
        from application.models.user_models import SportAssociation

        result = ValidationResult()

        try:
            SportAssociation.original_objects.get(
                sport_association_id=sport_association_id
            )
        except SportAssociation.DoesNotExist:
            result.add_error(f"Association not found: {sport_association_id}")

        return result


class ImportValidator:
    """Validates import data before applying."""

    REQUIRED_MANIFEST_FIELDS = [
        'version',
        'export_format',
        'export_date',
        'association',
    ]

    SUPPORTED_FORMATS = ['bakney_sport_export_v1']

    def __init__(self, zip_path: str):
        """
        Initialize the validator.

        Args:
            zip_path: Path to the export ZIP file
        """
        self.zip_path = zip_path
        self.manifest: Optional[Dict] = None
        self.source_association: Optional[Dict] = None
        self.source_owner: Optional[Dict] = None

    def validate_file(self) -> ValidationResult:
        """
        Validate the export file exists and is a valid ZIP.

        Returns:
            ValidationResult
        """
        result = ValidationResult()

        if not os.path.exists(self.zip_path):
            result.add_error(f"File not found: {self.zip_path}")
            return result

        if not zipfile.is_zipfile(self.zip_path):
            result.add_error(f"Not a valid ZIP file: {self.zip_path}")
            return result

        # Check file size
        file_size = os.path.getsize(self.zip_path)
        result.info['file_size_bytes'] = file_size
        result.info['file_size_mb'] = round(file_size / (1024 * 1024), 2)

        return result

    def validate_manifest(self) -> ValidationResult:
        """
        Validate the manifest file structure and contents.

        Returns:
            ValidationResult
        """
        result = ValidationResult()

        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zf:
                if 'manifest.json' not in zf.namelist():
                    result.add_error("Missing manifest.json in export file")
                    return result

                manifest_content = zf.read('manifest.json')
                self.manifest = json.loads(manifest_content)

        except json.JSONDecodeError as e:
            result.add_error(f"Invalid JSON in manifest: {e}")
            return result
        except Exception as e:
            result.add_error(f"Error reading manifest: {e}")
            return result

        # Check required fields
        for field in self.REQUIRED_MANIFEST_FIELDS:
            if field not in self.manifest:
                result.add_error(f"Missing required manifest field: {field}")

        # Check export format
        export_format = self.manifest.get('export_format')
        if export_format not in self.SUPPORTED_FORMATS:
            result.add_error(f"Unsupported export format: {export_format}")

        # Store manifest info
        result.info['export_format'] = export_format
        result.info['export_date'] = self.manifest.get('export_date')
        result.info['association'] = self.manifest.get('association', {})

        return result

    def validate_data_files(self) -> ValidationResult:
        """
        Validate that expected data files exist in the ZIP.

        Returns:
            ValidationResult
        """
        result = ValidationResult()

        if not self.manifest:
            result.add_error("Manifest not loaded - call validate_manifest first")
            return result

        expected_files = []
        models_exported = self.manifest.get('models_exported', [])

        for model_info in models_exported:
            filename = model_info.get('file')
            if filename:
                # Use path as-is if it includes directory, otherwise assume data/
                if '/' in filename:
                    expected_files.append(filename)
                else:
                    expected_files.append(f"data/{filename}")

        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zf:
                namelist = zf.namelist()

                missing = []
                for expected in expected_files:
                    if expected not in namelist:
                        missing.append(expected)

                if missing:
                    for m in missing:
                        result.add_warning(f"Missing data file: {m}")

                result.info['expected_files'] = len(expected_files)
                result.info['missing_files'] = len(missing)

        except Exception as e:
            result.add_error(f"Error reading ZIP contents: {e}")

        return result

    def validate_owner_identity(self) -> ValidationResult:
        """
        Validate and expose the archived owner identity.

        The owner is selected exclusively from SportAssociation.user_id and the
        matching User.user_id.  Roles are not used for owner discovery.
        """
        result = ValidationResult()

        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zf:
                try:
                    associations = json.loads(zf.read('data/01_sport_association.json'))
                except KeyError:
                    result.add_error("Missing required data file: 01_sport_association.json")
                    return result

                if not isinstance(associations, list) or len(associations) != 1:
                    result.add_error(
                        "Export must contain exactly one SportAssociation record in "
                        "data/01_sport_association.json"
                    )
                    return result

                association = associations[0]
                association_id = association.get('sport_association_id')
                if not association_id:
                    result.add_error("SportAssociation record is missing sport_association_id")
                    return result

                owner_user_id = association.get('user_id')
                if not owner_user_id:
                    result.add_error("SportAssociation record is missing user_id for owner selection")
                    return result

                try:
                    users = json.loads(zf.read('data/02_users.json'))
                except KeyError:
                    result.add_error("Missing required data file: 02_users.json")
                    return result

                owner_matches = [
                    user_data for user_data in users
                    if str(user_data.get('user_id')) == str(owner_user_id)
                ]
                if len(owner_matches) != 1:
                    result.add_error(
                        f"Expected exactly one User with user_id {owner_user_id} "
                        "matching SportAssociation.user_id"
                    )
                    return result

                owner = owner_matches[0]
                self.source_association = association
                self.source_owner = owner
                result.info['association'] = {
                    **result.info.get('association', {}),
                    'sport_association_id': association.get('sport_association_id'),
                    'denomination': association.get('denomination'),
                    'tax_code': association.get('tax_code'),
                }
                result.info['owner_user'] = {
                    'user_id': owner.get('user_id'),
                    'username': owner.get('username'),
                    'email': owner.get('email'),
                    'requires_recovery_password': not is_restorable_password_hash(
                        owner.get('password')
                    ),
                }

        except json.JSONDecodeError as e:
            result.add_error(f"Invalid JSON in data files: {e}")
        except Exception as e:
            result.add_error(f"Error reading owner identity: {e}")

        return result

    def validate_uuid_conflicts(self, preserve_uuids: bool = True) -> ValidationResult:
        """
        Check for UUID conflicts with existing data.

        Args:
            preserve_uuids: Ignored stale option; source UUIDs are always preserved

        Returns:
            ValidationResult
        """
        from application.models.user_models import SportAssociation, User

        result = ValidationResult()

        if not self.source_association or not self.source_owner:
            result.add_error("Owner identity not loaded")
            return result

        # Check association UUID
        assoc_id = self.source_association.get('sport_association_id')

        if assoc_id:
            if SportAssociation.original_objects.filter(
                sport_association_id=assoc_id
            ).exists():
                result.add_error(
                    f"Association UUID conflict: {assoc_id} already exists"
                )

        owner_id = self.source_owner.get('user_id')
        if owner_id:
            existing_owner = User.original_objects.filter(user_id=owner_id).first()
            if existing_owner:
                if is_restorable_password_hash(existing_owner.password):
                    result.info['existing_owner_has_restorable_password'] = True
                conflicting_association = SportAssociation.original_objects.filter(
                    user=existing_owner
                ).exclude(sport_association_id=assoc_id).first()
                if conflicting_association:
                    result.add_error(
                        "Owner user already owns another SportAssociation: "
                        f"{conflicting_association.sport_association_id}"
                    )

        return result

    def validate_all(
        self,
        owner_email: str = '',
        preserve_uuids: bool = True
    ) -> ValidationResult:
        """
        Run all validations.

        Args:
            owner_email: Ignored stale option; archived owner email is retained
            preserve_uuids: Ignored stale option; source UUIDs are always preserved

        Returns:
            Combined ValidationResult
        """
        result = ValidationResult()

        # File validation
        file_result = self.validate_file()
        result.merge(file_result)
        if not file_result.is_valid:
            return result

        # Manifest validation
        manifest_result = self.validate_manifest()
        result.merge(manifest_result)
        if not manifest_result.is_valid:
            return result

        # Data files validation
        data_result = self.validate_data_files()
        result.merge(data_result)

        # Archived owner identity
        identity_result = self.validate_owner_identity()
        result.merge(identity_result)

        # UUID conflicts
        if identity_result.is_valid:
            uuid_result = self.validate_uuid_conflicts(preserve_uuids)
            result.merge(uuid_result)
            if result.info.pop('existing_owner_has_restorable_password', False):
                result.info['owner_user']['requires_recovery_password'] = False

        return result


def validate_export_file(zip_path: str) -> ValidationResult:
    """
    Convenience function to validate an export file.

    Args:
        zip_path: Path to the export ZIP file

    Returns:
        ValidationResult
    """
    validator = ImportValidator(zip_path)

    result = validator.validate_file()
    if not result.is_valid:
        return result

    manifest_result = validator.validate_manifest()
    result.merge(manifest_result)
    if not manifest_result.is_valid:
        return result

    data_result = validator.validate_data_files()
    result.merge(data_result)

    return result


def validate_import(
    zip_path: str,
    owner_email: str = '',
    preserve_uuids: bool = True
) -> ValidationResult:
    """
    Convenience function to validate an import operation.

    Args:
        zip_path: Path to the export ZIP file
        owner_email: Ignored stale option; archived owner email is retained
        preserve_uuids: Ignored stale option; source UUIDs are always preserved

    Returns:
        ValidationResult
    """
    validator = ImportValidator(zip_path)
    return validator.validate_all(owner_email, preserve_uuids)
