from django.core import signing


DOWNLOAD_TOKEN_MAX_AGE = 60 * 60
DOWNLOAD_TOKEN_SALT = 'docmanager.association-export-download'
DOWNLOAD_TOKEN_PURPOSE = 'association-export'


def create_document_download_token(document_id, sport_association_id):
    return signing.dumps(
        {
            'document_id': str(document_id),
            'sport_association_id': str(sport_association_id),
            'purpose': DOWNLOAD_TOKEN_PURPOSE,
        },
        salt=DOWNLOAD_TOKEN_SALT,
    )


def load_document_download_token(token):
    try:
        payload = signing.loads(
            token,
            salt=DOWNLOAD_TOKEN_SALT,
            max_age=DOWNLOAD_TOKEN_MAX_AGE,
        )
    except signing.BadSignature:
        return None

    if not isinstance(payload, dict) or payload.get('purpose') != DOWNLOAD_TOKEN_PURPOSE:
        return None
    if not payload.get('document_id') or not payload.get('sport_association_id'):
        return None
    return payload


def is_valid_document_download_token(token, document_id, sport_association_id=None):
    payload = load_document_download_token(token)
    if payload is None or payload['document_id'] != str(document_id):
        return False
    return (
        sport_association_id is None
        or payload['sport_association_id'] == str(sport_association_id)
    )
