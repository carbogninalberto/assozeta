"""Run the authored staff-board document corpus through Django's real validator.

Offline component evidence only: no database, cache, HTTP or realtime work.
Django settings load from the self-host placeholder environment with an
ephemeral signing key, matching the bootstrap adapter. Run with the production
interpreter: ``python -m TESTS.adapters.django.staff_board_documents``.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / 'TESTS/fixtures/staff-board-documents.json'


def load_corpus(path=CORPUS):
    corpus = json.loads(Path(path).read_text())
    assert corpus['format'] == 1 and corpus['max_document_bytes'] == 14 * 1024 * 1024
    names = [case['name'] for case in corpus['cases']]
    assert len(names) == len(set(names)) and len(names) >= 100
    return corpus


def document_text(case):
    raw = case['document_json']
    if 'repeat' in case:
        raw = raw.replace('@@REPEAT@@', case['repeat']['unit'] * case['repeat']['count'])
    return raw


def outcome(validate, ValidationError, case):
    """Return the observed outcome in the corpus expectation vocabulary."""
    document = json.loads(document_text(case))
    try:
        cleaned, text = validate(document)
    except ValidationError as error:
        detail = error.detail
        assert isinstance(detail, list) and len(detail) == 1
        return {'error': str(detail[0])}
    except TypeError:
        return {'exception': 'TypeError'}
    return {'cleaned_json': json.dumps(cleaned), 'text_json': json.dumps(text)}


def expected(case):
    expect = case['expect']
    if 'cleaned' in expect:
        return {'cleaned_json': json.dumps(expect['cleaned']), 'text_json': json.dumps(expect['text'])}
    return expect


def setup_django(work):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import environ
    # Settings decode signing keys at import; generate a throwaway pair per run.
    key = Ed25519PrivateKey.generate()
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
        JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()).decode(),
        JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM,
                                                     serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
        DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings', TESTS_DATABASE='assozeta_contract_offline_documents',
        TESTS_WORKDIR=str(work), DBTYPE='POSTGRES', DBHOST=str(work), DBPORT='5432',
        DBNAME='assozeta_contract_offline_documents', USE_PGBOUNCER='False', STORE_ON_DISK='True')
    sys.path.insert(0, str(ROOT / 'BE'))
    import django
    with patch('environ.Env.read_env'):
        django.setup()
    from communications.staff_board import validate_document
    from rest_framework.serializers import ValidationError
    return validate_document, ValidationError


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    corpus = load_corpus()
    with tempfile.TemporaryDirectory(prefix='staff-board-documents-') as work:
        validate, ValidationError = setup_django(work)
        failures = [case['name'] for case in corpus['cases']
                    if outcome(validate, ValidationError, case) != expected(case)]
    print(json.dumps({'check': 'staff-board-documents.django', 'cases': len(corpus['cases']),
                      'failures': failures, 'success': not failures}))
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
