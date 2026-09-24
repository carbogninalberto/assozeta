"""Scoped browser downloads using the same streaming path as normal exports."""
from django.core import signing
from django.core.files.storage import default_storage
from django.http import StreamingHttpResponse
from django.utils.http import content_disposition_header
from application.utils.printing import PrintingService, _AsyncStreamingBody

SALT = 'instance.data-restore.recovery-download'
MAX_AGE = 60 * 60


def download_token(op):
    return signing.dumps({'operation': str(op.id), 'owner': str(op.owner_id),
                          'sha256': op.backup_sha256}, salt=SALT)


def valid_download_token(token, op):
    if not isinstance(token, str):
        return False
    try:
        return signing.loads(token, salt=SALT, max_age=MAX_AGE) == {
            'operation': str(op.id), 'owner': str(op.owner_id), 'sha256': op.backup_sha256,
        }
    except (signing.BadSignature, TypeError):
        return False


def stream_backup(op):
    filename = f'backup-prima-del-ripristino-{op.id}.zip'
    if hasattr(default_storage, 'bucket_name'):
        # S3Storage.open() spools the complete object before returning bytes.
        # Normal exports instead read the S3 response body incrementally.
        return PrintingService()._stream_file_response(op.backup_path, filename, as_attachment=True)
    size = default_storage.size(op.backup_path)
    response = StreamingHttpResponse(_AsyncStreamingBody(default_storage.open(op.backup_path, 'rb')),
                                     content_type='application/zip')
    response['Content-Length'] = str(size)
    response['Content-Disposition'] = content_disposition_header(True, filename)
    response.disable_gzip = True
    return response
