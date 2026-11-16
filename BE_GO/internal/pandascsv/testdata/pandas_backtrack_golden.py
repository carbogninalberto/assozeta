"""Capture pandas.read_csv(..., delimiter=';') answers for the bare-CR re-read for BE_GO/internal/pandascsv.

Run with the migration interpreter from the repository root:
``python -I BE_GO/internal/pandascsv/testdata/pandas_backtrack_golden.py``

The C tokenizer reads a whitespace-led line after a bare carriage return as a
line of its own, and when the whitespace turns out to open a field it steps
back to the first byte after the last line feed of the tokenize call (or to
where the call began) and reads those bytes again. The words and lines the
second pass appends are dups of buffered ones, so the parser's buffers
(``stream``, ``words``, ``lines``) and the reader's row limit decide the
answer: pandas raises ``ParserError('Buffer overflow caught - possible
malformed input file.')`` when a buffer fills first, or stops at the row
limit (``2**20 // width`` rows a chunk) and answers the rows read so far.

Each input is parsed as get_pandas_df_from_file does for a '.csv' document,
once per kind of file object the storage hands to pandas:

* ``raw``: a handle without a ``mode`` (a django-storages ``S3File`` in
  production): pandas hands its bytes to the C parser undecoded.
* ``text``: a binary handle with a ``mode`` (``FileSystemStorage``,
  ``BytesIO``): pandas decodes it as strict UTF-8 and reads 262144
  characters a call, so multibyte text moves the buffer boundaries.

The outcome is the exception, or the columns, the dtype of every column and,
for every row of ``DataFrame.iterrows()``, a tag for each value's Python type
followed by ``str(value)``: ``s`` str, ``i`` int, ``f`` float, ``b`` bool,
``I`` numpy.int64, ``U`` numpy.uint64, ``F`` numpy.float64, ``B`` numpy.bool.
Frames of more than 4000 cells record the row count and a SHA-256 of the same
cells instead. An input pandas does not finish within the time limit is
recorded as ``timeout``.

Inputs: authored documents around a bare carriage return and a whitespace-led
line (the whitespace run, what follows it, a line feed earlier in the buffer
or not, one to four fields, quotes, a BOM, NUL and multibyte bytes), then the
same shapes at the 262144-byte read boundaries, with prefixes long enough to
fill the parser's buffers, and documents whose re-read runs to the row limit.
Written to ``BE_GO/internal/pandascsv/testdata/pandas_backtrack.json.gz``;
``go test ./internal/pandascsv`` requires the same outcome for every input.
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
import threading
import warnings

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = ROOT / 'BE_GO/internal/pandascsv/testdata/pandas_backtrack.json.gz'
WORKERS = 4
DIGEST_CELLS = 4000


def worker():
    import numpy
    import pandas

    tags = {str: 's', int: 'i', float: 'f', bool: 'b', numpy.int64: 'I', numpy.uint64: 'U', numpy.float64: 'F', numpy.bool_: 'B'}

    class Raw:
        """A file object without a 'mode', as S3Storage.open() hands pandas."""

        def __init__(self, data):
            self._buffer = io.BytesIO(data)

        def read(self, count=-1):
            return self._buffer.read(count)

        def seek(self, offset, whence=0):
            return self._buffer.seek(offset, whence)

        def tell(self):
            return self._buffer.tell()

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
        result = {'raw': outcome(Raw(data), texts)}
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
        timeout = 20 if len(data) < 100000 else 600
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


CHUNK = 262144

# What follows the whitespace run of the line the bare carriage return opens.
TAILS = [b'', b'\r', b'\n', b'\r\n', b'x', b'x\r', b'x\n', b'x\r\n', b';', b';\r', b';\n', b';y\r', b'x;y\r', b'"q"\r', b'"q\nr"\r',
         b'\xff', b'\x00', b'\xc3\xa8', b'\t\r', b' \r']
# The whitespace run that opens that line.
BLANKS = [b'', b' ', b'  ', b'   ', b'\t', b' \t', b'\t ', b'  \t  ', b' ' * 40, b'\t' * 17]
# What precedes the bare carriage return: header and data lines, with or
# without a line feed, so the rewind reaches the call's start or a line feed.
PREFIXES = [b'h', b'h\r', b'h\r\n', b'h\nx', b'h\nx\r', b'h;y', b'h;y\r', b'h;y\r\n', b'h;y\n1;2', b'h;y\n1;2\r', b'h;y;z\r 1;2;3',
            b'"h"\r', b'\xef\xbb\xbfh\r', b'h\x00\r', b'\xc3\xa8\r', b'h;y\n 1;2\r', b'']


def authored():
    """Small documents: one bare carriage return and a whitespace-led line."""
    for prefix in PREFIXES:
        for blank in BLANKS:
            for tail in TAILS:
                yield prefix + b'\r' + blank + tail
    # The whitespace run opens the field the caller reads: the field keeps it.
    for tail in (b' d', b' d\r', b' d\r\n', b' d\n', b'\td\r', b' d;e\r', b'd\re\r', b' d\r ', b' 1\r2\r', b' N A\r'):
        yield b'a\r' + tail
        yield b'a;b\r' + tail
        yield b'a\nx\r' + tail
    # Bare carriage returns in a row, and a whitespace line between line feeds.
    for data in (b'a\r\r\r', b'a\r \r \r ', b'a\r\n\r\n', b'a\r \r\n\r ', b'a\r\t\n\t\r', b'a;\r;\r ;', b'\r \r \r ',
                 b'\r\r x\r\r y\r', b'a\r  \rb\r  \rc\r', b'a\r; \rb\r; \rc\r', b'a\r"x"\r" "\r', b'a\ry\r x\rz\r',
                 b'\ra\r b\r c\rd\r', b'\r \t\r \t\r', b'\n\r \n\r \n\r'):
        yield data
    # Field counts: more fields than the line before raise pandas' own error.
    for data in (b'a\r b;c\r', b'a;b\r c;d\re;f\r', b'a;b;c\r d\r', b'a;b;c\r d;e\r ', b'a\r b;c;d\re;f;g\r'):
        yield data
    # Quotes, NUL and multibyte text after the carriage return.
    for data in (b'a\r" b"\r', b'a\r" b\r', b'a\r "b";c\r', b'a\r\xc3\xa8\r', b'a\r\xc3\xa8;b\r \xc3\xa8\r',
                 b'\xc3\xa8\r b\r', b'a\x00\r b\x00\r', b'a\r b\x00\r', b'a\r;b\r', b'a\r ;\r'):
        yield data
    # A quoted field that swallows line feeds, then the bare carriage return.
    for data in (b'"a\nb"\r c\r', b'"a\rb"\r c\r', b'a\r"x\ny"\r z\r', b'"\r"\r \r'):
        yield data


def at_boundaries():
    """The same shapes where the 262144-byte read buffer ends."""
    line = b'1\n'
    for pad in (0, 2):
        for blank in (b' ', b'\t'):
            for tail in (b' z\r', b'\r', b' z'):
                head = b'h\n' + line * ((CHUNK - pad - 3) // 2)
                data = head + b'\r' + blank + tail
                yield data
                # The whitespace-led line right at the boundary, headings after it.
                yield head + b'\r' + blank + tail + b'\n1\n2\n'
    # The bare carriage return before the boundary, the whitespace after it.
    for shift in (1, 2, 3, 4):
        head = b'h\n' + line * ((CHUNK - shift - 3) // 2)
        data = head + b'\r' + (b' ' * shift) + b' z\r'
        yield data
    # A decoded handle reads 262144 characters, so multibyte text puts the
    # buffer end elsewhere: the answer can be the raw handle's or another.
    for filler in (b'\xc3\xa8\n', b'\xc3\xa8\r'):
        for tail in (b'\r z\r', b'\r\t z\r', b'\r z\n'):
            head = b'h\n' + filler * ((CHUNK - 3) // len(filler))
            yield head + tail
            yield head + tail + filler * 3
    # Wider rows: the row limit is 2**20 // width.
    for width in (2, 3, 4):
        row = b';'.join(b'%d' % (index + 1) for index in range(width))
        head = (b';'.join(b'c%d' % index for index in range(width))) + b'\n' + row + b'\n'
        yield head + b'\r x\r'
        yield b'h\n' + (row + b'\n') * ((CHUNK - 1) // len(row)) + b'\r x\r'


def long_lines():
    """A long line the re-read appends again, past the parser's buffers."""
    for size in (10, 100, 1000, 20000):
        yield b'h\n' + b'x' * size + b'\r y\r'
        yield b'h\n' + b'x' * size + b'\r' + b' ' * 3 + b'y\r'
        yield b'h;y\n1;' + b'x' * size + b'\r 2;y\r'
    # Repeated bare returns, each rewinding over the same long line.
    for count in (2, 3, 5, 9):
        yield b'h\n' + b'x' * 40000 + (b'\r y' * count) + b'\r'
        yield b'h\n' + b'y' * 2000 + (b'\r ' * count) + b'z\r'


