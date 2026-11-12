"""Declared first-frame GIF samples, palette decisions and login conversion."""
import ast
import base64
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import warnings

ROOT=Path(__file__).resolve().parents[3]


def literal_data(values,bits=8):
    # Clear between samples keeps each code's width explicit and independent
    # of either implementation's dictionary writer.
    codes=[]
    for v in values:codes.extend((1<<bits,v))
    codes.append((1<<bits)+1)
    value=offset=0;raw=bytearray()
    for c in codes:
        value|=c<<offset;offset+=bits+1
        while offset>=8:raw.append(value&255);value>>=8;offset-=8
    if offset:raw.append(value)
    return b''.join(bytes([len(raw[i:i+255])])+raw[i:i+255] for i in range(0,len(raw),255))+b'\0'


def gif(w,h,values=b'',*,screen=None,offset=(0,0),global_palette=None,local_palette=None,interlace=False,bits=8,extensions=b'',stream=None):
    palette=lambda p:b'' if p is None else p
    flags=lambda p:0 if p is None else 128+((len(p)//3).bit_length()-2)
    return (b'GIF89a'+struct.pack('<HHBBB',*(screen or (w,h)),flags(global_palette),0,0)+palette(global_palette)+extensions+
        b','+struct.pack('<HHHHB',*offset,w,h,flags(local_palette)+(64 if interlace else 0))+palette(local_palette)+bytes([bits])+
        (literal_data(values,bits) if stream is None else stream)+b';')


def helper():
    # Compile the actual helper unmodified; its unrelated ORM imports are not
    # needed for this pure image operation. Full accounts also passes it directly.
    source=ast.parse((ROOT/'BE/application/utils/api_utils.py').read_text())
    fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='compress_base64')
    from PIL import Image
    scope=dict(base64=base64,io=io,Image=Image,logger=logging.getLogger('gif-oracle'))
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(ROOT/'BE/application/utils/api_utils.py'),'exec'),scope)
    return scope['compress_base64']


