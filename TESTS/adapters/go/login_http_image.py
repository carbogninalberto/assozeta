"""Verify the shipped login root starts as non-root and admits pre-credential HTTP.

Networkless, read-only disposable container; no database/provider/customer data.
Successful-login and lifecycle parity require separate evidence.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[3]


def main():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--image',required=True);args=parser.parse_args()
    architecture=subprocess.check_output(['docker','image','inspect',args.image,'--format','{{.Architecture}}'],text=True).strip()
    assert architecture in ('amd64','arm64'),architecture
    name='az-login-image-'+uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='az-login-image-',dir='/tmp') as directory:
        work=Path(directory);probe=work/'probe'
        tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
        subprocess.run(['go','build','-o',str(probe),str(ROOT/'TESTS/adapters/go/testdata/login_http_probe.go')],
                       check=True,timeout=120,env={**tool_env,'CGO_ENABLED':'0','GOOS':'linux','GOARCH':architecture})
        probe.chmod(0o755)
        key=Ed25519PrivateKey.generate()
        import base64
        values={'DBTYPE':'POSTGRES','DBNAME':'contract','DBUSER':'contract','DBPASSWORD':'synthetic-only','DBHOST':'127.0.0.1',
                'REDIS_HOST':'127.0.0.1','REDIS_PASSWORD':'synthetic-only','SECRET_KEY':'synthetic-image-session-only',
                'JWT_SECRET_KEY_B64':base64.b64encode(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())).decode(),
                'JWT_PUBLIC_KEY_B64':base64.b64encode(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)).decode(),
                'DEBUG':'False','RATELIMIT_ENABLE':'False','GO_LISTEN_ADDR':':8000','RUNNING_VERSION':'v0.0.0-contract',
                'CORS_ALLOWED_ORIGINS':'https://allowed.example.test','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':'',
                'AWS_ACCESS_KEY_ID':'synthetic-access','AWS_SECRET_ACCESS_KEY':'synthetic-secret','AWS_STORAGE_BUCKET_NAME':'synthetic-bucket',
                'AWS_S3_REGION_NAME':'us-east-1','AWS_S3_ENDPOINT_URL':'http://127.0.0.1:9','AWS_LOCATION':'private'}
        environment=work/'environment';environment.write_text(''.join(f'{k}={v}\n' for k,v in values.items()));environment.chmod(0o600)
        try:
            subprocess.run(['docker','run','--detach','--name',name,'--network','none','--read-only','--env-file',str(environment),
                            '--mount',f'type=bind,source={probe},target=/contract-probe,readonly',args.image],check=True,capture_output=True,timeout=30)
            uid=subprocess.check_output(['docker','exec',name,'id','-u'],text=True,timeout=10).strip();assert uid=='10001'
            result=subprocess.check_output(['docker','exec',name,'/contract-probe'],text=True,timeout=30)
            record=json.loads(result);assert record['success'] and record['http_cases']==8
            print(json.dumps({**record,'uid':uid,'read_only':True,'network':'none'}),flush=True)
        finally:
            subprocess.run(['docker','rm','--force',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)


if __name__=='__main__':main()