def to_the_limit():
    """Re-reads that reach the row limit before the buffers fill."""
    for width in (1, 2, 3):
        row = b';'.join([b'1'] * width)
        header = b';'.join(b'c%d' % index for index in range(width))
        yield header + b'\n' + row + b'\n' + b'\r x\r'
        yield header + b'\n' + row + b'\n' + b'\r\tx\r\n' + row + b'\n'
        yield header + b'\r' + row + b'\r' + b'\r x\r'
    yield b'"x"\r\n;\xc3\xa8\rx1a.1"\n\t\na \r xa\n'
    yield b'aUnnamed: 0a;\t1b\r\t"\r aa\n'


def random_documents(generator):
    """Seeded documents of the shapes above."""
    pieces = [b'a', b'b', b';', b'\r', b'\r', b'\r', b'\n', b'\r\n', b' ', b'\t', b'"', b'\x00', b'\xc3\xa8', b'1', b'NA', b'-2.5']
    for _ in range(700):
        length = generator.randint(3, 40)
        data = b''.join(generator.choice(pieces) for _ in range(length))
        if b'\r' in data:
            yield data
    for _ in range(300):
        head = generator.choice([b'a', b'a;b', b'a;b;c', b'\xc3\xa8;b', b'"a";b'])
        body = []
        for _ in range(generator.randint(1, 6)):
            body.append(generator.choice([b'\r', b'\r\n', b'\n']))
            body.append(generator.choice([b'', b' ', b'\t', b'  ']))
            body.append(generator.choice([b'', b'x', b'x;y', b'y\r', b'y\n', b'"y"', b'\xc3\xa8']))
        yield head + b''.join(body)


