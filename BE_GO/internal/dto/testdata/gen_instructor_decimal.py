"""Generate the instructor compensation decimal golden corpus (instructor_decimal.json).

CPython's decimal module is the reference for the compensation notes the view
composes (BE/application/views/instructor_views.py):

    amount = Decimal(str(calculation_data['amount']))
    scaled = amount * (Decimal(hour.percentage_billing) / 100)

Both values reach the notes through str(). The corpus pins str(Decimal(text)),
the constructor's validity (InvalidOperation), str(Decimal(p) / 100) for the
stored percentages and str(Decimal(a) * (Decimal(p) / 100)) together with the
trapped outcomes (InvalidOperation, Overflow). It is generated with CPython
3.13 and only the standard library, deterministically.

Run from the worktree root:
    python3 -I BE_GO/internal/dto/testdata/gen_instructor_decimal.py
"""
import json
import random
from decimal import Decimal, InvalidOperation, Overflow
from pathlib import Path

OUTPUT = Path(__file__).with_name('instructor_decimal.json')
PERCENTAGES = ['50.00', '99.99', '33.33', '12.50', '0.01', '100.00']
MAX_STR = 4000

random.seed(20261117)


def parse_case(text):
    try:
        value = Decimal(text)
    except InvalidOperation:
        return {'text': text, 'outcome': 'invalid'}
    entry = {'text': text, 'outcome': 'ok'}
    rendered = str(value)
    if len(rendered) <= MAX_STR:
        entry['str'] = rendered
    return entry


def multiply_case(amount, percentage):
    scaled = Decimal(percentage) / 100
    entry = {'amount': amount, 'percentage': percentage}
    try:
        result = Decimal(amount) * scaled
    except InvalidOperation:
        entry['outcome'] = 'invalid'
        return entry
    except Overflow:
        entry['outcome'] = 'overflow'
        return entry
    entry['outcome'] = 'ok'
    rendered = str(result)
    if len(rendered) <= MAX_STR:
        entry['str'] = rendered
    return entry


def digits(count, zero_heavy=False):
    if zero_heavy:
        return ''.join(random.choice('00000123456789') for _ in range(count))
    return ''.join(random.choice('0123456789') for _ in range(count))


def finite_text():
    """A Decimal string with random sign, coefficient, fraction and exponent."""
    sign = random.choice(['', '', '-', '+'])
    whole = digits(random.randint(1, 40), random.random() < 0.15)
    fraction = digits(random.randint(0, 30), random.random() < 0.25) if random.random() < 0.6 else ''
    point = '.' if fraction or random.random() < 0.2 else ''
    exponent = ''
    if random.random() < 0.65:
        kind = random.random()
        if kind < 0.4:
            number = random.randint(-60, 60)
        elif kind < 0.6:
            number = random.choice([999999999, 1000000000, 999999999999999999, 1000000000000000000,
                                    -1999999999999999997, -1999999999999999998, -1000026, -1000027, 999999])
        elif kind < 0.8:
            number = random.randint(-1000050, 1000050)
        else:
            number = random.randint(-10**15, 10**15)
        text = str(abs(number))
        if random.random() < 0.25:
            text = '0' * random.randint(1, 30) + text
        sign_part = '-' if number < 0 else random.choice(['', '+'])
        exponent = random.choice(['E', 'e']) + sign_part + text
    return sign + whole + point + fraction + exponent


FIXED_PARSE = [
    '0', '-0', '+0', '0.00', '-0.000', '0E+5', '0E-5', '0E+999999', '0E+1000000',
    '0E-1000026', '0E-1000027', '0E-2000000', '0E+2000000',
    '1', '-1', '+1', '1.', '.5', '.', '1.5', '-1.50', '00123.4500',
    'NaN', 'sNaN', 'NaN0', 'NaN0005', '-NaN', '+NaN', 'sNaN5', '-sNaN0', 'NaN1_2', 'NaN01_2',
    'Inf', '-INFINITY', 'infinity', '+Inf', 'Infinit', 'nanx', 'NaN 1', 'sNaN_1',
    '1E999999999999999999', '1E1000000000000000000', '123E999999999999999997', '123E999999999999999998',
    '12E999999999999999998', '1E-1999999999999999997', '1E-1999999999999999998', '12E-1999999999999999998',
    '0E999999999999999999', '0E1000000000000000000', '0E-1999999999999999997', '0E-1999999999999999998',
    '0.0123E1000000000000000000', '0.001E-1999999999999999997', '1.23E-1999999999999999995',
    '123E999999999999999999', '1.23E1000000000000000000',
    '1E00000000000000000000000000001', '1E+0000000000000000000000005', '1E-0000000000000000000000005',
    '1E' + '0' * 80 + '5', '1E' + '0' * 80 + '-5', '1_000', '1__0', '_1', '1_', '1_0.5_0', '1e1_0',
    ' 1 ', '\t1\n', '1 2', '', ' ', 'abc', '10,5', 'True', 'None', '1.2.3', 'e1', '1e', '1e+', '+', '-',
    '--1', '1E++5', '0.1E', '0.1E-', '1E5E5', 'Infinity5', 'inf5', 'sNaN5x',
    '0.' + '0' * 100 + '123', '1' + '0' * 100, '-' + '9' * 80,
    '١٢٣', '١٢.٥', '-١E٢', ' \u00a01 ',
]


