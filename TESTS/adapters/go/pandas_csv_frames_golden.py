"""Capture pandas.read_csv(file, delimiter=';', dtype={column: str, ...}) frames for BE_GO/internal/pandascsv.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/pandas_csv_frames_golden.py``
Each input is parsed exactly as get_pandas_df_from_file does for a '.csv'
document and the case's text columns (``texts``; none for an upload, the
mapped text fields' columns for the final step), once per kind of file object
the storage hands to pandas:

* ``raw``: a django-storages ``S3File`` (the production storage). It has no
  ``mode`` attribute, so pandas takes it for a text handle and the C parser
  receives the undecoded bytes of ``read(262144)``.
* ``text``: a binary handle with a ``mode`` (``FileSystemStorage``,
  ``BytesIO``), which pandas wraps in a strict UTF-8 ``TextIOWrapper``.
  Recorded only where its outcome differs from ``raw``.

The outcome is the exception class, or the columns, the dtype of every column
and, for every row of ``DataFrame.iterrows()``, a tag for each value's Python
type followed by ``str(value)``: ``s`` str, ``i`` int, ``f`` float, ``b`` bool,
``I`` numpy.int64, ``U`` numpy.uint64, ``F`` numpy.float64, ``B`` numpy.bool.
Frames of more than 4000 cells record the row count and a SHA-256 of the same
cells instead. An input pandas does not finish within the time limit (a bare
carriage return can make the tokenizer re-read a line until its row limit) is
recorded as ``timeout``.

Inputs: authored values for every inference rule alone, in pairs and in
frames of mixed columns, the inputs of ``pandas_headers.json``, seeded random
typed documents, seeded random byte sequences (carriage returns, NUL, invalid
UTF-8), seeded random float and integer texts, and documents larger than the
parser's row chunk (2**20 // width rows) and its 262144-byte read buffer.
The text-column cases repeat the authored values and frames with their columns
read as text, and add repeated, unnamed and missing column names (a repeated
name hands its dtype to the name pandas gives the repeat), seeded random typed
documents and byte sequences with random text columns, and chunked documents.
Written to ``BE_GO/internal/pandascsv/testdata/pandas_frames.json.gz``;
``go test ./internal/pandascsv`` requires the same outcome for every input the
package accepts.
"""
import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import random
import select
import struct
import subprocess
import sys
import tempfile
import threading
import warnings

ROOT = Path(__file__).resolve().parents[3]
HEADERS = ROOT / 'BE_GO/internal/pandascsv/testdata/pandas_headers.json'
OUTPUT = ROOT / 'BE_GO/internal/pandascsv/testdata/pandas_frames.json.gz'
WORKERS = 4
DIGEST_CELLS = 4000

NA = [b'', b'#N/A', b'#N/A N/A', b'#NA', b'-1.#IND', b'-1.#QNAN', b'-NaN', b'-nan', b'1.#IND', b'1.#QNAN', b'<NA>', b'N/A', b'NA',
      b'NULL', b'NaN', b'None', b'n/a', b'nan', b'null']
NEAR_NA = [b' NA', b'NA ', b'na', b'Null', b'NONE', b'none', b'<na>', b'N/a', b'NAN', b'Nan', b'+nan', b'+NaN', b'-', b'#N/A#', b'1.#INF',
           b'-1.#INF', b'""', b'" "', b' ', b'\t', b'"NA"', b'"nan"']
INTS = [b'0', b'1', b'-1', b'+5', b'007', b'-0', b'+0', b' 12', b' 12 ', b'12 ', b'\t3', b'3\t', b'9223372036854775807',
        b'9223372036854775808', b'-9223372036854775808', b'-9223372036854775809', b'-9223372036854775807', b'18446744073709551615',
        b'18446744073709551616', b'+9223372036854775808', b'1 2', b'1_000', b'1,000', b'0x10', '१२'.encode(), '１２'.encode(),
        b'1' + b'0' * 30, b'-1' + b'0' * 30, b'00000000000000000000001', b'9007199254740993', b'-9007199254740993', b'--1', b'+-1', b'-+1',
        b'+', b'-', b'1-', b'1+1', b'"5"', b'"-7"', b'42', b'1000000', b'-abc', b'12\x0b', b'\x0c12', b'1\xc2\xa0']
