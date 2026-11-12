"""Generate the Go form-fill data for the Piemonte Biella medical appointment form.

fill_pdf_fields (BE/docmanager/views/utils.py) without an inserted image is pypdf reading the template and
writing every page back with the form values: the output depends on the values only through the /V string
of each field. This script runs that code once with a marker in every field and writes

  BE_GO/internal/medicalform/piemonte_biella.skeleton   the output, each value replaced by QQF<index>QQ
  BE_GO/internal/medicalform/piemonte_biella.fields     the field keys, one per line, in index order
  BE_GO/internal/medicalform/pdfdoc.table               pypdf's PDFDocEncoding reverse table: '<code point> <byte>'
  BE_GO/internal/medicalform/testdata/golden.json       authored values with the SHA-256 of fill_pdf_fields' bytes

Run from the repository root with the production interpreter:
  python TESTS/fixtures/generate_medical_form_skeleton.py [--check]
--check compares the files with what the script would write and changes nothing.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'BE_GO/internal/medicalform'
TEMPLATE = 'templates/document/application/pdfs/piemonte-biella.pdf'
SIGNATURE = 'sport_association.president_signature'


def load():
    """fill_pdf_fields and get_pdf_fields as the view imports them, with the working directory of the server (BE)."""
    source = (ROOT / 'BE/docmanager/views/utils.py').read_text()
    code = source[source.index('from typing import'):source.index('class HasDocumentToken')]
    namespace = {}
    exec(compile(code, 'docmanager/views/utils.py', 'exec'), namespace)
    return namespace


def fill(namespace, values):
    with contextlib.redirect_stdout(io.StringIO()):
        return namespace['fill_pdf_fields'](pdf_path=TEMPLATE, replace_dict=values)


GOLDEN = [
    {},
    {'associate.first_name': '  MARIA', 'associate.last_name': '  ROSSI', 'associate.born_date': '09/05/2012',
     'other.today': '  10/11/2026', 'other.today_datetime': '  02/01/2026 03:04'},
    {'sport_association.denomination': "  A.S.D. (POLISPORTIVA) \\ D'AURORA", 'associate.address_no_number': '  VIA ROMA /',
     'main_tutor.email': '  TUTOR+ONE@EXAMPLE.TEST', 'sport_association.stamp': '  data:image/png;base64,AAAA'},
    {'associate.first_name': '  ÀÈÌÒÙ ÇÑ ŒŠŽ €', 'associate.last_name': '  ŁÓDŹ', 'associate.born_city': '  ΑΘΗΝΑ',
     'medical_appointment.sport': '  КАРАТЭ 空手 😀', 'other.address': '  LINE\nTWO\tTAB'},
    {'associate.tax_code': '', 'associate.id_number': ' ', 'associate.address_number': '  ' + 'X' * 300,
     SIGNATURE: '  IGNORED BY THE WRITER'},
]


def build():
    os.chdir(ROOT / 'BE')
    namespace = load()
    with contextlib.redirect_stdout(io.StringIO()):
        keys = [list(field)[0].replace('-', '.') for field in namespace['get_pdf_fields'](TEMPLATE)]
    assert len(keys) == len(set(keys)) and SIGNATURE in keys and all('\n' not in key for key in keys)
    marked = fill(namespace, {key: 'QQF%02dQQ' % index for index, key in enumerate(keys) if key != SIGNATURE})
    slots = {key: marked.count(b'(QQF%02dQQ)' % index) for index, key in enumerate(keys)}
    # The writer skips fields whose name contains 'signature' and fields that are not widgets of a page.
    assert slots[SIGNATURE] == 0 and max(slots.values()) == 1 and marked.count(b'QQF') == sum(slots.values()), slots
    assert fill(namespace, {}) == fill(namespace, {})
    from pypdf._codecs import _pdfdoc_encoding_rev
    table = ''.join('%d %d\n' % (ord(char), byte) for char, byte in sorted(_pdfdoc_encoding_rev.items()))
    golden = []
    for values in GOLDEN:
        data = fill(namespace, dict(values))
        golden.append({'values': values, 'length': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    return {'piemonte_biella.skeleton': marked, 'piemonte_biella.fields': ''.join(key + '\n' for key in keys).encode(),
            'pdfdoc.table': table.encode(),
            'testdata/golden.json': (json.dumps(golden, ensure_ascii=False, indent=1) + '\n').encode()}


def main():
    files = build()
    if sys.argv[1:] == ['--check']:
        stale = [name for name, data in files.items() if not (OUT / name).is_file() or (OUT / name).read_bytes() != data]
        if stale:
            sys.exit('stale: ' + ', '.join(stale))
        print('medical form data is current')
        return
    if sys.argv[1:]:
        sys.exit(__doc__)
    for name, data in files.items():
        (OUT / name).parent.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_bytes(data)
        print(name, len(data))


if __name__ == '__main__':
    main()
