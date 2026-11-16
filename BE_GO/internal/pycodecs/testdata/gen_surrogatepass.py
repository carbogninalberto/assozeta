"""Writes surrogatepass.json: bytes.decode(codec, 'surrogatepass') results from the reference CPython 3.13.16.

Run: python -I gen_surrogatepass.py > surrogatepass.json
"""
import json
import random

rng = random.Random(7)
PIECES = [b'\xed\xa0\x80', b'\xed\xbf\xbf', b'\xed\x9f\xbf', b'\x00\xd8', b'\xd8\x00', b'\x00\xdc\x00\x00', b'\x00\x00\xdc\x00',
          b'\xff\xfe', b'\xfe\xff', b'a', b'\x80', b'\xc3\xa9', b'\x00', b'\xff', b'\xed\xa0', b'\x3d\xd8\x00\xde']
cases = []
for codec in ['utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'utf-32', 'utf-32-le', 'utf-32-be']:
    for _ in range(150):
        data = b''.join(rng.choice(PIECES) for _ in range(rng.randint(0, 6)))
        try:
            cases.append({'codec': codec, 'in': data.hex(), 'out': [ord(c) for c in data.decode(codec, 'surrogatepass')]})
        except UnicodeDecodeError as e:
            cases.append({'codec': codec, 'in': data.hex(), 'err': [e.start, e.end, e.reason]})
print(json.dumps(cases, separators=(',', ':')))
