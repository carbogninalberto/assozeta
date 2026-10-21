"""Declared PBM/PGM/PPM/PFM samples and file-policy expectations.

Pillow must agree with explicit header/pixel outcomes. JPEG expectations are
encoded from declared samples rather than from the file decoder under test.
"""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import warnings

ROOT=Path(__file__).resolve().parents[3]


def vectors():
    from PIL import Image
    cases=[]
    def jpeg(mode,size,pixels,target=None):
        if mode in ('I','F','P'):return None
        if mode=='RGBA':
            pixels=bytes((pixels[i+c]*pixels[i+3]+255*(255-pixels[i+3])+127)//255 for i in range(0,len(pixels),4) for c in range(3));mode='RGB'
        im=Image.frombytes('L' if mode=='1' else mode,size,pixels)
        if mode=='1':im=im.convert('1',dither=Image.Dither.NONE)
        if target is not None:im=im.resize(target,Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,header,payload=b'',*,magic='P6',mode='RGB',w=3,h=2,maxval=255,scale=0,pixels=None,invalid=False,lazy=False):
        data=header+payload;expected_header=None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            assert im.mode==mode and im.size==(w,h) and im.tile[0].offset==len(header),(name,im.mode,im.size,im.tile)
            expected_header=dict(width=w,height=h,mode=mode,magic=magic,maxvalue=maxval,scale=scale,dataoffset=len(header),pixelwarning=w*h>89478485)
        except (OSError,ValueError,SyntaxError,IndexError,Image.DecompressionBombError):
            assert invalid,(name,'header rejected')
        if expected_header is not None and not lazy:
            try:
                im.load();actual=im.convert('L').tobytes() if mode=='1' else im.tobytes()
                assert pixels is not None and actual==pixels,(name,'pixels differ',actual[:80],pixels and pixels[:80])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'pixels rejected')
        target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if len(data)>1024*1024 and not invalid else None
        expected=None if invalid or lazy or pixels is None else jpeg(mode,(w,h),pixels,target)
        actual=None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            ratio=min(200/im.width,200/im.height);size=(int(im.width*ratio),int(im.height*ratio))
            if size!=im.size or len(data)>1024*1024:
                if im.mode=='RGBA':
                    white=Image.new('RGB',im.size,(255,255,255));white.paste(im,mask=im.split()[3]);im=white
                im=im.resize(size,Image.Resampling.LANCZOS) if len(data)>1024*1024 else im.resize(im.size)
                out=io.BytesIO();im.save(out,format='JPEG');actual=out.getvalue()
        except (OSError,ValueError,SyntaxError,IndexError,Image.DecompressionBombError):pass
        assert actual==expected,(name,'file policy differs')
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),lazy=lazy,keep=expected is None,jpeg=enc(expected)))

    # Values above max are clipped in binary PPM, rejected in plain PPM.
    for magic,base,bands in [('P2','L',1),('P3','RGB',3),('P5','L',1),('P6','RGB',3),('PyP','P',1),('PyRGBA','RGBA',4),('PyCMYK','CMYK',4),('P0CMYK','CMYK',4)]:
        for maximum in (1,2,3,7,10,100,254,255,256,510,1000,65534,65535):
            mode='I' if base=='L' and maximum>255 else base
            values=([0,maximum//2,maximum,1,max(0,maximum-1),maximum//3]*bands)
            plain=magic in ('P2','P3')
            payload=(' '.join(str(v) for v in values)+' ').encode() if plain else bytes(values) if maximum<256 else b''.join(struct.pack('>H',v) for v in values)
            outmax=65535 if mode=='I' else 255
            expected=[round(v/maximum*outmax) for v in values]
            pixels=b''.join(struct.pack('<I',v) for v in expected) if mode=='I' else bytes(expected)
            header=f'{magic}\n3 2\n{maximum}\n'.encode()
            add(f'{magic} max={maximum}',header,payload,magic=magic,mode=mode,maxval=maximum,pixels=pixels)
            short=payload.rsplit(b' ',2)[0]+b' ' if plain else payload[:-1]
            add(f'{magic} short last sample max={maximum}',header,short,magic=magic,mode=mode,maxval=maximum)
            if not plain and maximum not in (255,65535):
                high=255 if maximum<256 else 65535
                payload=bytes([high]*(6*bands)) if maximum<256 else struct.pack('>H',high)*(6*bands)
                pixels=struct.pack('<I',65535)*6 if mode=='I' else bytes([255]*(6*bands))
                add(f'{magic} clipped max={maximum}',header,payload,magic=magic,mode=mode,maxval=maximum,pixels=pixels)
    # PBM row packing, inversion, trailing row bits and plain digit adjacency.
    for w in (1,3,7,8,9,17):
        values=[(x+y)%2 for y in range(2) for x in range(w)];pixels=bytes(255*(1-v) for v in values)
        packed=bytearray()
        for y in range(2):
            for x in range(0,w,8):packed.append(sum((values[y*w+x+i] if x+i<w else 1)<<(7-i) for i in range(8)))
        for magic,payload in [('P1',''.join(map(str,values)).encode()),('P4',bytes(packed))]:
            header=f'{magic} {w} 2\n'.encode()
            add(f'{magic} width={w}',header,payload,magic=magic,mode='1',maxval=0,w=w,pixels=pixels)
            add(f'{magic} truncated width={w}',header,payload[:-1],magic=magic,mode='1',maxval=0,w=w)
    for scale in (-1.,1.,-2.5,3.,1e-30):
        words=[0,0x80000000,0x3f800000,0xc0200000,0x7f800000,0x7fc01234]
        # PFM rows are bottom-up. Scale affects byte order, not magnitudes.
        payload=b''.join(struct.pack('<I' if scale<0 else '>I',v) for v in words[3:]+words[:3])
        header=f'Pf\n3 2\n{scale:g}\n'.encode();pixels=b''.join(struct.pack('<I',v) for v in words)
        add(f'PFM scale={scale}',header,payload,magic='Pf',mode='F',maxval=0,scale=scale,pixels=pixels)
        add(f'PFM truncated scale={scale}',header,payload[:-1],magic='Pf',mode='F',maxval=0,scale=scale)
    # Python numeric syntax and header comment/whitespace semantics.
    for space in (b' ',b'\t',b'\n',b'\r',b'\v',b'\f'):
        header=b'P5'+space+b'+3'+space+b'0_2'+space+b'2_55'+space
        add(f'header separator {space!r}',header,bytes(range(6)),magic='P5',mode='L',pixels=bytes(range(6)))
    for header in (b'P5\n# a\n3 # b\r2\n255\n',b'P5 0#join\n3 2 25#join\n5\n',b'P5 0000000003 2 255\n'):
        add(f'header syntax {header!r}',header,bytes(range(6)),magic='P5',mode='L',pixels=bytes(range(6)))
    for magic,mode in [('PyRGBA','RGBA'),('PyCMYK','CMYK'),('P0CMYK','CMYK')]:
        add(f'six-byte magic {magic} adjacent width',f'{magic}3 2 255\n'.encode(),bytes(range(24)),magic=magic,mode=mode,pixels=bytes(range(24)))
    add('CRLF leaves LF pixel',b'P5 3 2 255\r',b'\nabcde',magic='P5',mode='L',pixels=b'\nabcde')
    add('raw comment after max is pixels',b'P5 3 2 255\n',b'#abcde',magic='P5',mode='L',pixels=b'#abcde')
    for token in (b'0',b'-0',b'-1',b'65536',b'0xff',b'1__2',b'_1',b'1_',b'\x00',b'\xc2\xb2',b'00000000255'):
        add(f'invalid max token {token!r}',b'P5 3 2 '+token+b'\n',invalid=True)
    for token in (b'0',b'-1',b'178956971',b'9999999999',b'00000000003',b'1__2',b'1\x1c',b'\xff'):
        add(f'invalid width {token!r}',b'P5 '+token+b' 2 255\n',invalid=True)
    for token in (b'0',b'-0.0',b'inf',b'-inf',b'nan',b'1e999',b'1e-999',b'0x1p0',b'1_.0',b'1._0',b'.',b'1_'):
        add(f'invalid PFM scale {token!r}',b'Pf 3 2 '+token+b'\n',invalid=True)
    for token,scale in ((b'+1',1.),(b'.5',.5),(b'1.',1.),(b'1_0',10.),(b'-1e-2',-.01)):
        add(f'PFM numeric {token!r}',b'Pf 3 2 '+token+b'\n',bytes(24),magic='Pf',mode='F',maxval=0,scale=scale,pixels=bytes(24))
    for magic in (b'P0',b'P7',b'PF',b'P2#x',b'PyRGB',b'P0CMY',b'P9',b'p5',b'P'):
        add(f'bad magic {magic!r}',magic+b' 3 2 255\n',invalid=True)
    header=b'P6\n3 2\n255\n'
    for n in range(8):add(f'short header {n}',header[:n],invalid=True)
    for n,maximum in ((8,2),(9,25),(10,255)):add(f'max at EOF {n}',header[:n],maxval=maximum)
    for magic,mode,maxval,ending in [('P1','1',0,b''),('P4','1',0,b''),('P5','L',255,b' 255'),('P6','RGB',255,b' 255'),('Pf','F',0,b' -1')]:
        add(f'lazy no pixels {magic}',magic.encode()+b' 200 100'+ending,magic=magic,mode=mode,maxval=maxval,scale=-1 if magic=='Pf' else 0,w=200,h=100,lazy=True)
    add('pixel warning header',b'P5 89478486 1 255\n',magic='P5',mode='L',w=89478486,h=1)
    add('hard pixel limit header',b'P5 178956970 1 255\n',magic='P5',mode='L',w=178956970,h=1)
    # Explicit plain-data successes/failures and block-boundary validation.
    for name,payload,pixels in [
        ('joined comment',b'4#comment\n2 ',b'*'),('unterminated comment',b'4#comment',b'\4'),
        ('signed underscore',b'+0_42 ',b'*'),('negative zero',b'-0 ',b'\0'),
        ('negative',b'-1 ',None),('above max',b'256 ',None),('hex',b'0x2a ',None),
        ('negative bytes',b'\xff ',None),('adjacent comments',b'4#a\r#b\n2 ',b'*'),
        ('surplus complete invalid',b'42 x ',b'*'),('surplus partial invalid',b'42 x',b'*'),
        ('surplus partial too long',b'42 12345678901',None),('surplus complete too long',b'42 12345678901 ',b'*'),
        ('long only token',b'00000000042 ',None),('empty',b'',None),('empty comment',b'#',None),
        ('EOF token',b'42',b'*'),('comment-only tail',b'42 #x',b'*'),
    ]:add('plain '+name,b'P2 1 1 255\n',payload,magic='P2',mode='L',w=1,h=1,pixels=pixels)
    for payload,pixels in [(b'0x',None),(b'0 x ',None),(b'0101',b'\xff'),(b'0#x',b'\xff'),(b'0#x\n1',b'\xff'),(b'',None)]:
        add(f'PBM trailing {payload!r}',b'P1 1 1\n',payload,magic='P1',mode='1',maxval=0,w=1,h=1,pixels=pixels)
    block=1024*1024
    for delta in (-1,0,1):
        payload=b'0'+b' '*(block-1+delta)+b'x'
        add(f'PBM trailing invalid block {delta}',b'P1 1 1\n',payload,magic='P1',mode='1',maxval=0,w=1,h=1,pixels=None if delta<0 else b'\xff')
        payload=b'42 '+b' '*(block-14+delta)+b'12345678901'
        add(f'plain last long token block {delta}',b'P2 1 1 255\n',payload,magic='P2',mode='L',w=1,h=1,pixels=None if delta<=0 else b'*')
    for count in (block-1,block,block+1,2*block+1):
        payload=b'4#'+b'x'*count+b'\n2 '
        add(f'comment spans {count}',b'P2 1 1 255\n',payload,magic='P2',mode='L',w=1,h=1,pixels=b'*')
    # Pillow's comment-end helper picks the later terminator if the other is
    # at index zero of a continuation block (min/max condition uses a*b > 0).
    for first,later in ((b'\n',b'\r'),(b'\r',b'\n')):
        payload=b'4#'+b'x'*(block-2)+first+b'2'+later+b'3 '
        add(f'comment terminator at block zero {first!r}',b'P2 1 1 255\n',payload,magic='P2',mode='L',w=1,h=1,pixels=b'+')
    for start in (block-2,block-1,block,block+1):
        payload=b' '*start+b'42 '
        add(f'token boundary {start}',b'P2 1 1 255\n',payload,magic='P2',mode='L',w=1,h=1,pixels=b'*')
    # Both sides of the file-byte threshold, including mode-1 nearest resizing.
    for magic,mode,header,payload,pixels in [('P6','RGB',b'P6 3 2 255\n',bytes(range(18)),bytes(range(18))),('P1','1',b'P1 3 2\n',b'010101',b'\xff\0\xff\0\xff\0')]:
        for length in (block,block+1):
            pad=b' ' if magic=='P1' else b'\0'
            add(f'file threshold {magic}/{length}',header,payload+pad*(length-len(header)-len(payload)),magic=magic,mode=mode,maxval=0 if mode=='1' else 255,pixels=pixels)
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'ppm-codec';build_codec(codec,log,tool_env)
    fault=work/'ppm-fault';build_fault_codec(fault,log,tool_env)
    path=work/'ppm.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestPPM'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_PPM_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'PPM oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-ppm-',dir='/tmp') as directory:run(Path(directory),None,env)
