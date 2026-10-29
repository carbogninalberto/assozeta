"""Authored PIXAR raw RGB samples, header errors and lazy file policy."""
import base64
import contextlib
import io
import json
import os
import struct
import subprocess
import tempfile
import warnings
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
from PIL import Image

def pixar(w,h,pixels=b'',mode=(14,2)):
    b=bytearray(1024);b[:4]=b'\x80\xe8\0\0'
    struct.pack_into('<HH',b,416,h,w);struct.pack_into('<HH',b,424,*mode)
    return bytes(b)+pixels

def vectors():
    from TESTS.adapters.django.login_gif import helper
    compress=helper();cases=[]
    def add(name,data,*,size=(1,1),pixels=None,invalid=False,terminal=False,lazy=False):
        header=None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'unexpected header')
            assert im.format=='PIXAR' and im.mode=='RGB' and im.size==size and im.tile[0].offset==1024,(name,'header')
            header={'width':size[0],'height':size[1],'pixelwarning':size[0]*size[1]>89478485}
        except Image.DecompressionBombError:
            assert invalid and terminal,(name,'unexpected bomb')
        except (OSError,SyntaxError,ValueError):
            assert invalid and not terminal,(name,'unexpected header failure')
        if header and not lazy:
            try:
                im.load();assert pixels is not None and im.tobytes()==pixels,(name,'pixels')
            except (OSError,ValueError):
                assert pixels is None,(name,'unexpected decode failure')
        expected=None
        if header and not lazy and pixels is not None:
            w,h=size;target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h)))
            if (target!=size or len(data)>1024*1024) and (len(data)<=1024*1024 or min(target)>0) and (len(data)>1024*1024 or max(size)<=65500):
                im=Image.frombytes('RGB',size,pixels)
                if len(data)>1024*1024:im=im.resize(target,Image.Resampling.LANCZOS)
                out=io.BytesIO();im.save(out,format='JPEG');expected=out.getvalue()
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        uri='opaque,'+enc(data)
        with contextlib.redirect_stdout(io.StringIO()),warnings.catch_warnings():
            warnings.simplefilter('ignore');actual=compress(uri)
        assert actual==(uri if expected is None else 'data:image/jpeg;base64,'+enc(expected)),(name,'file policy')
        cases.append({'name':name,'data':enc(data),'header':header,'terminal':terminal,'pixels':enc(pixels),'keep':expected is None,'jpeg':enc(expected),'lazy':lazy})
    for size in ((1,1),(3,2),(201,1),(1,201),(200,3),(3,200),(200,200),(201,201),(257,19)):
        w,h=size;pixels=bytes((37*i+11)%256 for i in range(w*h*3));data=pixar(w,h,pixels)
        add(f'RGB {size}',data,size=size,pixels=pixels)
        for n in (1024*1024,1024*1024+1):
            add(f'file threshold {size}/{n}',data+bytes(n-len(data)),size=size,pixels=pixels)
    data=pixar(3,2,bytes(range(18)))
    for n in (0,3,4,415,416,417,418,419,420,423,424,425,426,427,428,511,512,1023,1024,1025,1030,1040,1041,1042):
        add(f'truncated {n}',data[:n],size=(3,2),pixels=bytes(range(18)) if n==1042 else None,invalid=n<428)
    for mode in ((0,0),(14,0),(14,1),(14,3),(0,2),(1,2),(15,2),(65535,65535)):
        add(f'unsupported mode {mode}',pixar(1,1,b'\x01\x02\x03',mode),invalid=True)
    for size in ((0,1),(1,0),(0,0),(65535,65535),(12800,14000)):
        add(f'dimensions {size}',pixar(*size),size=size,invalid=True,terminal=all(size))
    for size in ((200,1),(1,200),(200,200)):
        add(f'lazy missing pixels {size}',pixar(*size)[:428],size=size,lazy=True)
    for signature in (b'\x80\xe8\0\1',b'\0\xe8\0\0',b'\x80\0\0\0'):
        add(f'bad signature {signature!r}',signature+data[4:],invalid=True)
    for size in ((256,1),(257,87),(65535,1),(1,65535)):
        w,h=size;pixels=bytes((37*i+11)%256 for i in range(w*h*3))
        add(f'raw RGB boundaries {size}',pixar(w,h,pixels),size=size,pixels=pixels)
    for n in (65535,65536,65537,67076):
        data=pixar(257,87,bytes((37*i+11)%256 for i in range(257*87*3)))
        add(f'truncated raw refill {n}',data[:1024+n],size=(257,87))
    data=pixar(3,2,bytes(range(18)))
    noise=bytearray(data)
    for start,end in ((4,416),(420,424),(428,1024)):
        for i in range(start,end):noise[i]=(37*i+11)%256
    add('ignored header and padding bytes',bytes(noise),size=(3,2),pixels=bytes(range(18)))
    add('trailing raw bytes ignored',data+b'not another frame',size=(3,2),pixels=bytes(range(18)))
    add('unsupported mode before pixel ceiling',pixar(65535,65535,mode=(14,1)),invalid=True)
    return cases


def run(work,log,tool_env):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors();codec=work/'pixar-codec';fault=work/'pixar-fault';path=work/'pixar.json'
    build_codec(codec,log,tool_env);build_fault_codec(fault,log,tool_env)
    path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestPIXAR'],cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_PIXAR_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'PIXAR oracle: {len(cases)} declared header/pixel/file cases',flush=True);return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-pixar-',dir='/tmp') as directory:run(Path(directory),None,env)
