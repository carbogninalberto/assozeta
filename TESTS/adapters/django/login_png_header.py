"""Explicit Pillow PNG lazy-admission expectations, including metadata/APNG."""
import base64
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
MAGIC=b'\x89PNG\r\n\x1a\n'


def chunk(kind,data=b'',length=None):
    return struct.pack('>I',len(data) if length is None else length)+kind+data+struct.pack('>I',zlib.crc32(kind+data))


def ihdr(w=200,h=100,depth=8,color=2,compression=0,filter_category=0,interlace=0):
    return struct.pack('>IIBBBBB',w,h,depth,color,compression,filter_category,interlace)


def frame(seq=0,w=200,h=100,x=0,y=0,disposal=0,blend=0):
    return struct.pack('>IIIIIHHBB',seq,w,h,x,y,1,10,disposal,blend)


def vectors():
    from PIL import Image
    from TESTS.adapters.django.login_png_pixels import MODES
    cases=[]
    def add(name,prefix=b'',header=None,ending=None,valid=True,size=(200,100),mode='RGB',depth=8,color=2,
            interlace=False,frames=1,default=False,comment=None,tile=None,invalid_tile=False,warning=False,stop=None):
        if header is None:header=chunk(b'IHDR',ihdr())
        if ending is None:ending=struct.pack('>I',100)+b'IDAT'
        data=MAGIC+header+prefix+ending
        try:
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter('always');image=Image.open(io.BytesIO(data))
        except (OSError,SyntaxError,ValueError,TypeError,IndexError,struct.error,Image.DecompressionBombError):
            assert not valid, f'{name}: expected valid header rejected'
            cases.append(dict(name=name,data=base64.b64encode(data).decode(),invalid=True));return
        assert valid, f'{name}: invalid header accepted'
        assert image.size==size and image.mode==mode,(name,image.size,image.mode)
        assert image.n_frames==frames and bool(image.default_image)==default,(name,image.n_frames,image.default_image)
        assert bool(image.info.get('interlace'))==interlace,name
        actual_comment=image.info.get('comment')
        actual_comment=actual_comment.encode('utf8') if actual_comment else None
        assert actual_comment==comment,(name,actual_comment,comment)
        assert any(isinstance(w.message,Image.DecompressionBombWarning) for w in captured)==warning,name
        end=len(data) if stop is None else stop
        assert image.fp.tell()==end,(name,image.fp.tell(),end)
        expected_tile=tile if tile is not None else (0,0,*size) if image.tile else (0,0,0,0)
        if image.tile:
            extent=image.tile[0].extents
            if invalid_tile:assert isinstance(extent,str),name
            else:assert extent==(expected_tile[0],expected_tile[1],expected_tile[0]+expected_tile[2],expected_tile[1]+expected_tile[3]),(name,extent,expected_tile)
        kind=data[end-4:end].decode('ascii') if ending[-4:] in (b'IDAT',b'IEND') else 'fdAT' if ending[4:8]==b'fdAT' else 'IDAT'
        length=struct.unpack('>I',ending[:4])[0]-(4 if kind=='fdAT' else 0)
        cases.append(dict(name=name,data=base64.b64encode(data).decode(),invalid=False,width=size[0],height=size[1],mode=mode,
            depth=depth,color=color,interlace=interlace,frames=frames,default=default,comment=base64.b64encode(comment).decode() if comment is not None else None,
            tile=expected_tile,invalid_tile=invalid_tile,warning=warning,header_bytes=end,data_length=length,data_kind=kind))
        image.close()
    add('header only')
    add('zero length IDAT',ending=struct.pack('>I',0)+b'IDAT')
    pixels=chunk(b'IDAT',zlib.compress(bytes(601*100)))
    add('complete image stops before pixels',ending=pixels+chunk(b'IEND'),stop=41)
    broken=bytearray(pixels);broken[-1]^=1
    add('pixel CRC is deferred',ending=bytes(broken)+chunk(b'IEND'),stop=41)
    add('post-pixel bad metadata is deferred',ending=pixels+b'broken metadata',stop=41)
    add('IEND before pixels',ending=struct.pack('>I',0xffffffff)+b'IEND')
    for d,c,m,_ in MODES:add(f'mode {d}/{c}',header=chunk(b'IHDR',ihdr(depth=d,color=c)),mode=m,depth=d,color=c)
    for value in (1,2,7,255):
        add(f'compression ignored {value}',header=chunk(b'IHDR',ihdr(compression=value)))
        add(f'filter category {value}',header=chunk(b'IHDR',ihdr(filter_category=value)),valid=False)
        add(f'interlace nonzero {value}',header=chunk(b'IHDR',ihdr(interlace=value)),interlace=True)
    for w,h,valid,warn in [(0,100,False,False),(100,0,False,False),(89478485,1,True,False),(89478486,1,True,True),(178956970,1,True,True),(178956971,1,False,False),(0xffffffff,0xffffffff,False,False)]:
        add(f'dimensions {w}x{h}',header=chunk(b'IHDR',ihdr(w,h)),size=(w,h),valid=valid,warning=warn)
    for length in range(17):add(f'IHDR length {length}',header=chunk(b'IHDR',ihdr()[:length]+bytes(max(0,length-13))),valid=length>=13)
    for depth,color in [(0,0),(3,0),(8,1),(16,3),(4,6)]:
        add(f'unknown initial mode {depth}/{color}',header=chunk(b'IHDR',ihdr(depth=depth,color=color)),valid=False)
        add(f'unknown later mode preserves previous {depth}/{color}',prefix=chunk(b'IHDR',ihdr(17,13,depth,color)),size=(17,13))
    add('later IHDR replaces dimensions/mode',prefix=chunk(b'IHDR',ihdr(17,13,8,0)),size=(17,13),mode='L',color=0)
    add('interlace remains sticky',header=chunk(b'IHDR',ihdr(interlace=1)),prefix=chunk(b'IHDR',ihdr()),interlace=True)
    for kind,min_len,multiple in [(b'gAMA',4,1),(b'sRGB',1,1),(b'pHYs',9,1),(b'cHRM',32,1),(b'PLTE',0,1),(b'eXIf',0,1),(b'123_',0,1),(b'ZZZZ',0,1)]:
        for n in range(12):add(f'{kind!r} length {n}',prefix=chunk(kind,bytes(n)),valid=n>=min_len and n%multiple==0)
    for n in (31,32,33,35):add(f'cHRM boundary {n}',prefix=chunk(b'cHRM',bytes(n)),valid=n>=32)
    for color,mode,minimum in [(0,'L',2),(2,'RGB',6),(3,'P',0),(4,'LA',0),(6,'RGBA',0)]:
        for n in range(8):add(f'tRNS color {color} length {n}',header=chunk(b'IHDR',ihdr(color=color)),prefix=chunk(b'tRNS',bytes(n)),mode=mode,color=color,valid=n>=minimum)
    for kind in (b'....',b'a bC',b'\xffabc',b'abc\0'):add('invalid chunk id '+kind.hex(),prefix=chunk(kind),valid=False)
    normal=chunk(b'IHDR',ihdr())
    for n in range(len(normal)):add(f'truncated IHDR at {n}',header=normal[:n],ending=b'',valid=False)
    for kind in (b'IHDR',b'tEXt',b'ZZZZ'):
        body=ihdr() if kind==b'IHDR' else b'comment\0hi' if kind==b'tEXt' else b'x'
        bad=bytearray(chunk(kind,body));bad[-1]^=1
        add('bad CRC '+kind.decode(),header=bytes(bad) if kind==b'IHDR' else None,prefix=b'' if kind==b'IHDR' else bytes(bad),valid=False)
    add('IDAT before IHDR is skipped',header=chunk(b'IDAT',b'ignored')+normal)
    add('IDAT missing IHDR',header=b'',ending=chunk(b'IDAT',b'ignored')+struct.pack('>I',0)+b'IEND',valid=False)
    add('declared header payload truncated',prefix=chunk(b'ZZZZ',b'x',1000),valid=False)
    # Text is Latin-1 for tEXt/zTXt and strict UTF-8 for iTXt. Header comments
    # become UTF-8 JPEG bytes; an empty string differs from an empty JPEG COM.
    for value in (b'',b'hello',b'caf\xe8',b'\0binary\xff'):
        expected=value.decode('latin1').encode() or None
        add('plain comment '+value.hex(),prefix=chunk(b'tEXt',b'comment\0'+value),comment=expected)
        add('compressed comment '+value.hex(),prefix=chunk(b'zTXt',b'comment\0\0'+zlib.compress(value)),comment=expected)
    def itxt(key,value,flag=0,method=0,lang=b'',translated=b''):
        return key+b'\0'+bytes([flag,method])+lang+b'\0'+translated+b'\0'+value
    for value in (b'',b'hello','漢字'.encode(),b'bad\xff'):
        for flag in (0,1,2):
            compressed=zlib.compress(value) if flag else value
            expected=value or None if value!=b'bad\xff' else None
            add(f'international comment {value.hex()} flag {flag}',prefix=chunk(b'iTXt',itxt(b'comment',compressed,flag)),comment=expected)
    first=chunk(b'tEXt',b'comment\0first')
    for body in (b'',b'comment',b'comment\0',b'comment\0\1',itxt(b'comment',b'last',lang=b'\xff'),itxt(b'comment',b'last',translated=b'\xff'),itxt(b'comment',b'bad\xff'),itxt(b'comment',b'bad',1,1),itxt(b'comment',b'bad',1)):
        add('ignored iTXt preserves prior '+body.hex(),prefix=first+chunk(b'iTXt',body),comment=b'first')
    for body in (b'comment',b'comment\0',b'comment\0\0',b'comment\0\0bad'):
        add('bad zTXt clears prior '+body.hex(),prefix=first+chunk(b'zTXt',body))
    add('invalid zTXt compression',prefix=chunk(b'zTXt',b'comment\0\1bad'),valid=False)
    for n in range(13):
        packed=zlib.compress(b'hello')
        decompressor=zlib.decompressobj();expected=decompressor.decompress(packed[:n])
        # Fixed known prefix outputs for this stream, not inferred acceptance.
        assert expected==b'hello'[:max(0,min(5,n-3))]
        add(f'truncated zTXt {n}',prefix=first+chunk(b'zTXt',b'comment\0\0'+packed[:n]),comment=expected or None)
        add(f'truncated iTXt {n}',prefix=first+chunk(b'iTXt',itxt(b'comment',packed[:n],1)),comment=expected or None)
    for body,valid in [(b'',False),(b'x',False),(b'x\0',False),(b'x\0\1abc',False),(b'x\0\0bad',True),(b'x\0\0'+zlib.compress(b'profile'),True)]:add('ICC '+body.hex(),prefix=chunk(b'iCCP',body),valid=valid)
    for n in ((1<<20)-1,1<<20,(1<<20)+1):
        packed=zlib.compress(b'x'*n)
        for kind,body in [(b'zTXt',b'note\0\0'+packed),(b'iTXt',itxt(b'note',packed,1)),(b'iCCP',b'profile\0\0'+packed)]:
            add(f'{kind!r} inflated {n}',prefix=chunk(kind,body),valid=n<=1<<20)
    packed=chunk(b'zTXt',b'note\0\0'+zlib.compress(b'x'*(1<<20)))
    add('64 MiB cumulative text accepted',prefix=packed*64)
    add('65 MiB cumulative text rejected',prefix=packed*65,valid=False)
    for text,flag in [(b'',False),(b'0',True),(b'1',True)]:
        add('text interlace '+text.hex(),prefix=chunk(b'tEXt',b'interlace\0'+text),interlace=flag)
    add('text clears previous interlace',header=chunk(b'IHDR',ihdr(interlace=1)),prefix=chunk(b'tEXt',b'interlace\0'))
    add('text bbox accepted lazily',prefix=chunk(b'tEXt',b'bbox\0invalid'),invalid_tile=True,tile=(0,0,0,0))
    # First-frame APNG declarations; no pixel/frame seek is performed here.
    act=lambda n:chunk(b'acTL',struct.pack('>II',n,0))
    for n in (0,1,2,0x80000000,0x80000001):
        valid_frames=0<n<=0x80000000
        add(f'APNG count {n}',prefix=act(n),frames=n+1 if valid_frames else 1,default=valid_frames)
    add('duplicate acTL disables animation',prefix=act(2)+act(3))
    add('third acTL re-enables animation',prefix=act(2)+act(3)+act(4),frames=5,default=True)
    add('controlled first frame',prefix=act(2)+chunk(b'fcTL',frame()),frames=2)
    add('early IDAT sets default before mode exists',header=act(2)+chunk(b'IDAT',b'ignored')+normal,prefix=chunk(b'fcTL',frame()),frames=3,default=True)
    # An AttributeError for missing raw mode falls through the unknown-chunk
    # handler after fdAT's sequence read; its CRC then covers the next payload.
    displaced=struct.pack('>I',4)+b'fdAT'+struct.pack('>I',1)+b'next'+struct.pack('>I',zlib.crc32(b'fdATnext'))
    add('early fdAT unknown-handler consumption',header=act(2)+chunk(b'fcTL',frame(w=0,h=0))+displaced+normal,prefix=chunk(b'fcTL',frame(seq=2)),frames=2)
    add('IEND after control has no tile',prefix=act(2)+chunk(b'fcTL',frame()),ending=struct.pack('>I',0)+b'IEND',frames=2)
    add('IEND after standalone control has no tile',prefix=chunk(b'fcTL',frame()),ending=struct.pack('>I',0)+b'IEND')
    add('partial first frame',prefix=act(2)+chunk(b'fcTL',frame(w=17,h=13,x=4,y=5)),frames=2,tile=(4,5,17,13))
    add('frame control without animation',prefix=chunk(b'fcTL',frame(w=17,h=13,x=4,y=5)),tile=(4,5,17,13))
    add('later frame control wins',prefix=act(2)+chunk(b'fcTL',frame())+chunk(b'fcTL',frame(seq=1,w=17,h=13)),frames=2,tile=(0,0,17,13))
    for seq in (1,2,0xffffffff):add(f'bad initial sequence {seq}',prefix=chunk(b'fcTL',frame(seq=seq)),valid=False)
    add('bad repeated sequence',prefix=chunk(b'fcTL',frame())*2,valid=False)
    for w,h,x,y in [(201,100,0,0),(200,101,0,0),(200,100,1,0),(200,100,0,1),(0xffffffff,1,1,0)]:add(f'outside frame {w,h,x,y}',prefix=chunk(b'fcTL',frame(w=w,h=h,x=x,y=y)),valid=False)
    add('zero-sized frame accepted lazily',prefix=act(2)+chunk(b'fcTL',frame(w=0,h=0)),frames=2,tile=(0,0,0,0))
    for kind,n,body in [(b'acTL',8,struct.pack('>II',2,0)),(b'fcTL',26,frame())]:
        for length in range(n):add(f'truncated {kind!r} {length}',prefix=chunk(kind,body[:length]),valid=False)
    for length in range(4):add(f'truncated fdAT {length}',prefix=act(2)+chunk(b'fcTL',frame()),ending=struct.pack('>I',length)+b'fdAT'+bytes(length),valid=False)
    add('fdAT first pixels',prefix=act(2)+chunk(b'fcTL',frame()),ending=struct.pack('>I',104)+b'fdAT'+struct.pack('>I',1),frames=2)
    add('fdAT bad sequence',prefix=chunk(b'fcTL',frame()),ending=struct.pack('>I',104)+b'fdAT'+struct.pack('>I',2),valid=False)
    for text,flag in [(b'',False),(b'0',True)]:add('text default_image '+text.hex(),prefix=act(2)+chunk(b'fcTL',frame())+chunk(b'tEXt',b'default_image\0'+text),frames=3 if flag else 2,default=flag)
    for disposal in (0,1,2,3,255):
        add(f'text bbox disposal {disposal}',prefix=act(2)+chunk(b'fcTL',frame(disposal=disposal))+chunk(b'tEXt',b'bbox\0invalid'),frames=2,invalid_tile=True,tile=(0,0,0,0))
    return cases


