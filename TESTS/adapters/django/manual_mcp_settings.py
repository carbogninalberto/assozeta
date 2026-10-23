"""Owned stdio fixture settings; application command and transport stay real."""
import json
import os
from pathlib import Path
from unittest.mock import patch

# Prevent this child process from reading the developer's BE/.env. The parent
# supplies the private PostgreSQL settings and disposable signing keys.
with patch('environ.Env.read_env'):
    from TESTS.adapters.django.settings import *  # noqa: F403

path=Path(os.environ['TESTS_MANUAL_MCP_SETTINGS'])
if not path.parent.name.startswith('az-manual-http-'):
    raise RuntimeError('Private MCP fixture settings required')
values=json.loads(path.read_text())
if any(not key.startswith('MANUAL_') and key not in ('RUNNING_VERSION','ASSOZETA_DEPLOYMENT_MODE') for key in values):
    raise RuntimeError('Unexpected MCP fixture setting')
globals().update(values)
DATABASES['default'].setdefault('OPTIONS',{})['options']='-c application_name=manual-mcp-contract'  # noqa: F405