def vectors():
    from PIL import Image
    cases=[]
    def add(name,data,*,size=(3,2),mode='L',pixels=None,invalid=False,lazy=False,comment=None):
        header=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid and im.format=='GIF' and im.size==size and im.mode==mode,(name,im.format,im.size,im.mode)
            t=im.tile[0]
            assert im.info.get('comment')==comment,(name,'comment')
            header=dict(width=size[0],height=size[1],mode=mode,dataoffset=t.offset,left=t.extents[0],top=t.extents[1],right=t.extents[2],bottom=t.extents[3],bits=t.args[0],interlace=bool(t.args[1]),comment=base64.b64encode(comment).decode() if comment else None,transparency=im.info.get('transparency',-1),paletteattached=im.palette is not None)
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,Image.DecompressionBombError):assert invalid,(name,'unexpected header failure')
        if header and not lazy:
            try:
                im.load();assert pixels is not None and im.tobytes()==pixels,(name,'pixel mismatch',im.tobytes()[:40],pixels and pixels[:40])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'unexpected decode failure')
        encoded=None
        if pixels is not None and mode=='L' and not lazy and not header['paletteattached']:
            target=(int(size[0]*min(200/size[0],200/size[1])),int(size[1]*min(200/size[0],200/size[1])))
            if target!=size or len(data)>1024*1024:
                try:
                    im=Image.frombytes('L',size,pixels)
                    im=im.resize(target if len(data)>1024*1024 else size,Image.Resampling.LANCZOS)
                    out=io.BytesIO();im.save(out,format='JPEG',comment=comment or b'');encoded=out.getvalue()
                except (OSError,ValueError):pass
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name,data=enc(data),header=header,pixels=enc(pixels),lazy=lazy,keep=encoded is None,jpeg=enc(encoded)))
    raw=bytes([0,1,2,3,4,255]);identity=bytes(v for i in range(256) for v in (i,i,i));color=bytes([255,0,0,0,255,0])
    for palette in (None,identity,color):
        for local in (None,identity,color):
            mode='P' if (local if local is not None else palette)==color else 'L'
            add(f'palette global={palette==color}/{palette is None} local={local==color}/{local is None}',gif(3,2,raw,global_palette=palette,local_palette=local),mode=mode,pixels=raw)
    for v in range(256):add(f'literal {v}',gif(1,1,bytes([v])),size=(1,1),pixels=bytes([v]))
    for bits in range(13):
        p=bytes([0,min((1<<bits)-1,255),0,0,0,0]);add(f'code width {bits}',gif(3,2,p,bits=bits),pixels=p)
    for bits in range(13,256):add(f'invalid code width {bits}',gif(3,2,bits=bits,stream=b'\x01\x00\x00'))
    for h in range(1,18):
        w=3;p=bytes((i*17)%256 for i in range(w*h));rows=[y for start,step in ((0,8),(4,8),(2,4),(1,2)) for y in range(start,h,step)]
        add(f'interlace {h}',gif(w,h,b''.join(p[y*w:(y+1)*w] for y in rows),interlace=True),size=(w,h),pixels=p)
    for screen in ((1,1),(5,4)):
        w,h=max(screen[0],4),max(screen[1],3);p=bytearray(w*h)
        for y in range(2):p[(y+1)*w+1:(y+1)*w+4]=raw[y*3:y*3+3]
        add(f'offset and canvas {screen}',gif(3,2,raw,screen=screen,offset=(1,1)),size=(w,h),pixels=bytes(p))
        ext=b'!\xf9\x04\x01\0\0\x07\0';p=bytearray([7])*(w*h)
        for y in range(2):p[(y+1)*w+1:(y+1)*w+4]=raw[y*3:y*3+3]
        add(f'transparent canvas {screen}',gif(3,2,raw,screen=screen,offset=(1,1),extensions=ext),size=(w,h),pixels=bytes(p))
    for ext,comment in ((b'!\xfe\x03abc\x02de\0',b'abcde'),(b'!\xfe\x01a\0!\xfe\x01b\0',b'a\nb'),(b'!\xfe\0',None),(b'!\xff\x0bNETSCAPE2.0\x03\x01\0\0\0',None),(b'!\x01\x01x\0',None)):
        add(f'extension {ext!r}',gif(3,2,raw,extensions=ext),pixels=raw,comment=comment)
    data=gif(3,2,raw)
    for n in range(len(data)):add(f'truncated {n}',data[:n],invalid=n<24,pixels=raw if n>=len(data)-2 else None)
    add('lazy truncated data',gif(200,100,stream=b'')[:-1],size=(200,100),lazy=True)
    add('colored invalid data',gif(3,2,global_palette=color,stream=b'\x01\xff'),mode='P')
    for n in (1024*1024,1024*1024+1):add(f'byte threshold {n}',data+b'\0'*(n-len(data)),pixels=raw)
    for w,h in ((0,0),(0,1),(1,0)):
        add(f'zero tile {w}/{h}',gif(w,h,screen=(3,2)),pixels=None)
    add('zero canvas',gif(0,0),invalid=True)
    add('first frame only',data[:-1]+gif(1,1,b'\xff')[13:],pixels=raw)
    def codes_stream(codes,width=3):
        value=sum(c << (i*width) for i,c in enumerate(codes))
        raw=value.to_bytes((len(codes)*width+7)//8,'little')
        return bytes([len(raw)])+raw+b'\0'
    for name,codes,expected in (
        ('self reference',[4,0,6],b'\0'*3),
        ('without initial clear',[0,1,2],bytes([0,1,2])),
        ('repeated clear',[4,4,4,0,4,1,4,2],bytes([0,1,2])),
        ('invalid first dictionary code',[4,6],None),
        ('invalid later dictionary code',[4,0,7],None),
        ('premature end',[4,0,5],None),
        ('missing end after complete row',[4,0,4,1,4,2],bytes([0,1,2])),
    ):add(name,gif(3,1,bits=2,stream=codes_stream(codes)),size=(3,1),pixels=expected)
    ended=gif(3,1,bits=2,stream=codes_stream([4,0,5]))
    for n in (24+65536,24+65537):
        add(f'premature end refill {n}',ended+b'\0'*(n-len(ended)),size=(3,1),pixels=None if n==24+65536 else b'\0'*3)
    full=literal_data(raw)
    for n in (1,2,3,4,5,255):
        encoded=full[1:-1]
        split=b''.join(bytes([len(encoded[i:i+n])])+encoded[i:i+n] for i in range(0,len(encoded),n))+b'\0'
        add(f'subblock split {n}',gif(3,2,stream=split),pixels=raw)
    add('empty subblock before data',gif(3,2,stream=b'\0'+full),pixels=raw)
    add('declared short final block',gif(3,2,stream=bytes([255])+full[1:-1]),pixels=None)
    add('garbage after complete row',gif(3,2,stream=full+b'\xff'),pixels=raw)
    for length in (0,1,2,3,4):
        extension=b'!\xf9'+bytes([length])+b'\1'*length+b'\0'
        add(f'short control extension {length}',gif(3,2,raw,extensions=extension),invalid=length in (1,2,3),pixels=raw if length in (0,4) else None)
    for label in (0,1,249,254,255):
        add(f'short extension {label}',b'GIF89a'+struct.pack('<HHBBB',3,2,0,0,0)+bytes([33,label]),invalid=True)
    add('unknown bytes before image',gif(3,2,raw,extensions=b'junk'),pixels=raw)
    for version in (b'GIF87a',b'GIF89a',b'GIF90a'):
        add(f'version {version!r}',version+data[6:],invalid=version==b'GIF90a',pixels=raw if version!=b'GIF90a' else None)
    for comment_len in (65533,65534):
        comment=b'x'*comment_len
        ext=b'!\xfe'+b''.join(bytes([len(comment[i:i+255])])+comment[i:i+255] for i in range(0,len(comment),255))+b'\0'
        add(f'comment JPEG limit {comment_len}',gif(3,2,raw,extensions=ext),pixels=raw,comment=comment)
    # Pillow encoder exercises dictionary growth/reset and long prefix chains.
    for w,h in ((1,1),(17,19),(256,129),(400,300)):
        for kind in ('constant','gradient','noise'):
            state=7;p=bytearray()
            for i in range(w*h):
                state=(state*1664525+1013904223)&0xffffffff
                p.append(91 if kind=='constant' else i%256 if kind=='gradient' else state>>24)
            im=Image.frombytes('L',(w,h),bytes(p));out=io.BytesIO();im.save(out,format='GIF',optimize=False)
            add(f'encoded dictionary {w}/{h}/{kind}',out.getvalue(),size=(w,h),pixels=bytes(p))
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors();compress=django_helper or helper()
    with contextlib.redirect_stdout(log):
        for case in cases:
            uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
            assert compress(uri)==expected,case['name']
    codec=work/'gif-codec';build_codec(codec,log,tool_env)
    fault=work/'gif-fault';build_fault_codec(fault,log,tool_env)
    path=work/'gif.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestGIF'],cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_GIF_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'GIF oracle: {len(cases)} declared header/pixel/file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-gif-',dir='/tmp') as directory:run(Path(directory),None,env)
