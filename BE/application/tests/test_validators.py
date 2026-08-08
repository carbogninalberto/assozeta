"""
Tests for validators service - export/import validation utilities.

Port from SaaS test_validators.py adapted for self-host.
"""
import json
import os
import tempfile
import zipfile
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase

from application.services.validators import (
    ValidationResult,
    ExportValidator,
    ImportValidator,
    validate_export_file,
    validate_import,
)


class ValidationResultTests(TestCase):
    """Tests for ValidationResult dataclass."""

    def test_default_values(self):
        """Test default ValidationResult is valid with empty lists."""
        result = ValidationResult()
        self.assertTrue(result.is_valid)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.info, {})

    def test_add_error_marks_invalid(self):
        """Test adding error marks result as invalid."""
        result = ValidationResult()
        result.add_error("Test error")
        self.assertFalse(result.is_valid)
        self.assertEqual(result.errors, ["Test error"])

    def test_add_multiple_errors(self):
        """Test adding multiple errors."""
        result = ValidationResult()
        result.add_error("Error 1")
        result.add_error("Error 2")
        self.assertFalse(result.is_valid)
        self.assertEqual(len(result.errors), 2)

    def test_add_warning_keeps_valid(self):
        """Test adding warning doesn't affect validity."""
        result = ValidationResult()
        result.add_warning("Test warning")
        self.assertTrue(result.is_valid)
        self.assertEqual(result.warnings, ["Test warning"])

    def test_merge_valid_results(self):
        """Test merging two valid results stays valid."""
        result1 = ValidationResult()
        result1.info['key1'] = 'value1'

        result2 = ValidationResult()
        result2.info['key2'] = 'value2'

        result1.merge(result2)

        self.assertTrue(result1.is_valid)
        self.assertEqual(result1.info['key1'], 'value1')
        self.assertEqual(result1.info['key2'], 'value2')

    def test_merge_invalid_result_marks_invalid(self):
        """Test merging invalid result marks combined as invalid."""
        result1 = ValidationResult()
        result2 = ValidationResult()
        result2.add_error("Error from result2")

        result1.merge(result2)

        self.assertFalse(result1.is_valid)
        self.assertIn("Error from result2", result1.errors)

    def test_merge_combines_errors_and_warnings(self):
        """Test merge combines all errors and warnings."""
        result1 = ValidationResult()
        result1.add_error("Error 1")
        result1.add_warning("Warning 1")

        result2 = ValidationResult()
        result2.add_error("Error 2")
        result2.add_warning("Warning 2")

        result1.merge(result2)

        self.assertEqual(len(result1.errors), 2)
        self.assertEqual(len(result1.warnings), 2)


class ExportValidatorTests(TestCase):
    """Tests for ExportValidator class."""

    def test_validate_model_data_success(self):
        """Test validation of serializable data succeeds."""
        records = [
            {'id': 1, 'name': 'Test'},
            {'id': 2, 'name': 'Test 2'},
        ]
        result = ExportValidator.validate_model_data('TestModel', records)

        self.assertTrue(result.is_valid)
        self.assertEqual(result.info['model'], 'TestModel')
        self.assertEqual(result.info['record_count'], 2)

    def test_validate_model_data_empty_list(self):
        """Test validation of empty records list."""
        result = ExportValidator.validate_model_data('EmptyModel', [])

        self.assertTrue(result.is_valid)
        self.assertEqual(result.info['record_count'], 0)

    def test_validate_model_data_with_decimal(self):
        """Test validation handles Decimal types via DjangoJSONEncoder."""
        records = [{'amount': Decimal('100.50')}]
        result = ExportValidator.validate_model_data('Payment', records)

        self.assertTrue(result.is_valid)

    def test_validate_model_data_non_serializable(self):
        """Test validation fails for non-serializable objects."""
        class NonSerializable:
            pass

        records = [{'obj': NonSerializable()}]
        result = ExportValidator.validate_model_data('BadModel', records)

        self.assertFalse(result.is_valid)
        self.assertTrue(any('Failed to serialize' in e for e in result.errors))

    @patch('application.services.validators.default_storage')
    def test_validate_file_exists_success(self, mock_storage):
        """Test file exists validation succeeds when file exists."""
        mock_storage.exists.return_value = True

        result = ExportValidator.validate_file_exists('path/to/file.pdf')

        self.assertTrue(result.is_valid)
        mock_storage.exists.assert_called_once_with('path/to/file.pdf')

    @patch('application.services.validators.default_storage')
    def test_validate_file_exists_missing(self, mock_storage):
        """Test file exists validation fails when file missing."""
        mock_storage.exists.return_value = False

        result = ExportValidator.validate_file_exists('missing/file.pdf')

        self.assertFalse(result.is_valid)
        self.assertTrue(any('File not found' in e for e in result.errors))

    def test_validate_file_exists_empty_path(self):
        """Test file exists validation handles empty path."""
        result = ExportValidator.validate_file_exists('')

        self.assertTrue(result.is_valid)
        self.assertTrue(any('Empty filepath' in w for w in result.warnings))

    @patch('application.models.user_models.SportAssociation')
    def test_validate_association_exists_success(self, mock_sport_association):
        """Test association validation succeeds when association exists."""
        mock_sport_association.original_objects.get.return_value = MagicMock()

        result = ExportValidator.validate_association_exists('some-uuid-12345')

        self.assertTrue(result.is_valid)

    def test_validate_association_exists_missing(self):
        """Test association validation fails when association missing."""
        import uuid
        fake_id = str(uuid.uuid4())

        result = ExportValidator.validate_association_exists(fake_id)

        self.assertFalse(result.is_valid)
        self.assertTrue(any('Association not found' in e for e in result.errors))


