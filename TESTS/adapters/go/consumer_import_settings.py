"""Settings of the Django prefork worker the import harness starts: the consumer-restore worker settings (private broker,
production cache, channel layer and S3 storage) with Django's default PASSWORD_HASHERS, as core.settings has them.
The contract settings put the MD5 hasher first for cheap fixtures; the import hashes the owner's recovery password
with the default hasher, so the oracle must use production's."""
from django.conf.global_settings import PASSWORD_HASHERS as _DEFAULT_PASSWORD_HASHERS

from TESTS.adapters.go.consumer_restore_settings import *  # noqa: F401,F403

PASSWORD_HASHERS = list(_DEFAULT_PASSWORD_HASHERS)
