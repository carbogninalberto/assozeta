"""Contract settings for evaluate_manuale and the run_mcp_server it starts.

The disposable contract settings disable the manual; the evaluation harness
(TESTS/adapters/go/evaluate_manuale.py) gives both runtimes the same private
manual paths, revision, release and model endpoint through the environment.
"""
import os

from TESTS.adapters.django.settings import *  # noqa: F403

MANUAL_INDEX_PATH = os.environ['MANUAL_INDEX_PATH']
MANUAL_ASSET_ROOT = os.environ['MANUAL_ASSET_ROOT']
MANUAL_DEVELOPMENT_ROOT = os.environ['MANUAL_DEVELOPMENT_ROOT']
MANUAL_APPLICATION_REVISION = os.environ['MANUAL_APPLICATION_REVISION']
MANUAL_BUNDLE_PATH = os.environ['EVALUATE_MANUALE_ABSENT_BUNDLE']
MANUAL_BUNDLE_REQUIRED = False
RUNNING_VERSION = os.environ['RUNNING_VERSION']
AI_API_KEY = os.environ['AI_API_KEY']
AI_BASE_URL = os.environ['AI_BASE_URL']
