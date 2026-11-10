"""Capture the birthplace name get_data_from_italian_fiscal_code gives for BE_GO/internal/codicefiscale.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/codicefiscale_decode_golden.py``
The helper (BE/application/utils/api_utils.py) is codicefiscale.decode(code),
repeated with the expected control character when only that one is wrong, and
None on any exception. Codes are encoded from seeded random people, then
altered: a wrong control character, omocode letters, lower case and spacing,
a truncated or padded code, another month letter or place. Written to
``BE_GO/internal/codicefiscale/testdata/decode_names.json`` with the year
datetime.now() had; ``go test ./internal/codicefiscale`` requires the same
name (or None) for every code.
"""
from datetime import datetime
import json
from pathlib import Path
import random

from codicefiscale import codicefiscale

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/codicefiscale/testdata/decode_names.json'


def helper(fiscal_code):
    """get_data_from_italian_fiscal_code, without the print."""
    try:
        result = None
        try:
            result = codicefiscale.decode(fiscal_code)
        except ValueError as error:
            text = str(error)
            if 'wrong CIN' in text:
                cin = text.split('expected ')[1].split(',')[0].replace("'", '')
                fiscal_code = fiscal_code[:-1] + cin
                result = codicefiscale.decode(fiscal_code)
        return result
    except Exception:
        return None


def main():
    generator = random.Random(20261110)
    places = sorted(codicefiscale._DATA['municipalities']) + sorted(codicefiscale._DATA['countries'])
    names = ['Mario', 'Anna', 'Luca', 'Giulia', 'Ugo', 'Maria Chiara', 'Jo', 'Francesca']
    surnames = ['Rossi', 'Bianchi', 'Verdi', 'Fo', 'De Luca', 'Esposito', 'Ricci']
    codes = ['', 'x', 'RSSMRA80A01H501U', 'rssmra80a01h501u', ' RSSMRA80A01H501U ', 'RSSMRA80A01H501X', 'RSSMRA80A01H501X ',
             'RSS MRA 80A01 H501U', 'RSSMRA80A01H501', 'RSSMRA80A01H501UU', 'RSSMRA80A41H501Y', 'RSSMRA80A71H501U', 'RSSMRA80A00H501U',
             'RSSMRA80B31H501U', 'RSSMRA80A01Z999U', 'RSSMRA80A01H5O1U', 'RSSMRAU0A01H501U', '1234567890123456', 'RSSMRA80A01H501Ù']
    for _ in range(1500):
        year, month, day = generator.randint(1900, 2030), generator.randint(1, 12), generator.randint(1, 28)
        try:
            code = codicefiscale.encode(generator.choice(surnames), generator.choice(names), generator.choice('MF'),
                                        f'{day:02d}/{month:02d}/{year}', generator.choice(places))
        except ValueError:
            continue
        codes.append(code)
        roll = generator.random()
        if roll < 0.2:
            codes.append(code[:-1] + generator.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
        elif roll < 0.4:
            codes.append(generator.choice(codicefiscale.decode(code)['omocodes']))
        elif roll < 0.5:
            codes.append(code.lower() if generator.random() < 0.5 else ' '.join([code[:6], code[6:11], code[11:]]))
        elif roll < 0.6:
            codes.append(code[:8] + generator.choice('ABCDEHLMPRST') + code[9:])
        elif roll < 0.7:
            codes.append(code[:11] + generator.choice('ABFHLZ') + f'{generator.randint(0, 999):03d}' + code[15:])
        elif roll < 0.75:
            codes.append(code[:generator.randint(10, 15)])
    cases = []
    for code in codes:
        data = helper(code)
        cases.append({'code': code, 'name': data['birthplace']['name'] if data else None})
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({'year': datetime.now().year, 'cases': cases}, ensure_ascii=False, indent=0) + '\n')
    print(len(cases), 'codes', sum(case['name'] is not None for case in cases), 'named')


if __name__ == '__main__':
    main()
