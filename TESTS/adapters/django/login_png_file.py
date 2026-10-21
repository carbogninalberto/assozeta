"""Explicit whole-PNG output/fallback cases, including tail metadata and APNG.

Expected JPEG pixels come from declared samples/canvas placement. Pillow's file
path and the actual Django helper must independently agree with those outcomes.
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
import zlib

ROOT=Path(__file__).resolve().parents[3]


def vectors():
    from PIL import Image
    from TESTS.adapters.django.login_png_pixels import chunk, file_bytes, MODES
    from TESTS.adapters.django.login_png_header import vectors as headers, frame
    from TESTS.adapters.django.login_png_stream import vectors as streams
    from TESTS.adapters.django.login_bilevel import vectors as bilevel
    cases=[]
    def jpeg(mode,size,pixels,comment=None,target=None):
        if mode not in ('1','L','RGB','RGBA'):return None
        if mode=='RGBA':
            pixels=bytes((pixels[i+c]*pixels[i+3]+255*(255-pixels[i+3])+127)//255 for i in range(0,len(pixels),4) for c in range(3))
            mode='RGB';comment=None
        image=Image.frombytes('L' if mode=='1' else mode,size,pixels)
        if mode=='1':image=image.convert('1',dither=Image.Dither.NONE)
        if comment:image.info['comment']=comment
        if target is not None:image=image.resize(target,Image.Resampling.LANCZOS)
        output=io.BytesIO();image.save(output,format='JPEG');return output.getvalue()
    def reference(data):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore');image=Image.open(io.BytesIO(data))
            ratio=min(200/image.width,200/image.height);target=(int(image.width*ratio),int(image.height*ratio))
            if target==image.size and len(data)<=1024*1024:return None
            if image.mode=='RGBA':
                white=Image.new('RGB',image.size,(255,255,255));white.paste(image,mask=image.split()[3]);image=white
            resized=image.resize(target,Image.Resampling.LANCZOS) if len(data)>1024*1024 else image.resize(image.size)
            output=io.BytesIO();resized.save(output,format='JPEG');return output.getvalue()
        except (OSError,ValueError,SyntaxError,TypeError,IndexError,struct.error):return None
    def add(name,data,expected):
        actual=reference(data)
        assert actual==expected,(name,'whole-file Pillow outcome differs')
        cases.append(dict(name=name,data=base64.b64encode(data).decode(),keep=expected is None,jpeg=base64.b64encode(expected).decode() if expected is not None else None))
    for case in streams():
        spec=case['spec'];data=base64.b64decode(case['data']);expected=None
        assert len(data)<=1024*1024
        if not case['invalid'] and not case['post_invalid']:
            expected=jpeg(case['mode'],(spec['width'],spec['height']),base64.b64decode(case['expected']))
        add('stream '+case['name'],data,expected)
    lazy=0
    for case in headers():
        data=base64.b64decode(case['data'])
        if not case['invalid'] and (case['width'],case['height'])==(200,100) and len(data)<=1024*1024:
            add('lazy '+case['name'],data,None);lazy+=1
    assert lazy==235,lazy
    for case in bilevel()['policy']:add('bilevel '+case['name'],base64.b64decode(case['data']),base64.b64decode(case['jpeg']) if case['jpeg'] else None)
    spec=dict(width=3,height=2,depth=8,color=0,interlace=False)
    pixels=b'\1\2\3\4\5\6';compressed=zlib.compress(b'\0\1\2\3\0\4\5\6')
    prefix=file_bytes(spec,b'')[:33];idat=chunk(b'IDAT',compressed);iend=chunk(b'IEND',b'')
    ordinary=jpeg('L',(3,2),pixels)
    def tail(name,payload,expected=ordinary,before=b''):
        add(name,prefix+before+idat+payload,expected)
    for n in range(9):tail(f'partial next header {n}',(struct.pack('>I',100)+b'tEXt')[:n],None if n==8 else ordinary)
    for n in range(5):add(f'partial pixel CRC {n}',prefix+idat[:-4]+idat[-4:][:n],ordinary)
    tail('invalid next chunk name',struct.pack('>I',100)+b'bad!')
    tail('IEND body not required',struct.pack('>I',0xffffffff)+b'IEND')
    tail('unknown chunk short',struct.pack('>I',100)+b'abCDx',None)
    tail('unknown chunk complete',chunk(b'abCD',b'x')+iend)
    for name,body,value in [('latin1',b'comment\0caf\xe9','caf\xe9'.encode('utf8')),('empty',b'comment\0',None),('nul',b'comment\0a\0b',b'a\0b')]:
        expected=jpeg('L',(3,2),pixels,value)
        tail('tail text '+name,chunk(b'tEXt',body)+iend,expected,before=chunk(b'tEXt',b'comment\0before'))
        broken=bytearray(chunk(b'tEXt',body));broken[-1]^=255
        tail('tail text ignored CRC '+name,bytes(broken)+iend,expected)
    for name,kind,body,value,invalid in [
        ('ztxt',b'zTXt',b'comment\0\0'+zlib.compress(b'after'),b'after',False),
        ('ztxt bad stream',b'zTXt',b'comment\0\0bad',None,False),
        ('ztxt bad method',b'zTXt',b'comment\0\1bad',None,True),
        ('itxt',b'iTXt',b'comment\0\0\0en\0name\0'+ '\u03a9'.encode(), '\u03a9'.encode(),False),
        ('itxt malformed unicode',b'iTXt',b'comment\0\0\0\0\0\xff',b'before',False),
        ('icc bad method',b'iCCP',b'profile\0\1bad',None,True),
        ('icc bad stream',b'iCCP',b'profile\0\0bad',b'before',False),
    ]:tail(name,chunk(kind,body)+iend,None if invalid else jpeg('L',(3,2),pixels,value),before=chunk(b'tEXt',b'comment\0before'))
    for kind,minimum in [(b'IHDR',13),(b'tRNS',2),(b'gAMA',4),(b'sRGB',1),(b'pHYs',9),(b'acTL',8),(b'fcTL',26)]:
        for n in range(minimum):tail(f'tail short {kind!r} {n}',chunk(kind,bytes(n))+iend,None)
    for n in range(9):tail(f'tail cHRM {n}',chunk(b'cHRM',bytes(n))+iend,None if n%4 else ordinary)
    for kind in (b'PLTE',b'eXIf',b'tEXt',b'iTXt'):
        tail(f'empty tail {kind!r}',chunk(kind,b'')+iend)
    # Later IHDR updates the parser's mode/size, not the already-created image.
    rgba_header=struct.pack('>IIBBBBB',99,88,8,6,0,0,0)
    tail('tail IHDR changes parser mode only',chunk(b'IHDR',rgba_header)+chunk(b'tRNS',b'')+iend)
    tail('tail fcTL uses later IHDR bounds',chunk(b'IHDR',rgba_header)+chunk(b'fcTL',frame(w=99,h=88))+iend)
    tail('tail invalid fdAT initial sequence',chunk(b'fdAT',struct.pack('>I',0))+iend,None)
    for n in range(4):tail(f'tail short fdAT {n}',chunk(b'fdAT',bytes(n))+iend,None)
    for seq in (0,1,2):tail(f'tail fdAT sequence {seq}',chunk(b'fdAT',struct.pack('>I',seq)+b'ignored')+iend,ordinary if seq==1 else None,before=chunk(b'fcTL',frame(w=3,h=2)))
    tail('tail acTL does not change original animated flag',chunk(b'acTL',struct.pack('>II',2,0))+chunk(b'fcTL',b'')+iend,None)
    for count,default in [(1,False),(2,False),(1,True)]:
        before=chunk(b'acTL',struct.pack('>II',count,0))+(b'' if default else chunk(b'fcTL',frame(w=3,h=2)))
        tail(f'next fcTL count={count} default={default}',struct.pack('>I',100)+b'fcTL',ordinary if count+int(default)>1 else None,before)
    blob=b'x'*(1<<20);ztxt=chunk(b'zTXt',b'x\0\0'+zlib.compress(blob))
    for n in (63,64):tail(f'cumulative pre/post text {n}+1',ztxt+iend,ordinary if n==63 else None,before=ztxt*n)
    for kind,body in [(b'zTXt',b'x\0\0'+zlib.compress(blob+b'x')),(b'iCCP',b'p\0\0'+zlib.compress(blob+b'x'))]:tail(f'oversize tail {kind!r}',chunk(kind,body)+iend,None)
    for size in (65511,65512,65533,65534):
        body=b'comment\0'+b'x'*size
        tail(f'comment save limit {size}',chunk(b'tEXt',body)+iend,jpeg('L',(3,2),pixels,b'x'*size) if size<=65511 else None)
    # A first frame is placed on a zero canvas; blend/disposal do not composite
    # against another frame because no previous frame exists.
    for mode,color,channels in [('L',0,1),('RGB',2,3),('RGBA',6,4)]:
        sample=bytes((10,20,30,128)[:channels]);canvas=bytearray(3*2*channels);canvas[4*channels:5*channels]=sample
        s={**spec,'color':color};base=file_bytes(s,b'')[:33]
        for animated in (False,True):
            for disposal in (0,1,2,255):
                for blend in (0,1,255):
                    before=(chunk(b'acTL',struct.pack('>II',2,0)) if animated else b'')+chunk(b'fcTL',frame(w=1,h=1,x=1,y=1,disposal=disposal,blend=blend))
                    data=base+before+chunk(b'IDAT',zlib.compress(b'\0'+sample))+chunk(b'tEXt',b'comment\0after')+iend
                    add(f'canvas {mode} animated={animated} disposal={disposal} blend={blend}',data,jpeg(mode,(3,2),bytes(canvas),b'after'))
        full=sample*3+b'\0'+sample*3
        for w,h,x,y in [(0,0,0,0),(0,1,0,0),(0,1,0,1),(1,0,0,0),(0,0,1,0),(0,1,3,0),(3,0,0,2)]:
            data=base+chunk(b'fcTL',frame(w=w,h=h,x=x,y=y))+chunk(b'IDAT',zlib.compress(b'\0'+full))+iend
            add(f'zero extent {mode} {w,h,x,y}',data,jpeg(mode,(3,2),sample*6) if w==0 and x==0 else None)
    for mode,color,channels in [('L',0,1),('RGB',2,3),('RGBA',6,4)]:
        for size,target in [((200,100),(200,100)),((17,13),(200,152)),((289,100),(199,69)),((1,400),(0,200)),((400,1),(200,0)),((31,17),(200,109)),((257,129),(200,100))]:
            sample=bytes((10,20,30,128)[:channels]);p=sample*(size[0]*size[1]);raw=(b'\0'+sample*size[0])*size[1]
            s={**spec,'width':size[0],'height':size[1],'color':color};data=file_bytes(s,zlib.compress(raw))
            for length in (len(data),1024*1024,1024*1024+1):
                keep=(size==target and length<=1024*1024) or (0 in target and length>1024*1024)
                add(f'policy {mode} {size} length={length}',data+bytes(length-len(data)),None if keep else jpeg(mode,size,p,target=target if length>1024*1024 else None))
    add('signature without chunks',b'\x89PNG\r\n\x1a\n',None)
    broken=bytearray(prefix);broken[-1]^=1
    add('bad IHDR CRC',bytes(broken)+idat+iend,None)
    add('small header without pixel payload',file_bytes({**spec,'width':17,'height':13},b'')[:41],None)
    add('IEND without pixels',prefix+iend,None)
    for value in (b'',b'bad'):
        add(f'text bbox {value!r}',prefix+chunk(b'tEXt',b'bbox\0'+value)+idat+iend,None)
    for seq in (0,1,2):
        data=prefix+chunk(b'fcTL',frame(w=3,h=2))+chunk(b'fdAT',struct.pack('>I',seq)+compressed)+iend
        add(f'first fdAT sequence {seq}',data,ordinary if seq==1 else None)
    data=prefix+chunk(b'fcTL',frame(w=3,h=2))+chunk(b'fdAT',struct.pack('>I',1)+compressed)+chunk(b'fdAT',struct.pack('>I',2)+b'ignored')+iend
    add('first fdAT then later sequence',data,ordinary)
    for depth,color,mode,channels in MODES:
        if depth<8:
            sample=1<<(depth-1);packed=bytes([128])
            decoded=bytes([sample*255//(2**depth-1) if color==0 else sample])
        else:
            values=([42,128,90,160] if depth==8 else [0x1234,0x5678,0x9abc,0xdef0])[:channels]
            packed=bytes(values) if depth==8 else b''.join(struct.pack('>H',v) for v in values)
            if depth==8:decoded=packed
            elif color==0:decoded=struct.pack('<H',values[0])
            elif color==4:decoded=bytes([values[0]>>8]*3+[values[1]>>8])
            else:decoded=bytes(v>>8 for v in values)
        for interlace in (False,True):
            for animated in (False,True):
                canvas=bytearray(6*len(decoded));offset=0 if interlace else 4*len(decoded)
                canvas[offset:offset+len(decoded)]=decoded
                s={**spec,'depth':depth,'color':color,'interlace':interlace}
                base=file_bytes(s,b'')[:33]
                if color==3:base+=chunk(b'PLTE',bytes(v for i in range(256) for v in (i,i,i)))
                before=(chunk(b'acTL',struct.pack('>II',2,0)) if animated else b'')+chunk(b'fcTL',frame(w=1,h=1,x=1,y=1))
                data=base+before+chunk(b'IDAT',zlib.compress(b'\0'+packed))+iend
                with Image.open(io.BytesIO(data)) as image:
                    image.load();actual=image.convert('L').tobytes() if mode=='1' else image.tobytes()
                    assert image.mode==mode and actual==canvas,(depth,color,interlace,animated)
                add(f'first tile {depth}/{color} interlace={interlace} animated={animated}',data,jpeg(mode,(3,2),bytes(canvas)))
    for key in (b'interlace',b'bbox',b'disposal',b'default_image'):
        tail(f'post-pixel text state {key!r}',chunk(b'tEXt',key+b'\0changed')+iend)
    for interlace in (False,True):
        s={**spec,'interlace':interlace};base=file_bytes(s,b'')[:33]
        canvas=bytearray(6);canvas[0 if interlace else 4]=42
        data=base+chunk(b'fcTL',frame(w=1,h=1,x=1,y=1))+chunk(b'IDAT',zlib.compress(b'\0\x2a'))+chunk(b'tEXt',b'interlace\0'+(b'' if interlace else b'yes'))+iend
        add(f'post-pixel interlace flip {interlace}',data,jpeg('L',(3,2),bytes(canvas)))
    assert len(cases)==1466,len(cases)
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'png-file-codec';build_codec(codec,log,tool_env)
    fault=work/'png-file-fault';build_fault_codec(fault,log,tool_env)
    path=work/'png-file.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestPNG(File|Tail|Canvas)'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_PNG_FILE_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        path.unlink(missing_ok=True);codec.unlink(missing_ok=True);fault.unlink(missing_ok=True)
        Path(str(fault)+'.started').unlink(missing_ok=True)
    print(f'PNG file oracle: {len(cases)} whole-file output/fallback cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-png-file-',dir='/tmp') as directory:run(Path(directory),None,env)
