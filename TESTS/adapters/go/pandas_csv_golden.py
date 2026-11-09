"""Capture pandas.read_csv(file, delimiter=';') column names for BE_GO/internal/pandascsv.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/pandas_csv_golden.py``
Each input is parsed exactly as get_pandas_df_from_file does for a '.csv'
document (a binary file object, delimiter ';', every other option default) and
the outcome recorded: the column list, or the exception class. Inputs are the
authored edge cases plus seeded random documents over an alphabet of
delimiters, quotes, line ends, blanks, repeated names and non-ASCII text.
Written to ``BE_GO/internal/pandascsv/testdata/pandas_headers.json``;
``go test ./internal/pandascsv`` requires the same outcome for every input.
"""
import base64
import io
import json
from pathlib import Path
import random
import warnings

import pandas

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/pandascsv/testdata/pandas_headers.json'

AUTHORED = [
    b'', b'\n', b'\n\n', b' \n\t\n', b'a', b'a\n', b'a;b;c\n1;2;3\n', b'a;b;c\n1;2;3\n1\n1;2\n', b'a;b;c\n1;2;3;4\n1;2;3;4\n',
    b'a;b;c\n1;2;3;4\n1;2;3;4;5\n', b'a;;a;a.1;a\n', b'\n\n  \na;b\n', b'"a\nb";c\n', b'"a"x;c\n', b'"abc', b'a;b\n"x',
    b'a;b;\n1;2;3;4\n', b'\xef\xbb\xbfa;b\n', b'a;b\r\n1;2\r\n', b'a;b\r1;2\r', b'Unnamed: 1;;x\n', b'a;b\n1;2;3\n', b'1;2\n',
    b' a ; b \n', b'a;b\n1\n1;2;3\n', b'a;b\n1;2;3;4\n', b'a;b\n1;2;3\n1;2;3;4\n', b';\n1;2;3\n', b'a;b\n"1;2";3;4\n', b'a;\tb\n',
    b'a"b;c\n', b'"a""b";c\n', b'"a" ;c\n', b';;\n', b'a;a;a.1\n', b'a.1;a;a\n', b'a;a.1;a;a.1\n', b'a\r\n\r\nb\r\n', b'"a;b\n',
    b' ;\n', b' "a";b\n', b'\t\n"a";b\n', b'a;b\n \n1;2;3\n', b'a;b\n"";""\n', b'"";\n', b'""\n', b'a;b\n1;2\n\n1;2;3\n',
    b'Nome;Cognome;Codice Fiscale;Data di nascita;Email\nMario;Rossi;RSSMRA80A01H501U;01/01/1980;mario@example.test\n',
    'Città;Società;Età\nRoma;ASD;30\n'.encode(), b'a;b\r\n"x\r\ny";z\r\n', b'a;b\r\n"x\r\n', b'a;b;c;d\n1;2\n1;2;3;4\n1;2;3;4;5\n',
    b'"a"\r\n', b'"a"\r', b'a;b"', b'"a"";b\n', b'a\n\n\n1;2;3;4;5\n1;2\n', b'  a;b\n', b'a  \n', b' \t \n \nx;y\n',
]
ALPHABET = [b';', b';', b'"', b'\n', b'\r\n', b'\r', b' ', b'\t', b'a', b'b', b'a.1', b'Unnamed: 0', b'x', b'\xc3\xa0', b'"', b'1', b'']


def outcome(data):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            frame = pandas.read_csv(io.BytesIO(data), delimiter=';')
    except pandas.errors.EmptyDataError:
        return {'error': 'empty'}
    except pandas.errors.ParserError:
        return {'error': 'parser'}
    columns = frame.columns.tolist()
    assert all(isinstance(column, str) for column in columns), data
    return {'columns': columns}


def main():
    generator = random.Random(20261109)
    inputs = list(AUTHORED)
    for _ in range(4000):
        inputs.append(b''.join(generator.choice(ALPHABET) for _ in range(generator.randint(1, 24))))
    crlf = [item for item in ALPHABET if item != b'\r']
    for _ in range(4000):
        inputs.append(b''.join(generator.choice(crlf) for _ in range(generator.randint(1, 40))))
    for _ in range(800):
        rows = []
        width = generator.randint(1, 5)
        for _ in range(generator.randint(1, 6)):
            count = max(0, width + generator.choice([-2, -1, 0, 0, 0, 1, 2]))
            rows.append(b';'.join(generator.choice([b'a', b'b', b'', b'"q;r"', b'1', b' ']) for _ in range(count)))
        inputs.append(b'\n'.join(rows) + generator.choice([b'', b'\n', b'\r\n']))
    seen, cases = set(), []
    for data in inputs:
        if data in seen:
            continue
        seen.add(data)
        cases.append({'input': base64.b64encode(data).decode(), **outcome(data)})
    OUTPUT.write_text(json.dumps({'pandas': pandas.__version__, 'cases': cases}, ensure_ascii=False, indent=0) + '\n')
    print(len(cases), 'cases')


if __name__ == '__main__':
    main()