class ImportValidatorTests(TestCase):
    """Tests for ImportValidator class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.valid_zip_path = os.path.join(self.temp_dir, 'test_export.zip')
        self.invalid_zip_path = os.path.join(self.temp_dir, 'not_a_zip.txt')
        self.missing_path = os.path.join(self.temp_dir, 'missing.zip')

        self.create_valid_zip()

        with open(self.invalid_zip_path, 'w') as f:
            f.write('This is not a ZIP file')

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_valid_zip(self, manifest=None):
        """Create a valid test ZIP file with manifest."""
        if manifest is None:
            manifest = {
                'version': '1.0',
                'export_format': 'bakney_sport_export_v1',
                'export_date': '2024-01-15',
                'association': {
                    'sport_association_id': 'test-uuid',
                    'name': 'Test Association'
                },
                'models_exported': [
                    {'model': 'Subscription', 'file': 'subscriptions.json'}
                ]
            }

        with zipfile.ZipFile(self.valid_zip_path, 'w') as zf:
            zf.writestr('manifest.json', json.dumps(manifest))
            zf.writestr('data/subscriptions.json', '[]')
            zf.writestr('data/01_sport_association.json', json.dumps([{
                'sport_association_id': 'test-uuid',
                'user_id': 'owner-uuid-12345',
                'denomination': 'Test Association',
                'tax_code': '12345678901',
            }]))
            zf.writestr('data/02_users.json', json.dumps([{
                'user_id': 'owner-uuid-12345',
                'username': 'testowner',
                'email': 'owner@test.com',
                'password': 'md5$salt$6f8db599de986fab7a21625b7916589c',
            }]))

    def test_validate_file_success(self):
        """Test file validation succeeds for valid ZIP."""
        validator = ImportValidator(self.valid_zip_path)
        result = validator.validate_file()

        self.assertTrue(result.is_valid)
        self.assertIn('file_size_bytes', result.info)
        self.assertIn('file_size_mb', result.info)

    def test_validate_file_not_found(self):
        """Test file validation fails when file missing."""
        validator = ImportValidator(self.missing_path)
        result = validator.validate_file()

        self.assertFalse(result.is_valid)
        self.assertTrue(any('File not found' in e for e in result.errors))

    def test_validate_file_not_zip(self):
        """Test file validation fails for non-ZIP file."""
        validator = ImportValidator(self.invalid_zip_path)
        result = validator.validate_file()

        self.assertFalse(result.is_valid)
        self.assertTrue(any('Not a valid ZIP' in e for e in result.errors))

    def test_validate_manifest_success(self):
        """Test manifest validation succeeds for valid manifest."""
        validator = ImportValidator(self.valid_zip_path)
        result = validator.validate_manifest()

        self.assertTrue(result.is_valid)
        self.assertEqual(result.info['export_format'], 'bakney_sport_export_v1')
        self.assertIsNotNone(validator.manifest)

    def test_validate_manifest_missing(self):
        """Test manifest validation fails when manifest missing."""
        no_manifest_path = os.path.join(self.temp_dir, 'no_manifest.zip')
        with zipfile.ZipFile(no_manifest_path, 'w') as zf:
            zf.writestr('data/test.json', '[]')

        validator = ImportValidator(no_manifest_path)
        result = validator.validate_manifest()

        self.assertFalse(result.is_valid)
        self.assertTrue(any('Missing manifest.json' in e for e in result.errors))

    def test_validate_manifest_invalid_json(self):
        """Test manifest validation fails for invalid JSON."""
        bad_json_path = os.path.join(self.temp_dir, 'bad_json.zip')
        with zipfile.ZipFile(bad_json_path, 'w') as zf:
            zf.writestr('manifest.json', 'not valid json{')

        validator = ImportValidator(bad_json_path)
        result = validator.validate_manifest()

        self.assertFalse(result.is_valid)
        self.assertTrue(any('Invalid JSON' in e for e in result.errors))

    def test_validate_manifest_missing_required_field(self):
        """Test manifest validation fails when required field missing."""
        incomplete_path = os.path.join(self.temp_dir, 'incomplete.zip')
        with zipfile.ZipFile(incomplete_path, 'w') as zf:
            manifest = {
                'export_format': 'bakney_sport_export_v1',
                'export_date': '2024-01-15',
                'association': {}
            }
            zf.writestr('manifest.json', json.dumps(manifest))

        validator = ImportValidator(incomplete_path)
        result = validator.validate_manifest()

        self.assertFalse(result.is_valid)
        self.assertTrue(any('Missing required' in e for e in result.errors))

    def test_validate_manifest_unsupported_format(self):
        """Test manifest validation fails for unsupported format."""
        bad_format_path = os.path.join(self.temp_dir, 'bad_format.zip')
        with zipfile.ZipFile(bad_format_path, 'w') as zf:
            manifest = {
                'version': '1.0',
                'export_format': 'unsupported_format',
                'export_date': '2024-01-15',
                'association': {}
            }
            zf.writestr('manifest.json', json.dumps(manifest))

        validator = ImportValidator(bad_format_path)
        result = validator.validate_manifest()

        self.assertFalse(result.is_valid)
        self.assertTrue(any('Unsupported export format' in e for e in result.errors))

    def test_validate_data_files_success(self):
        """Test data files validation succeeds when files present."""
        validator = ImportValidator(self.valid_zip_path)
        validator.validate_manifest()
        result = validator.validate_data_files()

        self.assertTrue(result.is_valid)
        self.assertEqual(result.info['expected_files'], 1)
        self.assertEqual(result.info['missing_files'], 0)

    def test_validate_data_files_missing_manifest(self):
        """Test data files validation fails without manifest."""
        validator = ImportValidator(self.valid_zip_path)
        result = validator.validate_data_files()

        self.assertFalse(result.is_valid)
        self.assertTrue(any('Manifest not loaded' in e for e in result.errors))

    def test_validate_data_files_missing_data_file(self):
        """Test data files validation warns about missing files."""
        missing_data_path = os.path.join(self.temp_dir, 'missing_data.zip')
        with zipfile.ZipFile(missing_data_path, 'w') as zf:
            manifest = {
                'version': '1.0',
                'export_format': 'bakney_sport_export_v1',
                'export_date': '2024-01-15',
                'association': {},
                'models_exported': [
                    {'model': 'Subscription', 'file': 'subscriptions.json'}
                ]
            }
            zf.writestr('manifest.json', json.dumps(manifest))

        validator = ImportValidator(missing_data_path)
        validator.validate_manifest()
        result = validator.validate_data_files()

        self.assertTrue(result.is_valid)
        self.assertEqual(result.info['missing_files'], 1)
        self.assertTrue(any('Missing data file' in w for w in result.warnings))

    def test_validate_uuid_conflicts_no_manifest_loaded(self):
        """Test UUID conflict check fails when identity not loaded."""
        validator = ImportValidator(self.valid_zip_path)
        result = validator.validate_uuid_conflicts()

        self.assertFalse(result.is_valid)

    def test_validate_owner_identity_success(self):
        """Test owner identity validation succeeds for valid ZIP."""
        validator = ImportValidator(self.valid_zip_path)
        result = validator.validate_owner_identity()

        self.assertTrue(result.is_valid)
        self.assertIn('owner_user', result.info)
        self.assertEqual(result.info['owner_user']['email'], 'owner@test.com')

    def test_validate_owner_identity_requires_recovery_password(self):
        """Test owner identity marks requires_recovery_password when hash invalid."""
        recovery_path = os.path.join(self.temp_dir, 'recovery.zip')
        with zipfile.ZipFile(recovery_path, 'w') as zf:
            manifest = {
                'version': '1.0',
                'export_format': 'bakney_sport_export_v1',
                'export_date': '2024-01-15',
                'association': {},
                'models_exported': []
            }
            zf.writestr('manifest.json', json.dumps(manifest))
            zf.writestr('data/01_sport_association.json', json.dumps([{
                'sport_association_id': 'test-uuid',
                'user_id': 'owner-uuid-12345',
            }]))
            zf.writestr('data/02_users.json', json.dumps([{
                'user_id': 'owner-uuid-12345',
                'username': 'testowner',
                'email': 'owner@test.com',
                'password': '',
            }]))

        validator = ImportValidator(recovery_path)
        result = validator.validate_owner_identity()

        self.assertTrue(result.is_valid)
        self.assertTrue(result.info['owner_user']['requires_recovery_password'])

    @patch('application.models.user_models.SportAssociation')
    def test_validate_uuid_conflicts_with_conflict(self, mock_sport_association):
        """Test UUID conflict check fails when association exists."""
        import uuid as uuid_mod
        conflict_uuid = str(uuid_mod.uuid4())
        owner_uuid = str(uuid_mod.uuid4())

        mock_sport_association.original_objects.filter.return_value.exists.return_value = True

        conflict_path = os.path.join(self.temp_dir, 'conflict.zip')
        with zipfile.ZipFile(conflict_path, 'w') as zf:
            manifest = {
                'version': '1.0',
                'export_format': 'bakney_sport_export_v1',
                'export_date': '2024-01-15',
                'association': {
                    'sport_association_id': conflict_uuid
                }
            }
            zf.writestr('manifest.json', json.dumps(manifest))
            zf.writestr('data/01_sport_association.json', json.dumps([{
                'sport_association_id': conflict_uuid,
                'user_id': owner_uuid,
            }]))
            zf.writestr('data/02_users.json', json.dumps([{
                'user_id': owner_uuid,
                'username': 'testowner',
                'email': 'owner@test.com',
                'password': 'md5$salt$6f8db599de986fab7a21625b7916589c',
            }]))

        validator = ImportValidator(conflict_path)
        validator.validate_manifest()
        validator.validate_owner_identity()
        result = validator.validate_uuid_conflicts()

        self.assertFalse(result.is_valid)
        self.assertTrue(any('UUID conflict' in e for e in result.errors))

    def test_validate_all_stops_on_file_error(self):
        """Test full validation stops early on file error."""
        validator = ImportValidator(self.missing_path)
        result = validator.validate_all()

        self.assertFalse(result.is_valid)
        self.assertTrue(any('File not found' in e for e in result.errors))


class ValidatorConvenienceFunctionsTests(TestCase):
    """Tests for convenience functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.valid_zip_path = os.path.join(self.temp_dir, 'test.zip')

        with zipfile.ZipFile(self.valid_zip_path, 'w') as zf:
            manifest = {
                'version': '1.0',
                'export_format': 'bakney_sport_export_v1',
                'export_date': '2024-01-15',
                'association': {},
                'models_exported': []
            }
            zf.writestr('manifest.json', json.dumps(manifest))

    def tearDown(self):
        """Clean up temp files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_validate_export_file_success(self):
        """Test validate_export_file convenience function."""
        result = validate_export_file(self.valid_zip_path)
        self.assertTrue(result.is_valid)

    def test_validate_export_file_missing(self):
        """Test validate_export_file with missing file."""
        result = validate_export_file('/nonexistent/path.zip')
        self.assertFalse(result.is_valid)

    def test_validate_import_success(self):
        """Test validate_import convenience function."""
        import uuid as uuid_mod
        import_conflict_uuid = str(uuid_mod.uuid4())
        import_owner_uuid = str(uuid_mod.uuid4())

        import_path = os.path.join(self.temp_dir, 'import_test.zip')
        with zipfile.ZipFile(import_path, 'w') as zf:
            manifest = {
                'version': '1.0',
                'export_format': 'bakney_sport_export_v1',
                'export_date': '2024-01-15',
                'association': {
                    'sport_association_id': import_conflict_uuid
                },
                'models_exported': []
            }
            zf.writestr('manifest.json', json.dumps(manifest))
            zf.writestr('data/01_sport_association.json', json.dumps([{
                'sport_association_id': import_conflict_uuid,
                'user_id': import_owner_uuid,
            }]))
            zf.writestr('data/02_users.json', json.dumps([{
                'user_id': import_owner_uuid,
                'username': 'testowner',
                'email': 'owner@test.com',
                'password': 'md5$salt$6f8db599de986fab7a21625b7916589c',
            }]))

        result = validate_import(import_path)
        self.assertTrue(result.is_valid)
