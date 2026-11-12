"""Golden: what requests sends for an AI base URL that passed the settings serializer and the probe's own check."""
import django, json, sys
from pathlib import Path
from django.conf import settings
settings.configure(USE_I18N=False)
django.setup()
from urllib.parse import urlsplit
from rest_framework import serializers
import requests, urllib3
from urllib3.util.url import parse_url, _encode_target
field = serializers.URLField(max_length=2000, allow_blank=True)
def stored(value):
    try:
        value = field.run_validation(value)
    except serializers.ValidationError as e:
        return None, 'serializer: ' + str(e.detail[0])
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None, 'serializer: validate_base_url'
    return value, None
def probe(base_url):
    try:
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return {'answer': 'invalid'}
    except Exception as e:
        return {'answer': 'raises', 'error': type(e).__name__}
    url = base_url.rstrip('/') + '/models'
    try:
        prepared = requests.Request('GET', url, headers={'Authorization': 'Bearer k'}).prepare()
    except Exception as e:
        return {'answer': 'raises', 'error': type(e).__name__ + ': ' + str(e)[:80]}
    target = _encode_target(prepared.path_url)
    parts = parse_url(prepared.url)
    host = parts.host
    default = {'http': 80, 'https': 443}[parts.scheme]
    header = host if parts.port in (None, default) else f'{host}:{parts.port}'
    return {'answer': 'request', 'url': prepared.url, 'scheme': parts.scheme, 'connect_host': host.strip('[]'), 'port': parts.port or default,
            'host_header': header, 'target': target}
