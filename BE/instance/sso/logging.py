"""Suppress credential-bearing SSO requests in Django/Gunicorn/Uvicorn logs."""
import logging


class SSORequestFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if '/bakney/v1/' in message or '/instance/sso/' in message or '/instance/admin/bakney-pairing' in message:
            if record.name == 'uvicorn.access' and isinstance(record.args, tuple) and len(record.args) == 5:
                # Uvicorn's AccessFormatter unpacks this tuple even when the
                # message has already been formatted. Retain its required shape.
                client, method, _, version, status = record.args
                record.args = (client, method, '/bakney/v1/[redacted]', version, status)
            else:
                record.msg = 'Bakney SSO request (request details redacted)'
                record.args = ()
            # Django's error records also carry a request object whose repr
            # contains the full URL; custom formatters must not emit it.
            record.__dict__.pop('request', None)
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def install():
    for name in ('gunicorn.access', 'uvicorn.access', 'django.server', 'django.request'):
        logging.getLogger(name).addFilter(SSORequestFilter())
