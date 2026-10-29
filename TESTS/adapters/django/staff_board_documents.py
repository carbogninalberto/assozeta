"""Run the authored staff-board document and input corpora through real Django code.

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
INPUTS = ROOT / 'TESTS/fixtures/staff-board-inputs.json'
RESPONSES = ROOT / 'TESTS/fixtures/staff-board-responses.json'


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


def input_outcome(case):
    """Validate one authored request body with the real input serializer, in English."""
    from django.http import QueryDict
    from django.utils import translation
    from communications.serializers import StaffBoardMessageInputSerializer
    if 'form' in case:
        data = QueryDict(mutable=True)
        for key, value in case['form']:
            data.appendlist(key, value)
    else:
        data = json.loads(case['body_json'])
    serializer = StaffBoardMessageInputSerializer(data=data, partial=case['partial'])
    with translation.override('en'):
        if serializer.is_valid():
            return {'valid': json.loads(json.dumps(serializer.validated_data))}
        return {'errors': json.loads(json.dumps(serializer.errors))}


def response_outcome(case):
    """Project unsaved model instances through the real response serializer."""
    from datetime import datetime
    from types import SimpleNamespace
    from uuid import UUID
    from rest_framework.utils.encoders import JSONEncoder
    from application.models import SportAssociation, User
    from communications.models import StaffBoardMessage
    from communications.serializers import StaffBoardMessageSerializer
    record = case['record']
    association = SportAssociation(sport_association_id=UUID(record['association']['id']),
        user_id=UUID(record['association']['owner_id']), denomination=record['association']['denomination'])
    author = None
    if record['author']:
        values = record['author']
        author = User(user_id=UUID(values['id']), first_name=values['first_name'], last_name=values['last_name'],
                      username=values['username'], avatar_image=values['avatar_image'])
    message = StaffBoardMessage(staff_board_message_id=UUID(record['id']), sport_association=association, author=author,
        content=record['content'], document=record['document'], pinned=record['pinned'],
        created_at=datetime.fromisoformat(record['created_at']), updated_at=datetime.fromisoformat(record['updated_at']))
    request = SimpleNamespace(user=SimpleNamespace(pk=UUID(case['actor_id'])), collaborator=False)
    data = StaffBoardMessageSerializer(message, context={'request': request}).data
    return json.loads(json.dumps(data, cls=JSONEncoder))


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    corpus = load_corpus()
    inputs = json.loads(INPUTS.read_text())
    assert inputs['format'] == 1 and len({case['name'] for case in inputs['cases']}) == len(inputs['cases'])
    with tempfile.TemporaryDirectory(prefix='staff-board-documents-') as work:
        validate, ValidationError = setup_django(work)
        failures = [case['name'] for case in corpus['cases']
                    if outcome(validate, ValidationError, case) != expected(case)]
        input_failures = [case['name'] for case in inputs['cases'] if input_outcome(case) != case['expect']]
        responses = json.loads(RESPONSES.read_text())['cases']
        response_failures = [case['name'] for case in responses if response_outcome(case) != case['expect']]
    print(json.dumps({'check': 'staff-board-documents.django', 'cases': len(corpus['cases']), 'failures': failures,
                      'input_cases': len(inputs['cases']), 'input_failures': input_failures,
                      'response_cases': len(responses), 'response_failures': response_failures,
                      'success': not failures and not input_failures and not response_failures}))
    return 0 if not failures and not input_failures and not response_failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