FLOATS = [b'1.5', b'.5', b'5.', b'.', b'-.5', b'+1.5', b'1e5', b'1E5', b'1e', b'1e+', b'1e-', b'1e-5', b'1.e3', b'1e400', b'-1e400', b'1e308',
          b'1e309', b'1.7976931348623157e308', b'1.7976931348623159e308', b'17976931348623157e292', b'17976931348623159e292', b'2e308',
          b'1e-400', b'4.9e-324', b'5e-324', b'2.4703282292062327e-324', b'2.5e-324', b'1e-320', b'1e-308', b'2.2250738585072014e-308',
          b'2.2250738585072011e-308', b'1e-616', b'1e-617', b'1e-650', b'123e-630', b'0.1', b'0.2', b'0.3', b'1.1', b'2.675', b'123456789012345678',
          b'1234567890123456789012.5', b'0.00000000000000000000001234', b'0.000001234', b'1.0000000000000002', b'9007199254740993.0',
          b'9007199254740992.5', b'1e15', b'1e16', b'1e17', b'1e22', b'1e23', b'123456789012345.6', b'1234567890123456.7', b'0.0001', b'0.00001',
          b'-0.0', b'0.0', b'0e0', b'-0e5', b'1d5', b'1.5f', b'1,5', b'1.2.3', b' 1.5 ', b'1.5\t', b'1.5 x', b'1e5.5', b'1e99999999', b'1e-99999999',
          b'1e2147483648', b'1e4294967296', b'1e-4294967297', b'1e00000000000000005', b'0x1p3', b'1e+05', b'1E-05', b'1e 5', b'1 e5', b'e5', b'.e5',
          b'5.e', b'3.14159', b'100.0', b'1.0', b'1.50', b'99999999999999999999.0', b'0.30000000000000004', b'1' + b'0' * 400,
          b'0.' + b'0' * 400 + b'1', b'1' * 400 + b'e-400', b'1e308' + b'0' * 5, b'179769313486231580793e288', b'12345678901234567e-17']
INFS = [b'inf', b'-inf', b'+inf', b'Inf', b'INF', b'Infinity', b'-Infinity', b'+Infinity', b'infinity', b'INFINITY', b'iNf', b'-iNFiNiTy',
        b'infinit', b'in', b' inf', b'inf ', b'--inf', b'+-inf', b'infi', b'1inf', b'inf1']
BOOLS = [b'True', b'TRUE', b'true', b'False', b'FALSE', b'false', b'tRuE', b'fAlse', b'T', b'F', b'yes', b'no', b' True', b'True ', b't',
         b'truee', b'"True"', b'TRUE\x00x', b'Tru', b'FALS']
TEXTS = [b'abc', b' abc ', b'\tabc', 'è'.encode(), 'Città di Castello'.encode(), b'RSSMRA80A01H501U', b'01/01/1980', b'2024-01-31',
         b'mario@example.test', b'"a;b"', b'"a""b"', b'"a\nb"', b'a"b', b'"a"b', b'x\x00y', b'\x00', b'\x00x', b'a\x00', b'\xff', b'a\xffb',
         b'\xc3', b'\xed\xa0\x80', b'\xf4\x90\x80\x80', b'\xc0\x80', '﻿'.encode(), ' '.encode(), '\xa0x'.encode(), b'#', b'\\N',
         b"'", b"'1'", b'=1', b'1%', b'$1', b'1/2', b'12:30', b'+39 333 1234567', b'0039333', b'20100']
TOKENS = NA + NEAR_NA + INTS + FLOATS + INFS + BOOLS + TEXTS
PAIR_TOKENS = [b'""', b'NA', b'nan', b'#N/A', b'null', b' ', b'-', b'0', b'1', b'-1', b'+5', b'007', b' 12 ', b'9223372036854775807',
               b'9223372036854775808', b'-9223372036854775808', b'-9223372036854775809', b'18446744073709551615', b'18446744073709551616',
               b'1' + b'0' * 30, b'-abc', b'1 2', b'9007199254740993', b'1.5', b'.5', b'5.', b'.', b'1e5', b'1e', b'1e400', b'1e-400', b'-0.0',
               b'0.1', b'123456789012345678', b'9007199254740993.0', b'1e16', b'1e22', b'0.0001', b'0.00001', b' 1.5 ', b'1,5', b'inf', b'-inf',
               b'+Infinity', b'iNf', b'infinit', b'True', b'TRUE', b'true', b'False', b'false', b'tRuE', b'T', b' True', b'abc', b' abc ',
               'è'.encode(), b'"a;b"', b'x\x00y', b'\x00', b'TRUE\x00x', b'1\x00x', b'NA\x00x', b'\xff', b'1\xff', b'2024-01-31', b'01/01/1980',
               b'RSSMRA80A01H501U']
