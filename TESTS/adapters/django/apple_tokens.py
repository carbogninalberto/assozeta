"""Real Apple verifier protocol comparison using ephemeral RSA and local HTTPS.

Only the provider client ID, keys URL and clock are substituted in Django.
Requests, JSON/JWK parsing, RSA verification and JWT claim validation are real.
No customer account, developer .env or external Apple request is involved.
"""
import datetime
import base64
import gzip
import importlib.util
import ipaddress
import json
import logging
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'BE'))
NOW=1700000000


def main():
    import jwt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa,padding
    from cryptography.x509.oid import NameOID
    output=ROOT/'quality-reports/go-migration/apple-tokens'
    output.mkdir(parents=True,exist_ok=True)
    report={'success':False,'phase':'started'}
    (output/'results.json').write_text(json.dumps(report)+'\n')
    spec=importlib.util.spec_from_file_location('actual_social_auth',ROOT/'BE/application/services/social_auth_service.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.logger.disabled=True
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    jwk={**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())),'kid':'fixture'}
    other_jwk={**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(other.public_key())),'kid':'rotated'}
    base={'iss':'https://appleid.apple.com','aud':'client','iat':NOW-100,'exp':NOW+100,'sub':'subject','email':'person@example.test','email_verified':'true'}
    expected={'email':'person@example.test','email_verified':'true','sub':'subject','first_name':'','last_name':''}
    cases=[];unset=object()
    def add(name,valid=False,*,claims=None,remove=(),audience='client',token=unset,signing=key,algorithm='RS256',kid='fixture',keys=None,body=None,status=200,compressed=False,redirect=False,partial=False,info=None):
        payload={**base,**(claims or {})}
        for field in remove:payload.pop(field,None)
        if token is unset:
            token=jwt.api_jws.encode(json.dumps(payload).encode(),signing,algorithm=algorithm,headers={} if kid is None else {'kid':kid})
        document={'keys':[jwk]} if keys is None else {'keys':keys}
        cases.append(dict(name=name,token=token,audience=audience,valid=valid,expected={**expected,**(info or {})} if valid else None,
            body=json.dumps(document) if body is None else body,status=status,compressed=compressed,redirect=redirect,partial=partial,
            calls=(2 if redirect else 1) if audience else 0))
    add('valid',True)
    add('expired boundary',claims={'exp':NOW})
    add('next second valid',True,claims={'exp':NOW+1})
    add('fractional expiry truncates',claims={'exp':NOW+0.9})
    add('string expiry',True,claims={'exp':str(NOW+1)})
    add('missing expiry accepted',True,remove=('exp',))
    add('missing issued-at accepted',True,remove=('iat',))
    add('future issued-at',claims={'iat':NOW+1})
    add('future not-before',claims={'nbf':NOW+1})
    add('fractional issued-at',True,claims={'iat':NOW+0.9})
    add('null issued-at',claims={'iat':None})
    add('boolean issued-at',True,claims={'iat':True})
    add('unicode time digits',True,claims={'iat':'١٧٠٠٠٠٠٠٠٠'})
    add('underscore time string',True,claims={'iat':'+1_700_000_000'})
    add('null subject',claims={'sub':None})
    add('numeric subject',claims={'sub':17})
    add('missing subject accepted',True,remove=('sub',),info={'sub':None})
    add('numeric JWT ID',claims={'jti':17})
    add('null JWT ID',claims={'jti':None})
    add('string JWT ID',True,claims={'jti':'known'})
    add('wrong issuer',claims={'iss':'https://wrong.example.test'})
    add('missing issuer',remove=('iss',))
    add('issuer array',claims={'iss':['https://appleid.apple.com']})
    add('wrong audience',claims={'aud':'other'})
    add('missing audience',remove=('aud',))
    add('audience array',True,claims={'aud':['other','client']})
    add('mixed audience array',claims={'aud':[17,'client']})
    add('configured audience array',True,audience=['other','client'])
    add('configured audience dictionary',True,audience={'client':True})
    add('configured audience number',audience=17)
    for name,value in [('null',None),('empty string',''),('empty list',[]),('empty dictionary',{}),('false',False),('zero',0)]:
        add('disabled '+name,audience=value)
    add('missing email retained',True,remove=('email',),info={'email':None})
    add('numeric email retained',True,claims={'email':17},info={'email':17})
    add('unverified email retained',True,claims={'email_verified':False},info={'email_verified':False})
    add('missing email verification',True,remove=('email_verified',),info={'email_verified':False})
    add('null email verification retained',True,claims={'email_verified':None},info={'email_verified':None})
    add('wrong signature',signing=other)
    add('HMAC rejected',signing=b'synthetic-test-only-signing-value-123456789',algorithm='HS256')
    add('RS384 rejected',algorithm='RS384')
    add('malformed token',token='not.a.jwt')
    add('null token',token=None)
    add('number token',token=17)
    add('object token',token={})
    add('unknown key',kid='unknown')
    add('missing header key',kid=None)
    add('rotated key',True,signing=other,kid='rotated',keys=[other_jwk])
    add('first duplicate key wins',keys=[{**other_jwk,'kid':'fixture'},jwk])
    add('unrelated key skipped',True,keys=[other_jwk,jwk])
    add('malformed earlier key stops search',keys=[None,jwk])
    add('missing earlier key ID stops search',keys=[{'kty':'RSA'},jwk])
    add('wrong key type',keys=[{**jwk,'kty':'EC'}])
    add('missing modulus',keys=[{k:v for k,v in jwk.items() if k!='n'}])
    add('invalid private key',keys=[{**jwk,'d':'invalid'}])
    add('malformed key response',body='not JSON')
    add('nonobject key response',body='[]')
    add('empty key list',keys=[])
    add('HTTP status does not replace JSON verification',True,status=500)
    add('compressed key response',True,compressed=True)
    add('redirected key response',True,redirect=True)
    add('partial body',partial=True)
    add('absent kid matches null key ID',True,kid=None,keys=[{**jwk,'kid':None}])
    add('JWK algorithm metadata ignored',True,keys=[{**jwk,'alg':'RS384'}])
    def signed_raw(header,payload,padded=False):
        encode=lambda value:base64.urlsafe_b64encode(value) if padded else base64.urlsafe_b64encode(value).rstrip(b'=')
        message=encode(header)+b'.'+encode(payload)
        return (message+b'.'+encode(key.sign(message,padding.PKCS1v15(),hashes.SHA256()))).decode()
    header={'alg':'RS256','kid':'fixture','typ':'JWT'}
    for name,extra,valid in [
        ('unknown critical extension',{'crit':['other'],'other':True},False),
        ('empty critical extensions',{'crit':[]},False),
        ('nonlist critical extensions',{'crit':'b64'},False),
        ('missing critical extension',{'crit':['b64']},False),
        ('mixed critical extensions',{'crit':['b64',17],'b64':True},False),
        ('standard critical base64',{'crit':['b64'],'b64':True},True),
        ('detached payload unsupported',{'crit':['b64'],'b64':False},False),
        ('null key ID',{'kid':None},False),
        ('numeric key ID',{'kid':17},False),
    ]:
        add(name,valid,token=signed_raw(json.dumps({**header,**extra}).encode(),json.dumps(base).encode()))
    add('trailing payload JSON',token=signed_raw(json.dumps(header).encode(),json.dumps(base).encode()+b'{}'))
    add('trailing header JSON',token=signed_raw(json.dumps(header).encode()+b'{}',json.dumps(base).encode()))
    add('payload whitespace',True,token=signed_raw(json.dumps(header).encode(),b' \n'+json.dumps(base).encode()+b'\t'))
    add('padded token segments',True,token=signed_raw(json.dumps(header).encode(),json.dumps(base).encode(),True))
    add('duplicate audience last wins',True,token=signed_raw(json.dumps(header).encode(),b'{"aud":"wrong",'+json.dumps(base).encode()[1:]))
    add('empty audience array',claims={'aud':[]})
    add('decimal time string rejected',claims={'iat':str(NOW)+'.0'})
    add('large integer expiry',True,claims={'exp':10**60})
    add('large float expiry',True,claims={'exp':1e100})
    add('time string digit limit',claims={'exp':'9'*4301})
    add('unknown integer digit limit',token=signed_raw(json.dumps(header).encode(),b'{"unused":'+b'9'*4301+b','+json.dumps(base).encode()[1:]))
    add('integer string record separator',claims={'iat':'\x1c'+str(NOW)})
    add('integer string unit separator',claims={'iat':str(NOW)+'\x1f'})
    assert len(cases)==89,len(cases)

    class FrozenDateTime(datetime.datetime):
        @classmethod
        def now(cls,tz=None):return datetime.datetime.fromtimestamp(NOW+0.5,tz)

    with tempfile.TemporaryDirectory(prefix='az-apple-') as directory:
        work=Path(directory)
        tls_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
        cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(tls_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=1)).not_valid_after(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)).add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(tls_key,hashes.SHA256())
        cert_path=work/'certificate.pem';cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path=work/'https-key.pem';key_path.write_bytes(tls_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));key_path.chmod(0o600)
        calls=[0]*len(cases);lock=threading.Lock()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                parts=self.path.split('/')
                if len(parts)!=3 or parts[1] not in ('case','resolved'):
                    self.send_error(404);return
                index=int(parts[2]);case=cases[index]
                assert not self.headers.get('Authorization') and '?' not in self.path
                with lock:calls[index]+=1
                if case['redirect'] and parts[1]=='case':
                    self.send_response(302);self.send_header('Location',f'/resolved/{index}');self.end_headers();return
                data=case['body'].encode()
                if case['compressed']:data=gzip.compress(data)
                self.send_response(case['status']);self.send_header('Content-Type','application/json')
                if case['compressed']:self.send_header('Content-Encoding','gzip')
                self.send_header('Content-Length',str(len(data)+(10 if case['partial'] else 0)));self.end_headers()
                self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert_path,key_path);server.socket=tls.wrap_socket(server.socket,server_side=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'https://127.0.0.1:{server.server_port}'
        fixture=work/'cases.json';fixture.write_text(json.dumps({'now':NOW,'origin':origin,'cases':cases}));fixture.chmod(0o600)
        tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
        try:
            with patch.dict(os.environ,{**tool_env,'REQUESTS_CA_BUNDLE':str(cert_path),'NO_PROXY':'127.0.0.1'},clear=True),patch('jwt.api_jwt.datetime',FrozenDateTime):
                for index,case in enumerate(cases):
                    with patch.object(module,'provider_client_id',return_value=case['audience']),patch.object(module.SocialAuthService,'APPLE_KEYS_URL',origin+f'/case/{index}'):
                        valid,info=module.SocialAuthService.verify_apple_token(case['token'])
                    assert (valid,info)==(case['valid'],case['expected']),(case['name'],'Django outcome')
                    assert calls[index]==case['calls'],(case['name'],'Django requests',calls[index])
            print(f'Django: {len(cases)} real RSA/HTTPS Apple verification cases passed (PyJWT {jwt.__version__})',flush=True)
            with lock:calls[:]=[0]*len(cases)
            subprocess.run(['go','test','-race','-timeout=3m','-tags=appleintegration','./pkg/social','-run','^TestAppleHTTPSInterop$'],cwd=ROOT/'BE_GO',check=True,timeout=210,
                env={**tool_env,'ASSOZETA_APPLE_FIXTURE':str(fixture),'ASSOZETA_APPLE_CA':str(cert_path)})
            assert calls==[c['calls'] for c in cases],'Go provider request counts differ'
            report={'success':True,'cases':len(cases),'pyjwt':jwt.__version__,'transport':'local HTTPS with ephemeral trusted certificate','crypto':'real RSA signatures and JWK parsing','production_credentials':'none','apple_remote_login':'not exercised'}
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
            (output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
