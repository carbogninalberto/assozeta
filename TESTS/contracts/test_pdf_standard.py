"""Controls of the opt-in PDF standard (TESTS/contracts/pdf.py): equal
documents pass whatever their deflate output, and every real difference fails.

Run with the migration interpreter: ``python -m unittest TESTS.contracts.test_pdf_standard``.
"""
import base64
import copy
import os
import re
import time
import unittest
import zlib
from datetime import datetime, timedelta, timezone

from TESTS.contracts import pdf

SPEC = {'engine': 'data_table', 'columns': ['Nome', 'Cognome', 'Totale'],
        'rows': [['Anna', 'Rossi', {'decimal': '10.50'}], ['Città', None, {'int': '3'}]], 'title': 'Elenco (giorno stampa 10/11/2026)'}


def utc_document(spec):
    """(document, before, after) built as a server in UTC builds it."""
    saved = os.environ.get('TZ')
    os.environ['TZ'] = 'UTC'
    time.tzset()
    try:
        before = datetime.now(timezone.utc)
        data = pdf.build(spec)
        after = datetime.now(timezone.utc)
    finally:
        if saved is None:
            del os.environ['TZ']
        else:
            os.environ['TZ'] = saved
        time.tzset()
    return data, before, after


def recompress(data, level):
    """The same document with its page streams deflated at another level and the lengths, offsets and startxref rewritten."""
    out, offsets, position = [pdf.HEADER], [], len(pdf.HEADER)
    while True:
        match = pdf.OBJECT.match(data, position)
        if not match:
            break
        marker = data.find(b'>>\nstream\n', position)
        end = data.find(b'endobj\n', position)
        if 0 <= marker < end:
            dictionary = data[position:marker + 3]
            length = int(pdf.LENGTH.search(dictionary).group(1))
            raw = data[marker + 10:marker + 10 + length]
            if b'/FlateDecode' in dictionary:
                raw = base64.a85encode(zlib.compress(zlib.decompress(base64.a85decode(b'<~' + raw, adobe=True)), level), adobe=True)[2:]
            piece = pdf.LENGTH.sub(b'/Length %d' % len(raw), dictionary) + b'stream\n' + raw + b'endstream\nendobj\n'
            position = marker + 10 + length + 17
        else:
            piece = data[position:end + 7]
            position = end + 7
        offsets.append(sum(map(len, out)))
        out.append(piece)
    start = sum(map(len, out))
    out.append(b'xref\n0 %d\n0000000000 65535 f \n' % (len(offsets) + 1) + b''.join(b'%010d 00000 n \n' % offset for offset in offsets))
    trailer = data[data.index(b'trailer\n'):]
    out.append(re.sub(rb'startxref\n\d+\n', b'startxref\n%d\n' % start, trailer))
    return b''.join(out)


