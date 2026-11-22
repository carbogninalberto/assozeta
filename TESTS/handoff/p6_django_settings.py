"""Django settings of the Phase 6 schema driver: the contract adapter settings with real migrations.

TESTS.adapters.django.settings (isolated contract database, no providers); the database lives in
the private cluster named by DBHOST (a Unix socket directory). Migrations are NOT disabled here:
Phase 6 compares what the shipped migration paths build.
"""
from TESTS.adapters.django.settings import *  # noqa: F401,F403
