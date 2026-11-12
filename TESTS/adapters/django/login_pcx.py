"""Declared PCX/DCX header, RLE sample and whole-file login outcomes."""
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


def pcx(w,h,bits=8,planes=1,stride=None,version=5,origin=(0,0)):
    b=bytearray(128);b[:4]=bytes([10,version,1,bits])
    struct.pack_into('<4H',b,4,*origin,origin[0]+w-1,origin[1]+h-1)
    b[16:64]=bytes(range(48));b[65]=planes
    struct.pack_into('<H',b,66,(w*bits+7)//8 if stride is None else stride)
    return bytes(b)


def rle(data):
    return b''.join(bytes([0xc1,v]) if v>=192 else bytes([v]) for v in data)


def vectors():
    from PIL import Image
    cases=[]
    def jpeg(mode,size,pixels,target=None):
        if mode=='P':return None
        im=Image.frombytes('L' if mode=='1' else mode,size,pixels)
        if mode=='1':im=im.convert('1',dither=Image.Dither.NONE)
        if target is not None:im=im.resize(target,Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,data,*,w=3,h=2,bits=8,planes=1,stride=3,mode='L',raw='L',pixels=None,invalid=False,lazy=False,start=0,frames=1,directory=0):
        expected_header=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            offset=start+128
            headerbytes=directory+min(68,len(data)-start)+(min(769,len(data)) if bits==8 and planes==1 else 0)
            assert im.mode==mode and im.size==(w,h) and im.tile[0].offset==offset and im.tile[0].args==(raw,stride),(name,im.mode,im.size,im.tile)
            assert getattr(im,'n_frames',1)==frames,(name,'frame count')
            expected_header=dict(width=w,height=h,bits=bits,planes=planes,stride=stride,mode=mode,rawmode=raw,format='DCX' if directory else 'PCX',frames=frames,dataoffset=offset,headerbytes=headerbytes,pixelwarning=w*h>89478485)
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,struct.error,Image.DecompressionBombError):assert invalid,(name,'header rejected')
        if expected_header is not None and not lazy:
            try:
                im.load();actual=im.convert('L').tobytes() if mode=='1' else im.tobytes()
                assert pixels is not None and actual==pixels,(name,'pixels differ',actual[:90],pixels and pixels[:90])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'pixels rejected')
        target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if len(data)>1024*1024 and not invalid else None
        expected=None if invalid or lazy or pixels is None else jpeg(mode,(w,h),pixels,target)
        actual=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            ratio=min(200/im.width,200/im.height);size=(int(im.width*ratio),int(im.height*ratio))
            if size!=im.size or len(data)>1024*1024:
                im=im.resize(size,Image.Resampling.LANCZOS) if len(data)>1024*1024 else im.resize(im.size)
                out=io.BytesIO();im.save(out,format='JPEG');actual=out.getvalue()
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,struct.error,Image.DecompressionBombError):pass
        assert actual==expected,(name,'file policy differs')
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),lazy=lazy,keep=expected is None,jpeg=enc(expected)))

    for bits,planes,mode,raw in [(1,1,'1','1'),(1,2,'P','P;2L'),(1,4,'P','P;4L'),(8,1,'L','L'),(8,3,'RGB','RGB;L')]:
        for w in (1,2,3,5,7,8,9,17,31,32,63,64,65,129):
            pitch=(w*bits+7)//8;physical=bytearray();pixels=bytearray()
            for y in range(2):
                if bits==1:
                    values=[(x+y)%(1<<planes) for x in range(w)]
                    pixels.extend(v*255 if mode=='1' else v for v in values)
                    for p in range(planes):
                        for x in range(0,w,8):physical.append(sum(((values[x+i]>>p)&1)<<(7-i) for i in range(8) if x+i<w))
                elif planes==1:
                    row=bytes((x*7+y*31)%256 for x in range(w));physical+=row;pixels+=row
                else:
                    rows=[bytes((x*7+y*31+c*47)%256 for x in range(w)) for c in range(3)]
                    physical+=b''.join(rows);pixels+=bytes(rows[c][x] for x in range(w) for c in range(3))
            data=pcx(w,2,bits,planes)+rle(physical)
            kwargs=dict(w=w,bits=bits,planes=planes,stride=pitch*planes,mode=mode,raw=raw)
            add(f'raw layout {bits}/{planes} width={w}',data,pixels=bytes(pixels),**kwargs)
            add(f'truncated layout {bits}/{planes} width={w}',data[:-1],**kwargs)
            for version in (0,2,3):
                b=bytearray(data);b[1]=version
                add(f'version {version} {bits}/{planes} width={w}',bytes(b),pixels=bytes(pixels) if bits==1 else None,invalid=bits!=1,**kwargs)
    # Explicit padded planes: padding must not become another channel or index.
    for w,payload,pixels in [
        (1,bytes([10,1,20,2,30,3]),bytes([10,20,30])),
        (3,bytes([10,11,12,1,20,21,22,2,30,31,32,3]),bytes([10,20,30,11,21,31,12,22,32])),
        (5,bytes([10,11,12,13,14,1,20,21,22,23,24,2,30,31,32,33,34,3]),bytes([10,20,30,11,21,31,12,22,32,13,23,33,14,24,34])),
    ]:
        for provided in (0,w+1,65535):
            data=pcx(w,1,8,3,provided)+payload
            add(f'RGB padded width={w} declared={provided}',data,w=w,h=1,planes=3,stride=(w+1)*3,mode='RGB',raw='RGB;L',pixels=pixels)
            add(f'RGB final padding required width={w} declared={provided}',data[:-1],w=w,h=1,planes=3,stride=(w+1)*3,mode='RGB',raw='RGB;L')
    for planes,payload,pixels in [(2,b'\xa0\0\x60\0',bytes([1,2,3])),(4,b'\xa0\0\x60\0\x20\0\x40\0',bytes([1,10,7]))]:
        add(f'padded indexed planes={planes}',pcx(3,1,1,planes,2)+rle(payload),h=1,bits=1,planes=planes,stride=2*planes,mode='P',raw=f'P;{planes}L',pixels=pixels)
    add('padded indexed width17',pcx(17,1,1,2,4)+rle(b'\xff\xff\x80\0\0\xff\0\0'),w=17,h=1,bits=1,planes=2,stride=8,mode='P',raw='P;2L',pixels=bytes([1]*8+[3]*8+[1]))
    for count in range(64):
        w=max(1,count);payload=bytes([0xc0|count,42])+(b'\x2a' if count==0 else b'')
        add(f'run count {count}',pcx(w,1)+payload,w=w,h=1,stride=w,pixels=bytes([42]*w))
        if count>=2:add(f'run overrun {count}',pcx(w-1,1)+payload,w=w-1,h=1,stride=w-1)
    add('literal and escaped byte range',pcx(256,1)+rle(bytes(range(256))),w=256,h=1,stride=256,pixels=bytes(range(256)))
    add('zero runs consume every possible value',pcx(1,1)+b''.join(bytes([192,v]) for v in range(256))+b'*',w=1,h=1,stride=1,pixels=b'*')
    for payload,pixels in [(b'\xc3*\xc3+',b'***+++'),(b'\xc6*',None),(b'\xc0*\xc3*\xc0+\xc3+',b'***+++'),(b'\xc3*\xc3+\xff',b'***+++'),(b'\1\2\xc1',None)]:
        add(f'RLE rows {payload!r}',pcx(3,2)+payload,pixels=pixels)
    for encoding in (0,1,2,255):
        data=bytearray(pcx(3,1)+b'\xc3*');data[2]=encoding
        add(f'encoding field ignored {encoding}',bytes(data),h=1,pixels=b'***')
    gray=bytes([12])+b''.join(bytes([i])*3 for i in range(256))
    base=pcx(3,2)+bytes(range(6))
    for index in (None,0,127,255):
        palette=bytearray(gray)
        if index is not None:palette[1+index*3]^=1
        add(f'tail palette mutation {index}',base+palette,mode='L' if index is None else 'P',raw='L' if index is None else 'P',pixels=bytes(range(6)))
        add(f'palette followed by extra byte {index}',base+palette+b'x',pixels=bytes(range(6)))
    for length in (0,1,2,767,768):add(f'incomplete palette {length}',base+gray[:length],pixels=bytes(range(6)))
    add('palette marker used as final run value',pcx(1,1)+b'\xc1'+gray,w=1,h=1,stride=1,pixels=b'\x0c')
    for stride in (0,1,2,3,4,65535):
        add(f'L stride {stride}',pcx(3,1,stride=stride)+b'\1\2\3\4',h=1,stride=3 if stride==3 else 4,pixels=b'\1\2\3')
    add('nonzero origin',pcx(3,2,origin=(42,99))+bytes(range(6)),pixels=bytes(range(6)))
    for dpi in ((72,96),(65535,1)):
        data=bytearray(base);struct.pack_into('<HH',data,12,*dpi)
        add(f'DPI ignored by default JPEG {dpi}',bytes(data),pixels=bytes(range(6)))
    for n in range(68):add(f'too short header {n}',pcx(200,100)[:n],invalid=True)
    for n in range(68,129):
        for bits,planes,mode,raw in ((1,1,'1','1'),(8,1,'L','L'),(8,3,'RGB','RGB;L')):
            add(f'lazy partial header {n} {bits}/{planes}',pcx(200,100,bits,planes)[:n],w=200,h=100,bits=bits,planes=planes,stride=((200*bits+7)//8)*planes,mode=mode,raw=raw,lazy=True)
    for bits,planes in ((0,1),(2,1),(4,1),(1,0),(1,3),(1,5),(8,2),(8,4),(16,3),(255,255)):
        add(f'unsupported mode {bits}/{planes}',pcx(3,2,bits,planes),invalid=True)
    for version in (1,4,6,255):add(f'unsupported version {version}',pcx(3,2,version=version),invalid=True)
    for field in (4,6):
        data=bytearray(base);struct.pack_into('<H',data,field,65535);add(f'inverted bbox {field}',bytes(data),invalid=True)
    add('pixel warning',pcx(10000,10000),w=10000,h=10000,stride=10000)
    add('pixel hard limit',pcx(65536,65536,stride=0),invalid=True)
    # DCX only loads the first entry; later offsets are not inspected.
    for bits,planes,mode,raw,payload,pixels in [(1,1,'1','1',b'\xa0\x40',b'\xff\0\xff\0\xff\0'),(8,1,'L','L',bytes(range(6)),bytes(range(6))),(8,3,'RGB','RGB;L',bytes(range(18)),bytes([0,3,6,1,4,7,2,5,8,9,12,15,10,13,16,11,14,17]))]:
        for frames in (1,2,1024):
            directory=4+4*(frames+(frames<1024));start=directory
            prefix=struct.pack('<I',0x3ade68b1)+struct.pack('<I',start)+struct.pack('<I',0xffffffff)*(frames-1)+(bytes(4) if frames<1024 else b'')
            data=prefix+pcx(3,2,bits,planes)+rle(payload)
            # Every frame mode starts 128 bytes after its own frame offset.
            add(f'DCX {bits}/{planes} frames={frames}',data,bits=bits,planes=planes,stride=((3*bits+7)//8)*planes,mode=mode,raw=raw,start=start,directory=directory,frames=frames,pixels=pixels)
    prefix=struct.pack('<III',0x3ade68b1,12,0)
    for offset in (13,64):
        pre=struct.pack('<III',0x3ade68b1,offset,0)+bytes(offset-12)
        for planes in (1,3):
            expected=bytes(range(6)) if planes==1 else bytes([0,3,6,1,4,7,2,5,8,9,12,15,10,13,16,11,14,17])
            add(f'DCX frame offset {offset} planes={planes}',pre+pcx(3,2,planes=planes)+bytes(range(6*planes)),planes=planes,stride=3*planes,mode='L' if planes==1 else 'RGB',raw='L' if planes==1 else 'RGB;L',start=offset,directory=12,pixels=expected)
    for bits,planes,mode,raw in ((1,1,'1','1'),(8,1,'L','L'),(8,3,'RGB','RGB;L')):
        add(f'DCX partial lazy header {bits}/{planes}',prefix+pcx(200,100,bits,planes)[:68],w=200,h=100,bits=bits,planes=planes,stride=((200*bits+7)//8)*planes,mode=mode,raw=raw,start=12,directory=12,lazy=True)
    for index in (None,255):
        palette=bytearray(gray)
        if index is not None:palette[1+index*3]^=1
        add(f'DCX palette at whole file end {index}',prefix+pcx(3,2)+bytes(range(6))+palette,start=12,directory=12,mode='L' if index is None else 'P',raw='L' if index is None else 'P',pixels=bytes(range(6)))
    for n in (4,5,7,8,9,11):add(f'DCX short directory {n}',prefix[:n],invalid=True)
    for data in (struct.pack('<II',0x3ade68b1,0),struct.pack('<III',0x3ade68b1,0xffffffff,0),prefix+b'bad'):
        add(f'DCX invalid first frame {data!r}',data,invalid=True)
    # Whole-file byte threshold remains strict for gray and bilevel images.
    for bits,mode,raw,payload,pixels in [(8,'L','L',bytes(range(6)),bytes(range(6))),(1,'1','1',b'\xa0\x40',b'\xff\0\xff\0\xff\0')]:
        data=pcx(3,2,bits)+rle(payload)
        for size in (1024*1024,1024*1024+1):
            add(f'file threshold {bits}/{size}',data+bytes(size-len(data)),bits=bits,mode=mode,raw=raw,stride=3 if bits==8 else 1,pixels=pixels)
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'pcx-codec';build_codec(codec,log,tool_env)
    fault=work/'pcx-fault';build_fault_codec(fault,log,tool_env)
    path=work/'pcx.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestPCX'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_PCX_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'PCX/DCX oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-pcx-',dir='/tmp') as directory:run(Path(directory),None,env)