class PDFStandard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actual, cls.before, cls.after = utc_document(SPEC)

    def reason(self, spec=SPEC, actual=None, before=None, after=None):
        return pdf.matches_bytes(spec, self.actual if actual is None else actual, before or self.before, after or self.after)

    def changed(self, old, new, count=1):
        data = self.actual.replace(old, new, count)
        self.assertNotEqual(data, self.actual, f'{old!r} not in the document')
        return data

    def test_equal_document_passes(self):
        self.assertIsNone(self.reason())

    def test_deflate_output_is_free(self):
        for level in (0, 1, 9):
            data = recompress(self.actual, level)
            self.assertNotEqual(data, self.actual)
            self.assertIsNone(self.reason(actual=data))

    def test_base64_wrapper(self):
        encoded = base64.b64encode(self.actual).decode()
        self.assertIsNone(pdf.matches(SPEC, encoded, self.before, self.after))
        self.assertEqual(pdf.matches(SPEC, encoded + '!', self.before, self.after), 'not base64')
        self.assertEqual(pdf.matches(SPEC, None, self.before, self.after), 'not a base64 string')
        expected = {'file': {'$pdf': SPEC}, 'filename': 'x.pdf'}
        self.assertEqual(pdf.resolve(expected, {'file': encoded, 'filename': 'x.pdf'}, self.before, self.after),
                         ({'file': encoded, 'filename': 'x.pdf'}, None))
        other = base64.b64encode(utc_document({**SPEC, 'title': 'Altro'})[0]).decode()
        self.assertTrue(pdf.resolve(expected, {'file': other, 'filename': 'x.pdf'}, self.before, self.after)[1].startswith('pdf part'))
        self.assertTrue(pdf.resolve(expected, {'filename': 'x.pdf'}, self.before, self.after)[1].startswith('pdf not a base64'))

    def test_content_differences_fail(self):
        dropped = copy.deepcopy(SPEC)
        dropped['columns'].pop()
        for row in dropped['rows']:
            row.pop()
        total = copy.deepcopy(SPEC)
        total['rows'][0][2] = {'decimal': '10.51'}
        for name, spec in {
            'column dropped': dropped,
            'wrong total': total,
            'title': {**SPEC, 'title': 'Elenco (giorno stampa 11/11/2026)'},
            'row order': {**SPEC, 'rows': SPEC['rows'][::-1]},
            'alignment': {**SPEC, 'align': ['LEFT', 'CENTER', 'CENTER']},
            'page size': {**SPEC, 'is_a3': True},
            'orientation': {**SPEC, 'landscape': False},
            'extra row': {**SPEC, 'rows': SPEC['rows'] + [['x', 'y', 'z']]},
            'none against empty text': {**SPEC, 'rows': [SPEC['rows'][0], ['Città', '', {'int': '3'}]], 'columns': ['Nome', 'Cog', 'Totale']},
        }.items():
            with self.subTest(name):
                reason = self.reason(spec=spec)
                self.assertIsNotNone(reason)
                self.assertRegex(reason, r'^(part \d+ at byte|\d+ objects)')

    def test_stream_edits_fail(self):
        # One text position moved by 0.001 point, one rule dropped, one colour changed.
        parts, _, _ = pdf.normalize(self.actual)
        stream = next(part for part in parts if b'stream\n' in part)
        body = stream[stream.index(b'stream\n') + 7:-len(b'endstream\n')]
        for name, old, new in (('position', b' 44 Tm (Nome)', b' 44.001 Tm (Nome)'), ('rule', b'\nn 0 0 m 0 57 l S', b''),
                               ('colour', b'0 0 0 RG', b'0 0 .1 RG'), ('font', b'/F2 10 Tf', b'/F1 10 Tf')):
            with self.subTest(name):
                self.assertIn(old, body)
                edited = body.replace(old, new, 1)
                raw = base64.a85encode(zlib.compress(edited), adobe=True)[2:]
                marker = self.actual.index(b'>>\nstream\n', self.actual.index(b'/FlateDecode'))
                length = int(pdf.LENGTH.search(self.actual, self.actual.rindex(b' 0 obj\n', 0, marker)).group(1))
                data = self.actual[:marker + 10] + raw + self.actual[marker + 10 + length:]
                data = data.replace(b'/Length %d' % length, b'/Length %d' % len(raw), 1)
                data = recompress(data, 6)
                self.assertRegex(self.reason(actual=data), r'^part \d+ at byte')

    def test_dates_must_be_in_the_window_and_in_utc(self):
        self.assertRegex(self.reason(before=self.after + timedelta(seconds=2), after=self.after + timedelta(seconds=3)), '^dates .* outside')
        self.assertRegex(self.reason(before=self.before - timedelta(seconds=9), after=self.before - timedelta(seconds=2)), '^dates .* outside')
        local = pdf.matches_bytes(SPEC, self.changed(b"+00'00'", b"+01'00'", 2), self.before, self.after)
        self.assertRegex(local, 'not in UTC')
        self.assertEqual(self.reason(actual=self.changed(b'/ModDate (D:2', b'/ModDate (D:3')), 'document: dates')

    def test_inconsistent_files_fail(self):
        digest = re.search(rb'\[<([0-9a-f]{32})>', self.actual).group(1)
        flipped = bytes([digest[0] ^ 1]) + digest[1:]
        for name, data, text in (
            ('digest halves', self.changed(b'<' + digest + b'>]', b'<' + flipped + b'>]'), 'trailer'),
            ('offset', self.changed(b'0000000073 00000 n', b'0000000074 00000 n'), 'cross-reference'),
            ('startxref', re.sub(rb'startxref\n(\d+)', lambda m: b'startxref\n%d' % (int(m.group(1)) + 1), self.actual), 'trailer /ID, /Size or startxref'),
            ('truncated', self.actual[:-1], 'trailer'),
            ('length', self.changed(b'/Length ', b'/Length 1'), 'stream'),
            ('header', b'%PDF-1.3' + self.actual[8:], 'header'),
            ('empty', b'', 'header'),
            ('not flate', re.sub(rb'(stream\n)(.{6})', rb'\1!!!!!!', self.actual, count=1, flags=re.S), 'stream'),
        ):
            with self.subTest(name):
                reason = self.reason(actual=data)
                self.assertIsNotNone(reason)
                self.assertIn(text, reason)

    def test_extra_object_fails(self):
        other, _, _ = utc_document({**SPEC, 'rows': SPEC['rows'] * 40})
        self.assertRegex(pdf.matches_bytes(SPEC, other, self.before, datetime.now(timezone.utc)), r'^\d+ objects, expected \d+')


if __name__ == '__main__':
    unittest.main()
