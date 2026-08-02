"""
Export/Import Validators

This module contains validation utilities for the export/import system.
"""
import hashlib
import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from django.core.files.storage import default_storage
from django.core.serializers.json import DjangoJSONEncoder

logger = logging.getLogger(__name__)


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

    def validate_uuid_conflicts(self, preserve_uuids: bool = False) -> ValidationResult:
        """
        Check for UUID conflicts with existing data.

        Args:
            preserve_uuids: Whether UUIDs will be preserved during import

        Returns:
            ValidationResult
        """
        from application.models.user_models import SportAssociation, User

        result = ValidationResult()

        if not preserve_uuids:
            # No conflicts possible if generating new UUIDs
            return result

        if not self.manifest:
            result.add_error("Manifest not loaded")
            return result

        # Check association UUID
        assoc_info = self.manifest.get('association', {})
        assoc_id = assoc_info.get('sport_association_id')

        if assoc_id:
            if SportAssociation.original_objects.filter(
                sport_association_id=assoc_id
            ).exists():
                result.add_error(
                    f"Association UUID conflict: {assoc_id} already exists"
                )

        return result

    def validate_owner_email(self, owner_email: str) -> ValidationResult:
        """
        Validate that the owner email is available.

        Args:
            owner_email: Email for the new owner

        Returns:
            ValidationResult
        """
        from application.models.user_models import User

        result = ValidationResult()

        if User.objects.filter(email=owner_email).exists():
            result.add_error(f"User with email '{owner_email}' already exists")

        if User.objects.filter(username=owner_email).exists():
            result.add_error(f"User with username '{owner_email}' already exists")

        return result

    def validate_all(
        self,
        owner_email: str,
        preserve_uuids: bool = False
    ) -> ValidationResult:
        """
        Run all validations.

        Args:
            owner_email: Email for the new owner
            preserve_uuids: Whether to preserve original UUIDs

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

        # UUID conflicts
        uuid_result = self.validate_uuid_conflicts(preserve_uuids)
        result.merge(uuid_result)

        # Owner email
        email_result = self.validate_owner_email(owner_email)
        result.merge(email_result)

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
    owner_email: str,
    preserve_uuids: bool = False
) -> ValidationResult:
    """
    Convenience function to validate an import operation.

    Args:
        zip_path: Path to the export ZIP file
        owner_email: Email for the new owner
        preserve_uuids: Whether to preserve original UUIDs

    Returns:
        ValidationResult
    """
    validator = ImportValidator(zip_path)
    return validator.validate_all(owner_email, preserve_uuids)
