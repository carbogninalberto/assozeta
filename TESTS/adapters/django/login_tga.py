"""Declared TGA header, raw/RLE sample and whole-file login outcomes."""
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


def tga(w,h,kind=3,depth=8,flags=32,*,ident=b'',palette=None,start=0,count=None,mapdepth=24):
    b=bytearray(18);b[0]=len(ident);b[1]=palette is not None;b[2]=kind
    if palette is not None:struct.pack_into('<HHB',b,3,start,len(palette)//(mapdepth//8) if count is None else count,mapdepth)
    struct.pack_into('<HHBB',b,12,w,h,depth,flags)
    return bytes(b)+ident+(palette or b'')


def vectors():
    from PIL import Image
    cases=[]
    def jpeg(mode,size,pixels,target=None):
        if mode not in ('1','L','RGB','RGBA'):return None
        im=Image.frombytes('L' if mode=='1' else mode,size,pixels)
        if mode=='1':im=im.convert('1',dither=Image.Dither.NONE)
        if mode=='RGBA':
            bg=Image.new('RGB',im.size,(255,255,255));bg.paste(im,mask=im.getchannel('A'));im=bg
        if target is not None:im=im.resize(target,Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,data,*,w=3,h=2,kind=3,depth=8,flags=32,mode='L',raw='L',offset=18,palette=False,badpalette=False,pixelmode=None,pixels=None,invalid=False,lazy=False):
        pixelmode=pixelmode or mode
        expected_header=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            assert im.format=='TGA' and im.mode==mode and im.size==(w,h),(name,im.format,im.mode,im.size)
            assert bool(im.palette is not None)==palette,(name,'palette')
            assert im.info['orientation']==(1 if flags&32 else -1) and im._flip_horizontally==bool(flags&16),(name,'orientation')
            if raw:
                args=(raw,1 if flags&32 else -1,depth) if kind&8 else (raw,0,1 if flags&32 else -1)
                assert im.tile[0].offset==offset and im.tile[0].args==args,(name,'tile',im.tile)
            else:assert not im.tile,(name,'unexpected tile')
            expected_header=dict(width=w,height=h,depth=depth,imagetype=kind,dataoffset=offset,mode=mode,rawmode=raw,topdown=bool(flags&32),fliphorizontal=bool(flags&16),compressed=bool(kind&8),haspalette=palette,invalidpalette=badpalette,pixelwarning=w*h>89478485)
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,struct.error,Image.DecompressionBombError):assert invalid,(name,'header rejected')
        if expected_header is not None and not lazy:
            try:
                im.load();actual=im.convert('L').tobytes() if mode=='1' else im.tobytes()
                assert pixels is not None and actual==pixels and im.im.mode==pixelmode,(name,'pixels differ',im.im.mode,actual[:90],pixels and pixels[:90])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'pixels rejected')
        target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if len(data)>1024*1024 and not invalid else None
        expected=None if invalid or lazy or pixels is None else jpeg(pixelmode,(w,h),pixels,target)
        actual=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            ratio=min(200/im.width,200/im.height);size=(int(im.width*ratio),int(im.height*ratio))
            if size!=im.size or len(data)>1024*1024:
                # The production helper flattens alpha before resizing.
                if im.mode=='RGBA':
                    bg=Image.new('RGB',im.size,(255,255,255));bg.paste(im,mask=im.getchannel('A'));im=bg
                im=im.resize(size,Image.Resampling.LANCZOS) if len(data)>1024*1024 else im.resize(im.size)
                out=io.BytesIO();im.save(out,format='JPEG');actual=out.getvalue()
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,struct.error,Image.DecompressionBombError):pass
        assert actual==expected,(name,'file policy differs')
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),pixelmode=pixelmode,lazy=lazy,keep=expected is None,jpeg=enc(expected)))

    for kind,depth,mode,raw in [(3,1,'1','1'),(3,8,'L','L'),(3,16,'LA','LA'),(2,16,'RGBA','BGRA;15Z'),(2,24,'RGB','BGR'),(2,32,'RGBA','BGRA'),(1,8,'P','P')]:
        for w in (1,2,3,7,8,9,17,31,128,129):
            rows=[];samples=[]
            for y in range(2):
                row=[];sample=[]
                for x in range(w):
                    v=(x*37+y*83)%256
                    if depth==1:sample.append(bytes([255*((x+y)%2)]))
                    elif mode in ('L','P'):row.append(bytes([v]));sample.append(bytes([v]))
                    elif mode=='LA':row.append(bytes([v,255-v]));sample.append(bytes([v,255-v]))
                    elif depth==16:
                        value=((x+y)%2)*32768+((x*3+y)%32)*1024+((x+y*5)%32)*32+((x*7+y)%32)
                        row.append(struct.pack('<H',value));sample.append(bytes([((value>>10)&31)*255//31,((value>>5)&31)*255//31,(value&31)*255//31,0 if value&32768 else 255]))
                    else:
                        rgba=bytes([v,255-v,(v+113)%256, (x*19+y*31)%256]);row.append(bytes([rgba[2],rgba[1],rgba[0]])+(rgba[3:] if depth==32 else b''));sample.append(rgba if depth==32 else rgba[:3])
                rows.append([bytes([sum(((x+i+y)%2)<<(7-i) for i in range(8) if x+i<w)]) for x in range(0,w,8)] if depth==1 else row)
                samples.append(sample)
            for flags in (0,16,32,48,255):
                normalized=b''.join(b''.join(row[::-1] if flags&16 else row) for row in (samples if flags&32 else samples[::-1]))
                palette=bytes(768) if mode=='P' else None;offset=786 if palette is not None else 18
                kwargs=dict(w=w,kind=kind,depth=depth,flags=flags,mode=mode,raw=raw,palette=palette is not None,offset=offset)
                data=tga(w,2,kind,depth,flags,palette=palette)+b''.join(b''.join(row) for row in rows)
                add(f'raw {kind}/{depth} w={w} flags={flags}',data,pixels=normalized,**kwargs)
                if flags==32:add(f'truncated raw {kind}/{depth} w={w}',data[:-1],**kwargs)
                # Literal packets can span rows; packet size remains at most128.
                physical=[v for row in rows for v in row]
                payload=b''.join(bytes([len(physical[i:i+128])-1])+b''.join(physical[i:i+128]) for i in range(0,len(physical),128))
                add(f'RLE literal {kind}/{depth} w={w} flags={flags}',tga(w,2,kind+8,depth,flags,palette=palette)+payload,pixels=normalized if depth!=1 else None,**{**kwargs,'kind':kind+8})
    for kind,depth,mode,raw,source,sample in [(3,16,'LA','LA',b'\x2a\x7f',b'\x2a\x7f'),(2,16,'RGBA','BGRA;15Z',b'\0\xfc',b'\xff\0\0\0'),(2,24,'RGB','BGR',b'\1\2\3',b'\3\2\1'),(2,32,'RGBA','BGRA',b'\1\2\3\x7f',b'\3\2\1\x7f')]:
        for flags in (0,16,32,48):
            add(f'multibyte repeat {depth}/{mode}/{flags}',tga(3,2,kind+8,depth,flags)+b'\x82'+source+b'\x82'+source,kind=kind+8,depth=depth,mode=mode,raw=raw,flags=flags,pixels=sample*6)
            add(f'multibyte repeat crosses row {depth}/{mode}/{flags}',tga(3,2,kind+8,depth,flags)+b'\x85'+source,kind=kind+8,depth=depth,mode=mode,raw=raw,flags=flags)
    for flags in (1,8,15,64,128,192):
        b=bytearray(tga(3,2,flags=flags)+bytes(range(6)));struct.pack_into('<HH',b,8,65535,12345)
        add(f'origin descriptor bits ignored {flags}',bytes(b),flags=flags,pixels=bytes([3,4,5,0,1,2]))
    for mode,depth,raw,payload,pixels in [('1',1,'1',b'\xa0\x40',b'\xff\0\xff\0\xff\0'),('RGBA',32,'BGRA',bytes([10,20,30,40,50,60,70,80,90,100,110,120])*2,bytes([30,20,10,40,70,60,50,80,110,100,90,120])*2)]:
        kind=3 if depth==1 else 2
        data=tga(3,2,kind,depth)+payload
        for size in (1024*1024,1024*1024+1):
            add(f'alpha/bilevel file threshold {depth}/{size}',data+bytes(size-len(data)),kind=kind,depth=depth,mode=mode,raw=raw,pixels=pixels)
    for n in (1,2,3,63,127,128):
        for flags in (0,16,32,48):
            kwargs=dict(w=n,h=2,kind=11,flags=flags)
            expected=b'*'*n+b'+'*n if flags&32 else b'+'*n+b'*'*n
            add(f'repeat rows {n}/{flags}',tga(n,2,11,flags=flags)+bytes([127+n,42,127+n,43]),pixels=expected,**kwargs)
            add(f'truncated repeat {n}/{flags}',tga(n,2,11,flags=flags)+bytes([127+n,42,127+n]),**kwargs)
    for payload,pixels in [(b'\x83*',None),(b'\3\1\2\3\4',bytes([1,2,3,4])),(b'\5\1\2\3\4',None),(b'\5\1\2\3\4\5\6',bytes([1,2,3,4])),(b'\x81*\x81+\xff',b'**++')]:
        add(f'RLE boundary {payload!r}',tga(2,2,11)+payload,w=2,kind=11,pixels=pixels)
    # TGA v2 extension attribute zero disables alpha; other values retain it.
    for depth,source,sample,raw in [(16,b'\0\xfc',b'\xff\0\0\0','BGRA;15Z'),(32,b'\1\2\3\x7f',b'\3\2\1\x7f','BGRA')]:
        for compressed in (False,True):
            kind=10 if compressed else 2
            for attribute in (None,0,1,2,3,4,255):
                payload=b'\x82'+source+b'\x82'+source if compressed else source*6
                data=tga(3,2,kind,depth)+payload
                offset=len(data)
                extension=bytes(494)+(b'' if attribute is None else bytes([attribute]))
                data+=extension+struct.pack('<II',offset,0)+b'TRUEVISION-XFILE.\0'
                pixels=(sample[:3]+b'\xff' if attribute==0 else sample)*6
                # Missing attributes points into the footer: offset is nonzero,
                # so its first byte is not zero and alpha is retained.
                add(f'v2 alpha {depth}/{compressed}/{attribute}',data,kind=kind,depth=depth,mode='RGBA',raw=raw,pixels=pixels)
            data=tga(3,2,kind,depth)+payload
            for offset in (0,2**32-1):
                add(f'v2 alpha missing extension {depth}/{compressed}/{offset}',data+struct.pack('<II',offset,0)+b'TRUEVISION-XFILE.\0',kind=kind,depth=depth,mode='RGBA',raw=raw,pixels=sample*6)
    for depth,source,raw in [(16,b'\0\xfc','BGRA;15Z'),(32,b'\1\2\3\x7f','BGRA')]:
        data=tga(1,1,2,depth)+source
        add(f'RGBA file shorter than footer {depth}',data,w=1,h=1,kind=2,depth=depth,mode='RGBA',raw=raw,pixels=b'\xff\0\0\0' if depth==16 else b'\3\2\1\x7f')
    for mapdepth in (16,24,32):
        unit=mapdepth//8
        for kind,depth,mode,raw,payload in [(1,8,'P','P',bytes(range(6))),(3,8,'L','L',bytes(range(6))),(3,16,'LA','LA',bytes(range(12))),(3,1,'1','1',b'\xa0\x40'),(2,24,'RGB','BGR',bytes(range(18))),(2,32,'RGBA','BGRA',bytes(range(24)))]:
            for start,count in ((0,0),(0,1),(1,2),(255,1),(256,0),(256,1),(65535,0)):
                bad=start+count>256
                data=tga(3,2,kind,depth,palette=bytes(unit*count),start=start,mapdepth=mapdepth)+payload
                pixels=payload if mode in ('P','L','LA') and not bad else None
                add(f'palette {mapdepth}/{kind}/{depth} start={start} count={count}',data,kind=kind,depth=depth,mode=mode,raw=raw,offset=18+unit*count,palette=True,badpalette=bad,pixels=pixels,pixelmode='PA' if mode=='LA' else 'P' if mode=='L' else mode)
        for length in (0,1,unit-1,unit,unit+1):
            data=tga(3,2,1,8,palette=bytes(length),count=2,mapdepth=mapdepth)
            add(f'short palette {mapdepth}/{length}',data,kind=1,mode='P',raw='P',offset=18+length,palette=True,badpalette=False)
    for kind,depth in [(k,d) for k in (1,2,3,9,10,11) for d in (1,8,16,24,32)]:
        mode='P' if kind in (1,9) else 'RGB' if kind in (2,10) and depth==24 else 'RGBA' if kind in (2,10) else '1' if depth==1 else 'LA' if depth==16 else 'L'
        raw={(1,8):'P',(2,16):'BGRA;15Z',(2,24):'BGR',(2,32):'BGRA',(3,1):'1',(3,8):'L',(3,16):'LA'}.get((kind&7,depth),'')
        palette=b'' if kind in (1,9) else None
        for lazy in (False,True):
            w,h=(200,100) if lazy else (3,2)
            add(f'empty tile or pixels {kind}/{depth} lazy={lazy}',tga(w,h,kind,depth,palette=palette),w=w,h=h,kind=kind,depth=depth,mode=mode,raw=raw,palette=palette is not None,lazy=lazy)
    add('indexed without palette',tga(3,2,1)+bytes(range(6)),kind=1,raw='P')
    for ident in (b'x',bytes(range(255))):
        add(f'ID ignored length={len(ident)}',tga(3,2,ident=ident)+bytes(range(6)),offset=18+len(ident),pixels=bytes(range(6)))
        b=bytearray(tga(200,100));b[0]=255
        add(f'lazy short ID length={len(ident)}',bytes(b)+ident,w=200,h=100,offset=18+len(ident),lazy=True)
    for n in range(18):add(f'short header {n}',tga(3,2)[:n],invalid=True)
    for field,values in [(1,(2,255)),(2,(0,4,8,12,255)),(16,(0,2,15,17,255))]:
        for value in values:
            b=bytearray(tga(3,2));b[field]=value;add(f'invalid field {field}/{value}',bytes(b),invalid=True)
    for mapdepth in (0,1,8,15,255):
        b=bytearray(tga(3,2));b[1]=1;b[7]=mapdepth;add(f'invalid palette depth {mapdepth}',bytes(b),invalid=True)
    add('zero width',tga(0,2),invalid=True);add('zero height',tga(3,0),invalid=True)
    add('pixel warning',tga(10000,10000),w=10000,h=10000)
    add('pixel hard limit',tga(65535,65535),invalid=True)
    for size in (1024*1024,1024*1024+1):
        data=tga(3,2)+bytes(range(6));add(f'file threshold {size}',data+bytes(size-len(data)),pixels=bytes(range(6)))
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'tga-codec';build_codec(codec,log,tool_env)
    fault=work/'tga-fault';build_fault_codec(fault,log,tool_env)
    path=work/'tga.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestTGA'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_TGA_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'TGA oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-tga-',dir='/tmp') as directory:run(Path(directory),None,env)