ARCHETYPES = {
    'int': [b'1', b'-2', b'30', b'007', b'+4', b' 5 ', b'0'],
    'float': [b'1.5', b'-0.25', b'1e3', b'.5', b'2.', b'1e16', b'0.1', b'inf', b'-Infinity'],
    'bool': [b'True', b'false', b'TRUE', b'False', b'tRuE'],
    'text': [b'abc', b'Mario', b'RSSMRA80A01H501U', b'01/01/1980', b' x ', 'è'.encode(), b'1a', b'True1'],
    'uint': [b'9223372036854775808', b'18446744073709551615', b'1', b'0'],
    'big': [b'18446744073709551616', b'-9223372036854775809', b'1', b'-1'],
    'na': [b'', b'NA', b'nan', b'N/A', b'NULL', b'""'],
    'date': [b'2024-01-31', b'31/01/2024', b'1980-05-17 00:00:00', b'17.05.1980'],
    'code': [b'20100', b'00100', b'06012', b'3331234567', b'+393331234567'],
}
FRAME_COLUMNS = [[b'1', b'2', b'3'], [b'1', b'', b'3'], [b'1.5', b'2', b'-0.0'], [b'True', b'false', b'TRUE'], [b'True', b'', b'False'],
                 [b'a', b'', b'1'], [b'', b'NA', b'nan'], [b'9223372036854775808', b'1', b'2'], [b'18446744073709551616', b'1', b''],
                 [b'inf', b'1', b''], [b'9223372036854775808', b'-1', b'2'], [b'9223372036854775807', b'-9223372036854775808', b'9007199254740993']]
HEADER_NAMES = [b'Nome', b'Cognome', b'Codice Fiscale', b'Data di nascita', b'Email', b'a', b'a', b'b', b'', b'a.1', b'Unnamed: 0', b' x ',
                'Città'.encode(), b'1', b'NA', b'True', b'1.5']
SOUP = [b';', b';', b';', b'"', b'\n', b'\n', b'\n', b'\r\n', b'\r', b' ', b'\t', b'a', b'1', b'2.5', b'True', b'NA', b'', b'\x00', b'\xff',
        b'\xc3\xa0', b'\xc3', b'-', b'e5', b'.', b'x', b'9223372036854775808', b'inf']


def worker():
    import numpy
    import pandas
    from storages.backends.s3 import S3File

    tags = {str: 's', int: 'i', float: 'f', bool: 'b', numpy.int64: 'I', numpy.uint64: 'U', numpy.float64: 'F', numpy.bool_: 'B'}

    def raw_handle(data):
        # An S3File as S3Storage.open() leaves it after the download: read()
        # serves the spooled bytes; the object has no 'mode'.
        handle = S3File.__new__(S3File)
        handle._mode = 'rb'
        handle._file = tempfile.SpooledTemporaryFile()
        handle._file.write(data)
        handle._file.seek(0)
        handle._closed = False
        assert not hasattr(handle, 'mode')
        return handle

    def cell(value):
        kind = type(value)
        if kind is str:
            return 's' + value
        return tags[kind] + str(value)

    def outcome(handle, texts):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                frame = pandas.read_csv(handle, delimiter=';', dtype={column: str for column in texts})
        except pandas.errors.EmptyDataError:
            return {'error': 'empty'}
        except pandas.errors.ParserError:
            return {'error': 'parser'}
        except UnicodeDecodeError:
            return {'error': 'decode'}
        except Exception as error:
            return {'error': 'other:' + type(error).__name__}
        columns = frame.columns.tolist()
        assert all(type(column) is str for column in columns)
        result = {'columns': columns, 'dtypes': [str(frame[column].dtype) for column in columns]}
        if len(frame) <= 2000:
            rows = [[cell(row[column]) for column in columns] for _, row in frame.iterrows()]
        else:
            # iterrows() builds each row from DataFrame.values; checked on the first rows.
            values = frame.values
            head = [[cell(row[column]) for column in columns] for _, row in frame.head(300).iterrows()]
            assert head == [[cell(item) for item in line] for line in values[:300]]
            rows = ([cell(item) for item in line] for line in values)
        if len(frame) * len(columns) <= DIGEST_CELLS:
            result['rows'] = list(rows)
            return result
        digest = hashlib.sha256()
        for line in rows:
            for item in line:
                digest.update(item.encode())
                digest.update(b'\x1f')
            digest.update(b'\x1e')
        result['count'] = len(frame)
        result['digest'] = digest.hexdigest()
        return result

    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        head = stdin.read(4)
        if len(head) < 4:
            return
        data = stdin.read(struct.unpack('>I', head)[0])
        texts = json.loads(stdin.read(struct.unpack('>I', stdin.read(4))[0]))
        result = {'raw': outcome(raw_handle(data), texts)}
        text = outcome(io.BytesIO(data), texts)
        if text != result['raw']:
            result['text'] = text
        payload = json.dumps(result).encode()
        stdout.write(struct.pack('>I', len(payload)) + payload)
        stdout.flush()


