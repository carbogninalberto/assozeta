"""Actual Google service comparison with signed tokens and local HTTPS.

Provider configuration, destination and clock are controlled; Google auth,
requests, certificate parsing, signatures and access-token fallback are real.
"""
import base64
import datetime
import importlib.util
from importlib.metadata import version
import ipaddress
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'BE'))
NOW=1700000000


def main():
    import jwt
    import requests
    from google.auth import _helpers
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa,ec,ed25519
    from cryptography.x509.oid import NameOID
    out=ROOT/'quality-reports/go-migration/google-tokens';out.mkdir(parents=True,exist_ok=True)
    report={'success':False,'phase':'started'}
    (out/'results.json').write_text(json.dumps(report)+'\n')
    spec=importlib.util.spec_from_file_location('actual_social_auth',ROOT/'BE/application/services/social_auth_service.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.logger.disabled=True
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    ec_key=ec.generate_private_key(ec.SECP256R1())
    def public(k):return k.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    def certificate(k):
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'fixture')])
        return x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key()).serial_number(1).not_valid_before(datetime.datetime(2020,1,1)).not_valid_after(datetime.datetime(2021,1,1)).sign(k,hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()
    base={'iss':'accounts.google.com','aud':'client','iat':NOW-10,'exp':NOW+100,'sub':'subject','email':'person@example.test','email_verified':True,'given_name':'Ada','family_name':'Example','picture':'https://example.test/avatar'}
    expected={'email':base['email'],'email_verified':True,'sub':'subject','first_name':'Ada','last_name':'Example','picture':base['picture']}
    cases=[];unset=object()
    def add(name,valid=False,*,error=False,claims=None,remove=(),header=None,token=unset,signing=key,algorithm='RS256',audience='client',certs=None,cert_body=None,cert_status=200,token_info=None,info_status=401,user_info=None,user_status=200,expected_info=None,calls=None,jwks=None,jwk_body=None,jwk_status=200,cert_redirect=False,jwk_redirect=False):
        payload={**base,**(claims or {})}
        for field in remove:payload.pop(field,None)
        if token is unset:
            encode=lambda raw:base64.urlsafe_b64encode(raw).rstrip(b'=')
            message=encode(json.dumps({'alg':algorithm,'typ':'JWT','kid':'fixture',**(header or {})}).encode())+b'.'+encode(json.dumps(payload).encode())
            signer=jwt.algorithms.get_default_algorithms()[algorithm]
            token=(message+b'.'+encode(signer.sign(message,signer.prepare_key(signing)))).decode()
        if calls is None:calls=[] if not audience else ['certs'] if valid or error else ['certs','tokeninfo']
        cases.append(dict(name=name,token=token,audience=audience,valid=valid,error=error,expected={**expected,**(expected_info or {})} if valid else None,
            cert_body=json.dumps({'fixture':public(key)} if certs is None else certs) if cert_body is None else cert_body,cert_status=cert_status,
            token_info={'aud':'client'} if token_info is None else token_info,info_status=info_status,
            user_info=base if user_info is None else user_info,user_status=user_status,calls=calls,
            jwk_body=json.dumps(jwks) if jwk_body is None else jwk_body,jwk_status=jwk_status,cert_redirect=cert_redirect,jwk_redirect=jwk_redirect))
    add('RSA public key',True)
    add('certificate redirect followed',True,cert_redirect=True,calls=['certs','certs-resolved'])
    add('expired certificate is only a key',True,certs={'fixture':certificate(key)})
    add('ECDSA public key',True,signing=ec_key,algorithm='ES256',certs={'fixture':public(ec_key)})
    add('HTTPS issuer',True,claims={'iss':'https://accounts.google.com'})
    add('expiry boundary accepted',True,claims={'exp':NOW})
    add('expired',claims={'exp':NOW-1})
    add('fractional expired',claims={'exp':NOW-0.1})
    add('fractional future issued-at',claims={'iat':NOW+0.1})
    add('future issued-at',claims={'iat':NOW+1})
    add('not-before ignored',True,claims={'nbf':NOW+1000})
    add('missing expiry',remove=('exp',))
    add('missing issued-at',remove=('iat',))
    add('string expiry propagates',error=True,claims={'exp':str(NOW+100)})
    add('null issued-at propagates',error=True,claims={'iat':None})
    add('boolean issued-at',True,claims={'iat':True})
    add('large negative issued-at',True,claims={'iat':-10**60})
    add('wrong issuer propagates',error=True,claims={'iss':'https://wrong.example.test'})
    add('missing issuer propagates',error=True,remove=('iss',))
    add('wrong audience',claims={'aud':'other'})
    add('missing audience',remove=('aud',))
    add('array token audience rejected',claims={'aud':['client']})
    add('array configured audience',True,audience=['other','client'])
    add('map configured audience',True,audience={'client':True})
    add('numeric configured audience propagates',error=True,audience=17)
    add('null subject retained',True,claims={'sub':None},expected_info={'sub':None})
    add('numeric subject retained',True,claims={'sub':17},expected_info={'sub':17})
    add('numeric jti ignored',True,claims={'jti':17})
    add('null email retained',True,claims={'email':None},expected_info={'email':None})
    add('missing optional identity',True,remove=('email','email_verified','given_name','family_name','picture','sub'),expected_info={'email':None,'email_verified':False,'first_name':'','last_name':'','picture':None,'sub':None})
    add('critical metadata ignored by certificate decoder',True,header={'crit':['unknown'],'unknown':True})
    add('missing key ID tries keys',True,header={'kid':None},certs={'other':public(other),'fixture':public(key)})
    add('empty key ID tries keys',True,header={'kid':''})
    add('unknown key ID',header={'kid':'unknown'})
    add('array key ID propagates',error=True,header={'kid':['fixture']})
    add('wrong signature',signing=other)
    add('unsupported algorithm',algorithm='RS384')
    add('HMAC confusion',algorithm='HS256',signing=b'synthetic-key-at-least-32-bytes-123456789')
    add('malformed token',token='not-a-token')
    add('null token falls back',token=None)
    add('numeric token falls back',token=17)
    add('empty certificates',certs={})
    add('invalid certificate',certs={'fixture':'invalid'})
    add('null certificate',certs={'fixture':None})
    add('certificates non-200 propagates',error=True,cert_status=503)
    add('certificates invalid JSON falls back',cert_body='not-json')
    add('certificates scalar propagates',error=True,cert_body='17')
    for value in (None,'',False,0,[],{}):add('disabled '+repr(value),audience=value)
    add('access fallback',True,token='access +/&token',info_status=200,calls=['certs','tokeninfo','userinfo'])
    add('access wrong audience',token='access-token',info_status=200,token_info={'aud':'foreign'})
    add('access array audience rejected',token='access-token',info_status=200,token_info={'aud':['client']})
    add('access info malformed shape',token='access-token',info_status=200,token_info=[])
    add('access userinfo non-200',token='access-token',info_status=200,user_status=503,calls=['certs','tokeninfo','userinfo'])
    add('access userinfo malformed shape',token='access-token',info_status=200,user_info=[],calls=['certs','tokeninfo','userinfo'])
    add('access missing optional identity',True,token='access-token',info_status=200,user_info={},calls=['certs','tokeninfo','userinfo'],expected_info={'email':None,'email_verified':False,'first_name':'','last_name':'','picture':None,'sub':None})
    add('access raw identity values',True,token='access-token',info_status=200,user_info={**base,'email_verified':'false','given_name':None,'sub':17},calls=['certs','tokeninfo','userinfo'],expected_info={'email_verified':'false','first_name':None,'sub':17})
    jwk={**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())),'kid':'fixture'}
    ec_jwk={**json.loads(jwt.algorithms.ECAlgorithm.to_jwk(ec_key.public_key())),'kid':'fixture'}
    def add_jwk(name,valid=False,**kwargs):
        kwargs.setdefault('error',not valid);kwargs.setdefault('calls',['certs','jwks']);kwargs.setdefault('jwks',{'keys':[jwk]})
        add('JWK '+name,valid,certs={'keys':[]},**kwargs)
    add_jwk('RSA',True)
    add_jwk('ECDSA',True,signing=ec_key,algorithm='ES256',jwks={'keys':[ec_jwk]})
    add_jwk('RS384',True,algorithm='RS384',jwks={'keys':[{**jwk,'alg':'RS384'}]})
    add_jwk('PSS',True,algorithm='PS256',jwks={'keys':[{**jwk,'alg':'PS256'}]})
    ed_key=ed25519.Ed25519PrivateKey.generate()
    ed_jwk={**json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(ed_key.public_key())),'kid':'fixture'}
    add_jwk('Ed25519',True,signing=ed_key,algorithm='EdDSA',jwks={'keys':[ed_jwk]})
    secret=b'synthetic-remote-jwk-secret-at-least-32-bytes'
    add_jwk('HMAC from provider key',True,signing=secret,algorithm='HS256',jwks={'keys':[{'kty':'oct','k':base64.urlsafe_b64encode(secret).rstrip(b'=').decode(),'kid':'fixture'}]})
    add_jwk('missing expiry',True,remove=('exp',))
    add_jwk('expiry boundary rejected',claims={'exp':NOW})
    add_jwk('string expiry',True,claims={'exp':str(NOW+1)})
    add_jwk('future not-before',claims={'nbf':NOW+1})
    add_jwk('numeric subject',claims={'sub':17})
    add_jwk('unknown critical header',header={'crit':['unknown'],'unknown':True},calls=['certs'])
    add_jwk('wrong audience',claims={'aud':'other'})
    add_jwk('array audience',True,claims={'aud':['other','client']})
    add_jwk('wrong issuer',claims={'iss':'https://wrong.example.test'})
    add_jwk('wrong signature',signing=other)
    add_jwk('algorithm mismatch',algorithm='RS384')
    add_jwk('numeric key algorithm rejected',jwks={'keys':[{**jwk,'alg':17}]})
    add_jwk('boolean key algorithm rejected',jwks={'keys':[{**jwk,'alg':True}]})
    add_jwk('falsy key algorithm infers RSA',True,jwks={'keys':[{**jwk,'alg':False}]})
    add_jwk('unknown algorithm still fetches keys',header={'alg':'unknown'})
    add_jwk('null algorithm still fetches keys',header={'alg':None})
    add_jwk('unknown kid',header={'kid':'unknown'})
    add_jwk('null kid rejected before fetch',header={'kid':None},calls=['certs'])
    add_jwk('malformed token',token='invalid',calls=['certs'])
    add_jwk('no signing keys',jwks={'keys':[{**jwk,'use':'enc'}]})
    add_jwk('skip malformed key',True,jwks={'keys':[17,{'kty':'unknown'},jwk]})
    add_jwk('empty keys',jwks={'keys':[]})
    add_jwk('non-200',jwk_status=503)
    add_jwk('redirect rejected',jwk_redirect=True)
    add_jwk('invalid JSON falls back',error=False,jwk_body='invalid',calls=['certs','jwks','tokeninfo'])
    add_jwk('duplicate kid picks first',jwks={'keys':[{**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(other.public_key())),'kid':'fixture'},jwk]})
    assert len(cases)==93
    class FrozenDateTime(datetime.datetime):
        @classmethod
        def now(cls,tz=None):return datetime.datetime.fromtimestamp(NOW+0.5,tz)
    with tempfile.TemporaryDirectory(prefix='az-google-') as directory:
        work=Path(directory);tls_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
        cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(tls_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=1)).not_valid_after(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(tls_key,hashes.SHA256())
        cert_path=work/'certificate.pem';cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path=work/'https-key.pem';key_path.write_bytes(tls_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));key_path.chmod(0o600)
        calls=[[] for _ in cases];failures=[];lock=threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                try:self.serve_case()
                except Exception:
                    with lock:failures.append('provider request violated fixture protocol')
                    raise
            def serve_case(self):
                parsed=urlsplit(self.path);parts=parsed.path.split('/');index=int(parts[1]);phase=parts[2];case=cases[index]
                with lock:calls[index].append(phase)
                if phase in ('certs','jwks') and case['cert_redirect' if phase=='certs' else 'jwk_redirect']:
                    self.send_response(302);self.send_header('Location',f'/{index}/{phase}-resolved');self.end_headers();return
                phase=phase.removesuffix('-resolved')
                if phase=='certs':
                    assert not parsed.query and not self.headers.get('Authorization')
                    status=case['cert_status'];data=case['cert_body'].encode()
                elif phase=='jwks':
                    assert not parsed.query and not self.headers.get('Authorization')
                    status=case['jwk_status'];data=case['jwk_body'].encode()
                elif phase=='tokeninfo':
                    token=case['token'];query=parse_qs(parsed.query)
                    expected_query={} if token is None else {'access_token':[str(token)]}
                    assert query==expected_query and not self.headers.get('Authorization')
                    status=case['info_status'];data=json.dumps(case['token_info']).encode()
                else:
                    assert phase=='userinfo' and not parsed.query and self.headers.get('Authorization')=='Bearer '+str(case['token'])
                    status=case['user_status'];data=json.dumps(case['user_info']).encode()
                self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert_path,key_path);server.socket=tls.wrap_socket(server.socket,server_side=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin=f'https://127.0.0.1:{server.server_port}'
        fixture=work/'cases.json';fixture.write_text(json.dumps({'now':NOW,'origin':origin,'cases':cases}));fixture.chmod(0o600)
        tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
        current=[0];real_request=requests.Session.request
        real_open=urllib.request.OpenerDirector.open
        def local_open(opener,fullurl,data=None,timeout=30):
            assert isinstance(fullurl,urllib.request.Request) and fullurl.full_url=='https://www.googleapis.com/oauth2/v1/certs'
            request=urllib.request.Request(origin+f'/{current[0]}/jwks',headers=dict(fullurl.header_items()),method='GET')
            return real_open(opener,request,data,timeout)
        trust=ssl.create_default_context(cafile=str(cert_path))
        def local_request(session,method,url,**kwargs):
            destinations={'https://www.googleapis.com/oauth2/v1/certs':'certs','https://oauth2.googleapis.com/tokeninfo':'tokeninfo','https://www.googleapis.com/oauth2/v3/userinfo':'userinfo'}
            assert method.upper()=='GET' and url in destinations,'unexpected provider destination'
            kwargs['verify']=str(cert_path)
            return real_request(session,method,origin+f'/{current[0]}/'+destinations[url],**kwargs)
        try:
            with patch.dict(os.environ,{**tool_env,'NO_PROXY':'127.0.0.1'},clear=True),patch.object(requests.Session,'request',local_request),patch.object(_helpers,'utcnow',return_value=datetime.datetime.fromtimestamp(NOW,datetime.timezone.utc)),patch.object(urllib.request.OpenerDirector,'open',local_open),patch('ssl._create_default_https_context',return_value=trust),patch('jwt.api_jwt.datetime',FrozenDateTime):
                for index,case in enumerate(cases):
                    current[0]=index;error=False
                    with patch.object(module,'provider_client_id',return_value=case['audience']):
                        try:valid,info=module.SocialAuthService.verify_google_token(case['token'])
                        except Exception:error=True;valid=False;info=None
                    assert (valid,info,error)==(case['valid'],case['expected'],case['error']),(case['name'],'Django outcome',valid,error)
                    assert calls[index]==case['calls'],(case['name'],'Django HTTP sequence',calls[index])
                    assert not failures,'Django provider request violated fixture protocol'
            print(f'Django: {len(cases)} Google RSA/ECDSA/HTTPS cases passed (google-auth {version("google-auth")})',flush=True)
            with lock:calls[:]=[[] for _ in cases]
            subprocess.run(['go','test','-race','-timeout=3m','-tags=googleintegration','./pkg/social','-run','^TestGoogleHTTPSInterop$'],cwd=ROOT/'BE_GO',check=True,timeout=210,env={**tool_env,'ASSOZETA_GOOGLE_FIXTURE':str(fixture),'ASSOZETA_GOOGLE_CA':str(cert_path)})
            assert not failures,'Go provider request violated fixture protocol'
            assert calls==[case['calls'] for case in cases],[(case['name'],actual,case['calls']) for case,actual in zip(cases,calls) if actual!=case['calls']]
            report={'success':True,'cases':len(cases),'google_auth':version('google-auth'),'pyjwt':jwt.__version__,'transport':'local HTTPS','crypto':'real RSA/PSS/ECDSA/Ed25519/HMAC, PEM certificates and JWK','live_google_account':'not exercised','production_credentials':'none'}
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5);(out/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