def run(work,log,tool_env,django_helper=None):
    cases=vectors()
    assert len(cases)==398
    if django_helper is not None:
        from PIL import ImageFile
        from unittest.mock import patch
        loads=[]
        original_load=ImageFile.ImageFile.load
        def observed_load(image,*args,**kwargs):
            loads.append(image.size)
            return original_load(image,*args,**kwargs)
        checked=0
        with patch.object(ImageFile.ImageFile,'load',observed_load),warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for case in cases:
                if case['invalid'] or (case['width'],case['height'])!=(200,100):continue
                assert len(base64.b64decode(case['data']))<=1024*1024
                uri='opaque,'+case['data']
                assert django_helper(uri)==uri,case['name']
                checked+=1
        assert not loads,'Django attempted pixel loading on header-only passthrough'
        print(f'Django PNG lazy passthrough: {checked} cases, zero pixel loads',flush=True)
    path=work/'png-header.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    print(f'PNG header oracle: {len(cases)} explicit lazy/metadata/APNG admission cases',flush=True)
    try:subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestPNG(Header|Metadata)'],cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_PNG_HEADER_FIXTURE':str(path)},check=True,timeout=180)
    finally:path.unlink(missing_ok=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-png-header-',dir='/tmp') as directory:run(Path(directory),None,env)
