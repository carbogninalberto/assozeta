from rest_framework.exceptions import APIException


class InvalidSignatureError(APIException):
    status_code = 400
    default_detail = {
        'msg': 'Firma non valida.',
        'code': '400',
        'ref': 'invalid_signature',
    }
    default_code = 'invalid_signature'


class DuplicateSubscriptionError(APIException):
    status_code = 409
    default_detail = {
        'msg': 'Questa iscrizione esiste già.',
        'code': '409',
        'ref': 'subscription_duplicate',
    }
    default_code = 'duplicate_subscription'


class StorageUnavailableError(APIException):
    status_code = 503
    default_detail = {
        'msg': 'Storage temporaneamente non disponibile.',
        'code': '503',
        'ref': 'signature_storage',
    }
    default_code = 'storage_unavailable'