class Worker:
    def __init__(self):
        self.process = None

    def start(self):
        self.process = subprocess.Popen([sys.executable, '-I', __file__, '--worker'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, count, timeout):
        chunks = b''
        while len(chunks) < count:
            ready, _, _ = select.select([self.process.stdout], [], [], timeout)
            if not ready:
                return None
            chunk = self.process.stdout.read1(count - len(chunks))
            if not chunk:
                raise RuntimeError('worker exited')
            chunks += chunk
        return chunks

    def run(self, data, texts):
        if self.process is None:
            self.start()
        timeout = 8 if len(data) < 100000 else 300
        names = json.dumps(texts).encode()
        self.process.stdin.write(struct.pack('>I', len(data)) + data + struct.pack('>I', len(names)) + names)
        self.process.stdin.flush()
        head = self.read(4, timeout)
        body = self.read(struct.unpack('>I', head)[0], timeout) if head is not None else None
        if body is None:
            self.process.kill()
            self.process.wait()
            self.process = None
            return {'raw': {'error': 'timeout'}}
        return json.loads(body)

    def stop(self):
        if self.process is not None:
            self.process.stdin.close()
            self.process.wait()


def quoted(generator, cell):
    if generator.random() < 0.12 and b'"' not in cell and b'\x00' not in cell:
        return b'"' + cell + b'"'
    return cell


def typed_document(generator):
    width = generator.randint(1, 5)
    kinds = [generator.choice(list(ARCHETYPES)) for _ in range(width)]
    lines = [[generator.choice(HEADER_NAMES) for _ in range(width + generator.choice([0, 0, 0, 0, -1, 1]))]]
    for _ in range(generator.choice([0, 1, 1, 2, 3, 3, 4, 6, 10])):
        line = []
        for kind in kinds:
            draw = generator.random()
            if draw < 0.18:
                line.append(generator.choice(ARCHETYPES['na']))
            elif draw < 0.23:
                line.append(generator.choice(TOKENS))
            else:
                line.append(quoted(generator, generator.choice(ARCHETYPES[kind])))
        draw = generator.random()
        if draw < 0.08:
            line = line[:generator.randint(1, len(line))]
        elif draw < 0.14:
            line.append(generator.choice([b'', b'1', b'x']))
        lines.append(line)
    end = generator.choice([b'\n'] * 14 + [b'\r\n'] * 5 + [b'\r'])
    parts = []
    for line in lines:
        if not line:
            line = [b'h']
        if generator.random() < 0.04:
            parts.append(generator.choice([b'', b' ', b' \t', b'\t']))
        parts.append(b';'.join(line))
    data = end.join(parts) + generator.choice([b'', end, end])
    if generator.random() < 0.05:
        data = b'\xef\xbb\xbf' + data
    return data


def float_text(generator, digits):
    whole = generator.randint(0, digits)
    text = ''.join(generator.choice('0123456789') for _ in range(digits))
    text = text[:whole] + generator.choice(['.', '.', '.', '']) + text[whole:]
    if generator.random() < 0.5:
        text += generator.choice('eE') + generator.choice(['', '-', '+', '-', '-']) + str(
            generator.choice([generator.randint(0, 30), generator.randint(0, 340), generator.randint(290, 330)]))
    return (generator.choice(['', '', '-', '+']) + text).encode()


def int_text(generator):
    base = generator.choice([2 ** 63, 2 ** 64, 2 ** 53, 10 ** 15, 10 ** 18, 0, generator.randint(0, 2 ** 70)])
    return str(generator.choice([1, -1]) * (base + generator.randint(-3, 3))).encode()


def big_documents():
    one, two = 524288, 262144
    yield b'x\n' + b'1\n' * one + b'x\n'
    yield b'x\n' + b'1\n' * one + b'1.5\n-7\n'
    yield b'x\n' + b'1\n' * one + b'True\nfalse\n'
    yield b'x\n' + b'True\n' * one + b'2\n'
    yield b'x\n' + b'True\n' * one + b'2.5\n'
    yield b'x\n' + b'True\n' * one + b'x\n'
    yield b'x\n' + b'True\n' * one + b'9223372036854775808\n'
    yield b'x\n' + b'9007199254740993\n' * one + b'9223372036854775808\n18446744073709551615\n'
    yield b'x\n' + b'9007199254740993\n' * one + b'0.5\n'
    yield b'x\n' + b'18446744073709551615\n' * one + b'0.5\n'
    yield b'x\n' + b'1.5\n' * one + b'x\nNA\n1\n'
    yield b'x\n' + b'1\n' * (one - 1) + b'x\n'
    yield b'x\n' + b'1\n' * one
    yield b'x\n' + b'1\n' * (one - 1) + b'x\n' + b'2\n'
    yield b'x\n' + b'1\n' * one + b'2;3\n4;5\n'
    yield b'x\n' + b'1\n' * one + b'2;3\n4;5\n6;7;8\n'
    yield b'x\n' + b'1\n' * one + b'2\n4;5\n'
    yield b'x;y\n' + b'1;\n' * two + b';x\nNA;True\n'
    yield b'x;y\n' + b'1;True\n' * two + b'2;3;4\n5;6;7\n;\n'
    yield b'x;y\n' + b'1;True\n' * two + b'2;3;4\n5;6;7;8\n'
    yield b'x;y\n' + b'k;1;True\n' * two + b'k;x;\n'
    yield b'x;y\n' + b'1;\n' * two + b'2.5;\n' * two + b'x;True\n'
    yield b';'.join(b'c%d' % index for index in range(64)) + b'\n' + (b';' * 63 + b'\n') * 8192 + (b'1' + b';' * 63 + b'\n') * 8192 + (
        b'x;1' + b';' * 62 + b'\n') * 3
    # The 262144-byte read buffer: whitespace, a carriage return, a quoted
    # field and a multi-byte character across its end.
    for filler in (b'ab\n', 'è\n'.encode()):
        for tail in (b'  z\n k\n', b' \t;z\n', b'\r\nz\n', b'"q\nr"\n', b'\r \rz\r', 'è\n'.encode(), b' \n \nz\n'):
            for shift in (0, 1, 2):
                head = b'x\n' + filler * ((262144 - 2) // len(filler))
                head += b'v' * (262144 - shift - len(head) - 1) + b'\n' if 262144 - shift - len(head) > 0 else b''
                yield head + tail + filler * 3
    yield b'x' * 262144 + '﻿'.encode() + b'y\n1\n'
    yield b'x;y\n' + b' ' * 262142 + b'1;2\n'
    yield b'x\n' + b'1\n' * 100 + b'a\xff\n' + b'1\n' * 140000 + b'2;3\n'
    yield b'x\n' + b'1\n' * 140000 + b'2;3\n' + b'a\xff\n'
    yield b'x\n' + b'1\n' * 140000 + b'a\xff\n'


MANGLED_HEADERS = [b'a;a', b'a;a;a', b'a;a.1;a', b'a.1;a;a', b'a;a;a.1', b'a;a;a.2;a.2', b'a;a;a.1;a.1', b'a.1;a.1;a;a', b';;', b'Unnamed: 0;;',
                   b';Unnamed: 0', b'a;;a', b';a;;a', b'Unnamed: 1;;Unnamed: 1', b'0;1;0', b'a;b', b'a;A', b' a;a ', b'"a";a', 'è;è'.encode(),
                   b'a\x00b;a', b'a;a;a;a;a.1;a.1.1']
MANGLED_NAMES = ['a', 'a.1', 'a.2', 'a.3', 'a.1.1', 'a.2.1', 'a.1.1.1', 'Unnamed: 0', 'Unnamed: 1', 'Unnamed: 2', 'Unnamed: 0.1', 'Unnamed: 1.1',
                 '0', '1', '0.1', '', 'b', 'A', ' a', 'a ', 'è', 'è.1', 'zzz']
TEXT_NAMES = ['a', 'a.1', 'b', 'Nome', 'Cognome', 'Codice Fiscale', 'Email', 'Unnamed: 0', 'Unnamed: 1', 'Unnamed: 2', ' x ', 'Città', '1', 'NA',
              'True', '1.5', 'x', 'y', 'zzz', '', 'a.2', 'Data di nascita', '0', '2.5']


def text_inputs():
    """Inputs with the columns read as text."""
    generator = random.Random(20261111)
    for token in TOKENS:
        yield b'x\n' + token + b'\n', ['x']
        yield b'x;y\n' + token + b';k\n', ['x']
        yield b'x;y\n1;' + token + b'\n2;' + token, ['y']
        yield b'x;y\n1;' + token + b'\n2;' + token, ['x']
    for first in PAIR_TOKENS:
        for index, second in enumerate(PAIR_TOKENS):
            yield b'x\n' + first + b'\n' + second + b'\n', ['x']
            if index % 5 == 0:
                yield b'x;y\n' + first + b';1\n' + second + b';' + generator.choice([b'2', b'2.5', b'k', b'True', b'']) + b'\n', [
                    generator.choice(['x', 'y'])]
    for first in FRAME_COLUMNS:
        for second in FRAME_COLUMNS:
            for texts in (['x'], ['y'], ['x', 'y']):
                yield b'x;y\n' + b''.join(first[row] + b';' + second[row] + b'\n' for row in range(3)), texts
            yield b'x;y\n' + b''.join(b'k;' + first[row] + b';' + second[row] + b'\n' for row in range(3)), [generator.choice(['x', 'y', '0'])]
    for _ in range(600):
        chosen = [generator.choice(FRAME_COLUMNS) for _ in range(generator.randint(3, 4))]
        yield b';'.join(b'c%d' % index for index in range(len(chosen))) + b'\n' + b''.join(
            b';'.join(column[row] for column in chosen) + b'\n' for row in range(3)), generator.sample(['c0', 'c1', 'c2', 'c3', 'c9'], generator.randint(1, 3))
    for header in MANGLED_HEADERS:
        width = header.count(b';') + 1
        for body in (b'', b';'.join([b'01'] * width) + b'\n' + b';'.join([b'2'] * width) + b'\n', b';'.join([b'01'] * (width + 1)) + b'\n',
                     b'01\n'):
            for name in MANGLED_NAMES:
                yield header + b'\n' + body, [name]
            for _ in range(12):
                yield header + b'\n' + body, generator.sample(MANGLED_NAMES, generator.randint(2, 4))
    for _ in range(4000):
        data = typed_document(generator)
        present = [name for name in data.split(b'\n')[0].replace(b'\r', b'').decode(errors='ignore').split(';') if '"' not in name]
        pool = present * 3 + [name + '.1' for name in present] + TEXT_NAMES
        yield data, generator.sample(pool, min(len(pool), generator.randint(1, 4)))
    for _ in range(1500):
        yield b''.join(generator.choice(SOUP) for _ in range(generator.randint(1, 30))), generator.sample(
            ['a', '1', 'x', 'a.1', 'True', 'NA', '2.5', 'Unnamed: 0', 'Unnamed: 1', '1.1', 'x.1', 'inf'], generator.randint(1, 4))
    for _ in range(300):
        yield b'x\n' + b'\n'.join(float_text(generator, generator.randint(1, 26)) for _ in range(4)) + b'\n', ['x']
    one, two = 524288, 262144
    yield b'x\n' + b'1\n' * one + b'x\n', ['x']
    yield b'x\n' + b'1\n' * one + b'NA\n2.5\n', ['x']
    for texts in (['x'], ['y'], ['x', 'y'], ['zzz']):
        yield b'x;y\n' + b'01;1\n' * two + b'x;x\n', texts
        yield b'x;y\n' + b';True\n' * two + b'01;2\n\n;\n', texts
    yield b'x;x\n' + b'01;1\n' * two + b'x;x\n', ['x']
    yield b'x;y\n' + b'k;01;1\n' * two + b'k;;x\n', ['x']
    yield b'x;y\n' + b'1;True\n' * two + b'2;3;4\n5;6;7\n;\n', ['y']


def inputs():
    for data in untyped_inputs():
        yield data, []
    yield from text_inputs()


def untyped_inputs():
    generator = random.Random(20261110)
    for token in TOKENS:
        yield b'x\n' + token + b'\n'
        yield b'x;y\n' + token + b';k\n'
        yield b'x;y\n1;' + token + b'\n2;' + token
        yield token + b';y\n1;2\n'
    for first in PAIR_TOKENS:
        for index, second in enumerate(PAIR_TOKENS):
            yield b'x\n' + first + b'\n' + second + b'\n'
            if index % 5 == 0:
                yield b'x;y\n' + first + b';1\n' + second + b';' + generator.choice([b'2', b'2.5', b'k', b'True', b'']) + b'\n'
    for _ in range(1500):
        cells = [generator.choice(TOKENS) for _ in range(generator.randint(3, 5))]
        yield b'x\n' + b'\n'.join(cells) + b'\n'
    for first in FRAME_COLUMNS:
        for second in FRAME_COLUMNS:
            yield b'x;y\n' + b''.join(first[row] + b';' + second[row] + b'\n' for row in range(3))
            yield b'x;y\n' + b''.join(b'k;' + first[row] + b';' + second[row] + b'\n' for row in range(3))
    for _ in range(600):
        chosen = [generator.choice(FRAME_COLUMNS) for _ in range(generator.randint(3, 4))]
        yield b';'.join(b'c%d' % index for index in range(len(chosen))) + b'\n' + b''.join(
            b';'.join(column[row] for column in chosen) + b'\n' for row in range(3))
    for item in json.loads(HEADERS.read_text())['cases']:
        yield base64.b64decode(item['input'])
    for _ in range(5000):
        yield typed_document(generator)
    for _ in range(4000):
        yield b''.join(generator.choice(SOUP) for _ in range(generator.randint(1, 30)))
    for _ in range(600):
        yield b'x\n' + b'\n'.join(float_text(generator, generator.randint(1, 15)) for _ in range(20)) + b'\n'
    for _ in range(5000):
        yield b'x\n' + float_text(generator, generator.randint(16, 26)) + b'\n'
    for _ in range(300):
        yield b'x\n' + b'\n'.join(int_text(generator) for _ in range(generator.randint(1, 6))) + b'\n'
    yield from big_documents()


def main():
    import pandas
    if not HEADERS.is_file():
        raise SystemExit('missing ' + str(HEADERS))
    seen, documents = set(), []
    for data, texts in inputs():
        if (data, tuple(texts)) not in seen:
            seen.add((data, tuple(texts)))
            documents.append((data, texts))
    results = [None] * len(documents)
    position = iter(range(len(documents)))
    lock = threading.Lock()

    def drain():
        runner = Worker()
        while True:
            with lock:
                index = next(position, None)
            if index is None:
                break
            results[index] = runner.run(*documents[index])
        runner.stop()

    threads = [threading.Thread(target=drain) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if not documents or any(result is None for result in results):
        raise SystemExit('incomplete run')
    cases = [{'input': base64.b64encode(data).decode(), **({'texts': texts} if texts else {}), **result}
             for (data, texts), result in zip(documents, results)]
    payload = json.dumps({'pandas': pandas.__version__, 'cases': cases}, ensure_ascii=False, separators=(',', ':')).encode()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=buffer, compresslevel=9, mtime=0) as stream:
        stream.write(payload)
    OUTPUT.write_bytes(buffer.getvalue())
    kinds = {}
    for case in cases:
        for handle in ('raw', 'text'):
            if handle in case:
                kind = handle + ':' + case[handle].get('error', 'digest' if 'digest' in case[handle] else 'frame')
                kinds[kind] = kinds.get(kind, 0) + 1
    print(len(cases), 'cases', sum('texts' in case for case in cases), 'with text columns', sorted(kinds.items()), len(buffer.getvalue()), 'bytes')


if __name__ == '__main__':
    if sys.argv[1:] == ['--worker']:
        worker()
    else:
        main()