def parse_cases():
    cases = [parse_case(text) for text in FIXED_PARSE]
    for length in [1, 2, 27, 28, 29, 30, 35, 40, 64, 100, 200]:
        cases.append(parse_case('NaN' + digits(length)))
        cases.append(parse_case('-sNaN' + digits(length)))
        cases.append(parse_case('nan' + '0' * 5 + digits(length)))
    for number in [999999999999999999, 999999999999999998, 1000000000000000000,
                   -1999999999999999997, -1999999999999999998, -1999999999999999996]:
        for prefix in ['1', '12', '123', '0', '0.0', '0.0123', '1.23', '123.45', '0.001', '9' * 30]:
            cases.append(parse_case(prefix + 'E' + str(number)))
            cases.append(parse_case(prefix + 'E' + ('+' if number >= 0 else '-') + '0' * 7 + str(abs(number))))
    while len(cases) < 2300:
        cases.append(parse_case(finite_text()))
    return cases


SPECIAL_AMOUNTS = [
    'NaN', '-NaN', 'sNaN', '-sNaN7', 'Infinity', '-Infinity', 'inf',
    'NaN' + '9' * 27 + '12', 'NaN' + '1' * 35, '-NaN' + '1' * 35,
    'NaN1' + '0' * 27 + '5', 'NaN' + '0' * 30 + '5', 'sNaN' + '9' * 40,
    '0', '-0', '0.000', '-0.000', '0E-2000000', '0E+2000000', '0E-1000026', '0E+999999',
    '0E-1999999999999999997', '0E+999999999999999999',
    '5E-1000026', '15E-1000027', '1E-1000030', '-5E-1000026', '1E-1000026', '3E-1000026',
    '2.5E-1026', '1.5E-1026', '1E-1026', '1E-1025', '9E-1000026', '95E-1000027', '99E-1000027',
    '1E+999999', '2E+999999', '1E+1000000', '2E+1000000', '9.99E+999999', '1E1000000000',
    '1E1000000000000000', '1E1000000000000000000', '1E-1000000000', '1E-1000000000000000',
    '1E-1999999999999999997', '123.45', '-123.45', '1.005', '1.015', '2.675',
    '9999999999999999999999999999', '99999999999999999999999999999', '1000000000000000000000000000',
    '12345678901234567890123456789', '12345678901234567890123456788', '12345678901234567890123456791',
    '1234567890123456789012345678' + '123456789999',
]


def multiply_cases():
    cases = []
    for amount in SPECIAL_AMOUNTS:
        try:
            Decimal(amount)
        except InvalidOperation:
            # The view raises at Decimal(str(amount)) before multiplying.
            continue
        for percentage in PERCENTAGES:
            cases.append(multiply_case(amount, percentage))
    while len(cases) < 2500:
        text = finite_text()
        try:
            Decimal(text)
        except InvalidOperation:
            continue
        cases.append(multiply_case(text, random.choice(PERCENTAGES)))
    return cases


def main():
    percentages = [{'text': text, 'quotient': str(Decimal(text) / 100)} for text in PERCENTAGES]
    parse = parse_cases()
    multiply = multiply_cases()
    OUTPUT.write_text(json.dumps({'percentages': percentages, 'parse': parse, 'multiply': multiply},
                                 ensure_ascii=False, indent=1) + '\n')
    print(len(percentages), 'percentages,', len(parse), 'parse,', len(multiply), 'multiply',
          '->', OUTPUT)


if __name__ == '__main__':
    main()
