"""Independently authored testimonial add HTTP expectations; no Django imports."""
import json
from pathlib import Path

from TESTS.contracts.test_email_logs_http import require
from TESTS.contracts.test_workflow_writes_http import assert_case  # noqa: F401  (shared add/OPTIONS response rules)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/testimonials_http.json'


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 30, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source