def inputs():
    for data in authored():
        yield data, []
    for data in at_boundaries():
        yield data, []
    for data in long_lines():
        yield data, []
    for data in to_the_limit():
        yield data, []
    generator = random.Random(20261216)
    for data in random_documents(generator):
        yield data, []
    # Text columns do not change the tokenizer, only the dtype of the columns.
    for data in (b'a\r b\r', b'h;y\r 1;2\r', b'h;y\n1;2\r x;y\r', b'h;y\n1;2\n\r x;y\r'):
        yield data, ['h']
        yield data, ['y']


def prune(documents, results):
    """Drop inputs whose answer is a big frame the corpus already has.

    A re-read that runs to the reader's row limit answers a frame of 262144
    rows of dup words: hundreds of small inputs reach the same count and the
    same columns, and replaying all of them costs minutes. Keeping two per
    shape keeps the count and the row values of every shape.
    """
    kept, seen = [], {}
    for document, result in zip(documents, results):
        raw = result['raw']
        if raw.get('count', 0) > 1000 and not document[1]:
            signature = (raw.get('count'), tuple(raw.get('columns') or ()), tuple(raw.get('dtypes') or ()))
            seen[signature] = seen.get(signature, 0) + 1
            if seen[signature] > 2:
                continue
        kept.append((document, result))
    return kept


def main():
    import pandas
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
    kept = prune(documents, results)
    cases = [{'input': base64.b64encode(data).decode(), **({'texts': texts} if texts else {}), **result}
             for (data, texts), result in kept]
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
