"""Golden archives of Python's zipfile for BE_GO/internal/pyzip.

Each case is what PrintingService.download_multiple_files writes: a
ZipFile(path, 'w', compression=ZIP_DEFLATED) on a seekable temporary file and
one writestr(name, data) per document, with time.localtime fixed so the DOS
times are known. Output: BE_GO/internal/pyzip/testdata/python_zip.json.
Run with the production Python (-I).
"""
import base64
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

CASES = [
    ('empty archive', []),
    ('one pdf', [('1_ricevuta.pdf', b'%PDF-1.4\n' + b'0123456789' * 300)]),
    ('gap after a skipped document, non-ASCII and empty entry',
     [('1_a.pdf', b'hello' * 10), ('3_ricevuta è.pdf', b''), ('4_dup.pdf', bytes(range(256)) * 9)]),
    ('directory-like name and duplicate names', [('1_x/', b'abc'), ('2_same.pdf', b'one'), ('2_same.pdf', b'two')]),
]
AT = (2026, 11, 9, 17, 32, 49)


def build(entries):
    real = time.localtime
    time.localtime = lambda *_: time.struct_time(AT + (0, 313, 0))
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp:
            with zipfile.ZipFile(temp.name, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries:
                    archive.writestr(name, data)
            data = temp.read()
        os.unlink(temp.name)
        return data
    finally:
        time.localtime = real


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    cases = [{'name': name, 'at': list(AT), 'entries': [{'name': n, 'data': base64.b64encode(d).decode()} for n, d in entries],
              'zip': base64.b64encode(build(entries)).decode()} for name, entries in CASES]
    out = ROOT / 'BE_GO/internal/pyzip/testdata/python_zip.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'python': sys.version.split()[0], 'cases': cases}, indent=1, ensure_ascii=False) + '\n')
    print(out)


if __name__ == '__main__':
    main()
