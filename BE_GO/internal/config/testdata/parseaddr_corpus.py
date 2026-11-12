"""Write parseaddr.json: email.utils.parseaddr (Python 3.13, strict) outputs.

core.settings builds DEFAULT_FROM_EMAIL = f"{WHITELABEL_NAME} <{EMAIL_HOST_USER}>";
the Go port (config.ParseAddr) must return the same (name, address) pairs.
Run with the production interpreter: python -I parseaddr_corpus.py
"""
import json
import sys
from email.utils import parseaddr
from pathlib import Path

assert sys.version_info[:2] == (3, 13), 'the production interpreter (3.13) is the oracle'
NAMES = ['Assozeta', 'Bakney Sport', 'A.S.D. Polisportiva', 'Polisportiva Città', 'Società Sportiva "Aurora"',
         'Polisportiva, Città', 'A.S.D. Polisportiva "Città" (Aurora)', 'ASD Ginnastica & Danza', "L'Atletica Sud",
         'S.S.D. a r.l. Nuoto Club', 'Circolo (Tennis) Club', 'Gruppo <Sportivo>', 'Club: Tennis', 'Club; Tennis',
         'Club @ Lago', 'Club [Lago]', 'Unione "Sportiva', 'Club (aperto', 'Club chiuso)', '"Polisportiva"',
         '  Spazi   Multipli  ', 'Tab\tName', 'Back\\slash', 'Quote \\" Escape', 'Ünïcödé Ñame', '中文 俱乐部',
         'Name.With.Dots.', '.Leading', 'a,b,c', '', 'None', 'Emoji 🏐 Club', 'Club Nbsp', 'Ctrl\x1cName',
         'Paren (nested (deep)) Club', 'Quoted "a, b" Club', '"Mixed" (comment) Name', 'Trailing\\']
USERS = ['', 'None', 'segreteria@polisportiva.example', 'info@example.test', 'Mixed.Case@Example.Test', 'user',
         'a@b@c', 'first last@example.test', '"quoted"@example.test', 'user@[192.0.2.1]', 'user@', '@example.test',
         'us,er@example.test', 'user@exa mple.test', 'user.@example.test', 'user@example.test>', '<user@example.test']
RAW = ['', 'plain@example.test', 'Name <a@b>, Other <c@d>', 'group: a@b, c@d;', '<>', 'Name <>', '()', '(x', 'x)',
       '"unterminated', 'Name <a@b> trailing', '<<a@b>>', 'Name <@route:a@b>', 'a@b (Comment)', 'Name (c) <a@b>',
       'Name\r\n <a@b>', 'a . b @ c . d', '"a\\"b" <x@y>', '\\', '"\\"', 'Name <a@[b>']
cases = [f'{name} <{user}>' for name in NAMES for user in USERS] + RAW
out = [[text, *parseaddr(text)] for text in cases]
Path(__file__).with_name('parseaddr.json').write_text(json.dumps(out, ensure_ascii=False, indent=0) + '\n')
print(len(out), 'cases')