URLS = '''https://api.example.com/v1
https://API.Example.COM/V1/
HTTPS://api.example.com:8443/v1
http://api.example.com:80/v1
https://api.example.com:443
https://api.example.com
https://api.example.com/
https://api.example.com//
https://api.example.com/v1//
https://api.example.com/a/../b
https://api.example.com/a/./b/
https://api.example.com/../a
https://api.example.com/a/..
https://api.example.com/a/b/../../../c
https://api.example.com/a/%2e%2e/b
https://api.example.com/a%2fb
https://api.example.com/a%2Fb
https://api.example.com/%41%7e%2d
https://api.example.com/a%zzb
https://api.example.com/a%
https://api.example.com/a%4
https://api.example.com/a b
https://api.example.com/a"b
https://api.example.com/a<b>
https://api.example.com/a^b`c
https://api.example.com/a{b}|c
https://api.example.com/a\\b
https://api.example.com/a[b]
https://api.example.com/a;b=c,d
https://api.example.com/a:b@c
https://api.example.com/a!$&'()*+,
https://api.example.com/à/è
https://api.example.com/%C3%A0
https://api.example.com/%c3%a0
https://api.example.com/~user/_x.-y
https://:@api.example.com/v1
https://@api.example.com/v1
https://:@API.example.com
https://user@api.example.com/v1
https://user:pw@api.example.com/v1
https://api.example.com./v1
https://api.example.com:/v1
https://api.example.com:08080/v1
https://api.example.com:65536/v1
https://127.0.0.1/v1
https://127.0.0.1:9000
https://[::1]/v1
https://[::1]:8000/v1
https://[2001:DB8::1]/v1
https://[::ffff:1.2.3.4]/
http://localhost/v1
http://localhost:11434
http://LOCALHOST:11434/api/
https://bücher.example/v1
https://xn--bcher-kva.example/v1
https://straße.example/v1
https://api.example.com/v1?x=1
https://api.example.com/v1#frag
https://api.example.com/v1?
https://api.example.com/v1#
ftp://api.example.com/v1
https://api.example.com/v1/
https://api.example.com/v1///
  https://api.example.com/v1  
https://api_x.example.com/v1
https://-api.example.com/v1
https://a..example.com/v1
https://example/v1
https://api.example.com/a%25b
https://api.example.com/a%2525b
https://api.example.com/a%20b
https://api.example.com/a+b
https://api.example.com/a=b&c
https://api.example.com/%
https://api.example.com/a/b/c/./../../g
https://api.example.com/.
https://api.example.com/..
https://api.example.com/a/.
https://api.example.com/a/./
https://api.example.com/.a/..b/...
https://api.example.com/a//../b
https://Api.Example.com/%7Euser/%7euser
https://api.example.com/a%E2%82%ACb
https://api.example.com/€
https://api.example.com/a\tb
https://1.2.3.4.5/v1
https://0x7f.1/v1
https://api.example.com/v1/models
https://api.example.com/v1/.
https://api.example.com/v1/..
https://BÜCHER.example/v1
https://ΑΒΓ.example/v1
https://straße.example/v1
https://STRASSE.example/v1
https://faß.de/v1
https://ς.example/v1
https://aßς.example/v1
https://😀.example/v1
https://\u0301example.example/v1
https://ab--cd.example/v1
https://bÜ--cher.example/v1
https://api.bücher.example/v1
https://bücher.API.example/v1
https://bücher.example./v1
https://ｅｘａｍｐｌｅ.example/v1
https://bücher。example/v1
https://BÜCHER.example:8443/v1/
https://ünïcode.example/v1
https://xn--strae-oqa.example/v1
https://XN--BCHER-KVA.example/v1
https://straße.example:443/v1
https://api%2Eexample.com/v1
https://%41PI.example.com/v1
https://api%2dexample.com/v1
https://%C3%A0.example/v1
https://api%zz.example/v1
https://api%25.example/v1
https://127.0.0.1:80/v1
http://127.0.0.1:65535/v1
https://[::]/v1
https://[::1]:08080/v1
https://[fe80::1%25eth0]/v1
https://[fe80::1%eth0]/v1
https://[FE80::1%25ETH0]/v1
https://[::FFFF:1.2.3.4]:8443/v1
https://[2001:db8::]/v1
https://[::1]:443/v1
http://[::1]:0/v1
https://1.2.3.4/v1
https://%2e/v1
https://api.example.com/%2e/v1
https://api.example.com/%2E%2E/v1
https://api.example.com/a/%2e%2e/b/
https://api.example.com/a/%2F/../b
https://api.example.com/a%3fb
https://api.example.com/a%23b
https://api.example.com/a%3Ab%40c
https://api.example.com/a%5Bb%5D
https://api.example.com/a%252fb
https://api.example.com//a///b//
https://api.example.com/./a/./
https://api.example.com/a/../../
https://api.example.com/%C3%A9/%E2%82%AC
https://api.example.com/%e2%82%ac
https://api.example.com/a%2e%2e%2fb
https://api.example.com/./
https://api.example.com/..//
https://api.example.com:0/v1
https://api.example.com:000443/v1
https://api.example.com:65535/v1
https://api.example.com:00065536/v1
https://api.example.com:099999/v1
https://API.EXAMPLE.COM:08080/a/../%7e?
https://api.example.com/v1/?'''.split('\n')
# Labels whose punycode A-label is longer than the RFC 1035 limit.
URLS += ['https://' + 'ü' * 60 + '.example/v1', 'https://' + 'a' * 60 + 'ü.example/v1']
rows = []
for raw in URLS:
    raw = raw.replace('\\t', '\t').replace('\\\\', '\\')
    value, why = stored(raw)
    row = {'input': raw}
    if value is None:
        row['stored'] = None; row['why'] = why
    else:
        row['stored'] = value
        row.update(probe(value))
    rows.append(row)
OUTPUT = Path(__file__).resolve().parents[3] / 'BE_GO/internal/instanceprobe/testdata/requests_url_golden.json'
out = sys.argv[1] if len(sys.argv) > 1 else str(OUTPUT)
json.dump({'requests': requests.__version__, 'urllib3': urllib3.__version__, 'django': django.get_version(), 'cases': rows}, open(out, 'w'), ensure_ascii=True, indent=0)
for r in rows:
    print(repr(r['input'])[:50].ljust(50), '|', r.get('why') or r.get('answer'), '|', r.get('url') or r.get('error', ''), '|', r.get('host_header', ''), '|', r.get('target', ''))
