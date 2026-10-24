"""Declared SGI header, planar/RLE samples and whole-file login outcomes."""
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


def sgi(w,h,c=1,bpc=1,compression=0,dimension=None):
    b=bytearray(512)
    struct.pack_into('>HBB4H',b,0,474,compression,bpc,(2 if c==1 else 3) if dimension is None else dimension,w,h,c)
    return bytes(b)


def atoms(bpc,*values):
    return bytes(values) if bpc==1 else b''.join(struct.pack('>H',v) for v in values)


def rle_file(w,h,rows,c=1,bpc=1,dimension=None,lengths=None,offsets=None,tail=b''):
    start=512+8*c*h;positions=[];body=b''
    for row in rows:positions.append(start+len(body));body+=row
    return sgi(w,h,c,bpc,1,dimension)+b''.join(struct.pack('>I',v) for v in (positions if offsets is None else offsets))+b''.join(struct.pack('>I',v) for v in ([len(row) for row in rows] if lengths is None else lengths))+body+tail


def vectors():
    from PIL import Image
    cases=[]
    def jpeg(mode,size,pixels,target=None):
        im=Image.frombytes(mode,size,pixels)
        if mode=='RGBA':
            bg=Image.new('RGB',im.size,(255,255,255));bg.paste(im,mask=im.getchannel('A'));im=bg
        if target is not None:im=im.resize(target,Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,data,*,w=3,h=1,c=1,bpc=1,compression=0,dimension=None,pixels=None,invalid=False,lazy=False):
        dimension=(2 if c==1 else 3) if dimension is None else dimension
        mode={1:'L',3:'RGB',4:'RGBA'}.get(c,'');raw=mode+(';16B' if bpc==2 else '')
        expected_header=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            assert im.format=='SGI' and im.mode==mode and im.size==(w,h),(name,im.format,im.mode,im.size)
            if compression==0 and bpc==1:
                expected_tiles=[('raw',(0,0,w,h),512+band*w*h,(layer,0,-1)) for band,layer in enumerate(mode)]
            elif compression==0:expected_tiles=[('SGI16',(0,0,w,h),512,(mode,0,-1))]
            elif compression==1:expected_tiles=[('sgi_rle',(0,0,w,h),512,(raw,-1,bpc))]
            else:expected_tiles=[]
            assert im.tile==expected_tiles,(name,'tiles',im.tile,expected_tiles)
            expected_header=dict(width=w,height=h,channels=c,bpc=bpc,dimension=dimension,compression=compression,mode=mode,rawmode=raw,dataoffset=512,headerbytes=min(512,len(data)),pixelwarning=w*h>89478485)
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,struct.error,Image.DecompressionBombError):assert invalid,(name,'header rejected')
        if expected_header is not None and not lazy:
            try:
                im.load();actual=im.tobytes()
                assert pixels is not None and actual==pixels,(name,'pixels differ',actual[:90],pixels and pixels[:90])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'pixels rejected')
        target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if len(data)>1024*1024 and not invalid else None
        expected=None if invalid or lazy or pixels is None else jpeg(mode,(w,h),pixels,target)
        actual=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            ratio=min(200/im.width,200/im.height);size=(int(im.width*ratio),int(im.height*ratio))
            if size!=im.size or len(data)>1024*1024:
                if im.mode=='RGBA':
                    bg=Image.new('RGB',im.size,(255,255,255));bg.paste(im,mask=im.getchannel('A'));im=bg
                im=im.resize(size,Image.Resampling.LANCZOS) if len(data)>1024*1024 else im.resize(im.size)
                out=io.BytesIO();im.save(out,format='JPEG');actual=out.getvalue()
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,struct.error,Image.DecompressionBombError):pass
        assert actual==expected,(name,'file policy differs')
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),pixelmode=mode,lazy=lazy,keep=expected is None,jpeg=enc(expected)))

    layouts=[(1,1,1),(1,2,1),(2,1,1),(2,2,1),(1,3,3),(2,3,3),(1,3,4),(2,3,4)]
    for bpc,dimension,c in layouts:
        for w in (1,2,3,7,17,63,127,128,129,256):
            physical=[];compressed=[]
            for band in range(c):
                for y in range(2):
                    values=[(x*17+y*31+band*59)%256 for x in range(w)]
                    samples=[v if bpc==1 else v*256+(x*13+band)%256 for x,v in enumerate(values)]
                    physical.append(atoms(bpc,*samples))
                    row=b''.join(atoms(bpc,128+len(samples[i:i+127]),*samples[i:i+127]) for i in range(0,w,127))+bytes(bpc)
                    compressed.append(row)
            expected=bytes((x*17+y*31+band*59)%256 for y in (1,0) for x in range(w) for band in range(c))
            kwargs=dict(w=w,h=2,c=c,bpc=bpc,dimension=dimension)
            for compression,data in [(0,sgi(w,2,c,bpc,dimension=dimension)+b''.join(physical)),(1,rle_file(w,2,compressed,c,bpc,dimension))]:
                add(f'layout {bpc}/{dimension}/{c}/{w}/{compression}',data,compression=compression,pixels=expected,**kwargs)
                add(f'truncated layout {bpc}/{dimension}/{c}/{w}/{compression}',data[:-1],compression=compression,**kwargs)
        for n in (12,13,31,127,511,512):
            add(f'partial lazy header {bpc}/{dimension}/{c}/{n}',sgi(200,100,c,bpc,dimension=dimension)[:n],w=200,h=100,c=c,bpc=bpc,dimension=dimension,lazy=True)
        for compression in (2,3,255):
            for lazy in (False,True):
                w,h=(200,100) if lazy else (3,1)
                add(f'unknown compression {bpc}/{dimension}/{c}/{compression}/{lazy}',sgi(w,h,c,bpc,compression,dimension),w=w,h=h,c=c,bpc=bpc,dimension=dimension,compression=compression,lazy=lazy)
    for bpc in (1,2):
        sample=lambda v:v if bpc==1 else v*256+17
        for count in range(1,128):
            repeat=atoms(bpc,count,sample(42),0)
            literal=atoms(bpc,128+count,*[sample(i) for i in range(count)],0)
            add(f'repeat {bpc}/{count}',rle_file(count,1,[repeat],bpc=bpc),w=count,bpc=bpc,compression=1,pixels=bytes([42])*count)
            add(f'literal {bpc}/{count}',rle_file(count,1,[literal],bpc=bpc),w=count,bpc=bpc,compression=1,pixels=bytes(range(count)))
            if count>1:add(f'row overrun {bpc}/{count}',rle_file(count-1,1,[repeat],bpc=bpc),w=count-1,bpc=bpc,compression=1)
        first=atoms(bpc,3,sample(42),0);second=atoms(bpc,1,sample(99),0)
        add(f'partial row retains preceding values {bpc}',rle_file(3,2,[first,second],bpc=bpc),h=2,bpc=bpc,compression=1,pixels=bytes([99,42,42,42,42,42]))
        for length in (0,1,2,3,0x7fffffff,0x80000000,0xffffffff):
            expected=bytes(3) if length in (0,1,0x80000000,0xffffffff) else bytes([42])*3
            add(f'declared packet budget {bpc}/{length}',rle_file(3,1,[first],bpc=bpc,lengths=[length]),bpc=bpc,compression=1,pixels=expected)
        for op in (0,1,128,129,255):
            for length in (1,2):
                row=atoms(bpc,op)
                expected=bytes([42])*6 if op in (0,128) and (length==2 or op==0) else bytes(3)+bytes([42])*3 if length==1 else None
                add(f'early exit {bpc}/{op}/{length}',rle_file(3,2,[first,row],bpc=bpc,lengths=[len(first),length]),h=2,bpc=bpc,compression=1,pixels=expected)
        for length in (0,0x80000000,0xffffffff):
            add(f'past-file offset with no packets {bpc}/{length}',rle_file(3,1,[b''],bpc=bpc,offsets=[0xffffffff],lengths=[length]),bpc=bpc,compression=1,pixels=bytes(3))
        for offset,length,pixels in [(511,0,None),(511,1,None),(512,1,bytes(3)),(520,1,None),(0xffffffff,1,None)]:
            add(f'offset bounds {bpc}/{offset}/{length}',rle_file(3,1,[b''],bpc=bpc,offsets=[offset],lengths=[length]),bpc=bpc,compression=1,pixels=pixels)
        for kind,row in [('repeat',atoms(bpc,3,sample(42))),('literal',atoms(bpc,131,sample(10),sample(20),sample(30)))]:
            for missing in range(1,bpc+1):
                add(f'missing terminator bytes {bpc}/{kind}/{missing}',rle_file(3,1,[row+bytes(bpc-missing)],bpc=bpc),bpc=bpc,compression=1)
        rows=[atoms(bpc,3,sample(42),0),atoms(bpc,3,sample(99),0)];start=528
        add(f'reversed row offsets {bpc}',rle_file(3,2,rows,bpc=bpc,offsets=[start+len(rows[0]),start]),h=2,bpc=bpc,compression=1,pixels=bytes([42])*3+bytes([99])*3)
        add(f'shared row offsets {bpc}',rle_file(3,2,rows,bpc=bpc,offsets=[start,start]),h=2,bpc=bpc,compression=1,pixels=bytes([42])*6)
        overlap=atoms(bpc,1,129 if bpc==1 else 0x2a81,0,0)
        add(f'overlapping rows {bpc}',rle_file(1,2,[overlap,b''],bpc=bpc,offsets=[528,528+bpc],lengths=[4,3]),w=1,h=2,bpc=bpc,compression=1,pixels=bytes([0,129 if bpc==1 else 42]))
        for c in (3,4):
            counts=[1,0,2,3][:c];rows=[]
            for band in range(c):
                rows.extend([atoms(bpc,3,sample(20+band*40),0),atoms(bpc,counts[band],sample(99+band),0) if counts[band] else atoms(bpc,0)])
            expected=bytes(99+band if x<counts[band] else 20+band*40 for x in range(3) for band in range(c))+bytes(20+band*40 for x in range(3) for band in range(c))
            add(f'partial multichannel rows {bpc}/{c}',rle_file(3,2,rows,c,bpc),h=2,c=c,bpc=bpc,compression=1,pixels=expected)
            lengths=[len(row) for row in rows];lengths[2]=1
            add(f'early exit before storing first row {bpc}/{c}',rle_file(3,2,rows,c,bpc,lengths=lengths),h=2,c=c,bpc=bpc,compression=1,pixels=bytes(6*c))
    for length in range(8):add(f'truncated RLE table {length}',sgi(3,1,compression=1)+bytes(length),compression=1)
    for n in range(12):add(f'truncated header {n}',sgi(200,100)[:n],invalid=True)
    for bpc,dimension,c in [(0,2,1),(3,2,1),(255,2,1),(1,0,1),(1,3,1),(1,65535,1),(1,2,0),(1,2,2),(1,3,5),(2,1,3),(2,2,4)]:
        add(f'invalid layout {bpc}/{dimension}/{c}',sgi(3,1,c,bpc,dimension=dimension),invalid=True)
    for w,h in ((0,1),(1,0),(65535,65535)):add(f'invalid dimensions {w}/{h}',sgi(w,h),invalid=True)
    add('pixel warning',sgi(10000,10000),w=10000,h=10000)
    for fill in (0,127,255):
        data=bytearray(sgi(3,1)+bytes([10,20,30]));data[12:512]=bytes([fill])*500
        add(f'ignored header metadata {fill}',bytes(data),pixels=bytes([10,20,30]))
    for c in (1,4):
        rows=[bytes([10+band*40,20+band*40,30+band*40]) for band in range(c)]
        pixels=bytes(rows[band][x] for x in range(3) for band in range(c))
        for compression,data in [(0,sgi(3,1,c)+b''.join(rows)),(1,rle_file(3,1,[bytes([131])+row+b'\0' for row in rows],c))]:
            for size in (1024*1024,1024*1024+1):add(f'file threshold {c}/{compression}/{size}',data+bytes(size-len(data)),c=c,compression=compression,pixels=pixels)
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'sgi-codec';build_codec(codec,log,tool_env)
    fault=work/'sgi-fault';build_fault_codec(fault,log,tool_env)
    path=work/'sgi.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestSGI'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_SGI_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'SGI oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-sgi-',dir='/tmp') as directory:run(Path(directory),None,env)
