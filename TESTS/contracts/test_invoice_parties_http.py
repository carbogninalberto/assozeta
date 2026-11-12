"""Independently authored customer and supplier invoice list, add and update HTTP expectations; no Django imports."""
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/invoice_parties_http.json'
# Function views: @api_view([...]) lists exactly its methods plus OPTIONS.
ALLOWED = {'list': {'GET', 'OPTIONS'}, 'add': {'POST', 'OPTIONS'}, 'update': {'PATCH', 'OPTIONS'}}


def load_source(path=SOURCE):
    return family.load_source(path)


def regenerated_xml(case, expected, actual):
    """A listed row whose authored xml is '$xml' was regenerated during the request: its body carries a FatturaPA document
    for that row (the runner checks the stored XML); any other value must match exactly."""
    rows = expected.get('data') if isinstance(expected, dict) else None
    listed = actual.get('data') if isinstance(actual, dict) else None
    if not isinstance(rows, list) or not isinstance(listed, list) or len(rows) != len(listed):
        return expected, actual
    data = []
    for row, got in zip(rows, listed):
        if isinstance(row, dict) and row.get('xml') == '$xml' and isinstance(got, dict) and isinstance(got.get('xml'), str) \
                and got.get('customer_invoice_id') == row['customer_invoice_id'] and '<ProgressivoInvio>' in got['xml']:
            row = {**row, 'xml': got['xml']}
        data.append(row)
    return {**expected, 'data': data}, actual


def assert_case(case, base_url, *, token, sessions):
    return family.assert_case(case, base_url, token=token, sessions=sessions, normalize=regenerated_xml,
                              allowed=lambda case: ALLOWED[case['path'].split('?')[0].split('/')[-1]])
