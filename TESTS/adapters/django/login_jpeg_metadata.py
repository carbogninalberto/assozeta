"""JPEG first-frame EXIF/MPF admission with explicitly expected Pillow outcomes."""
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
UNITS={1:1,2:1,3:2,4:4,5:8,6:1,7:1,8:2,9:4,10:8,11:4,12:8,13:4,16:8}


def tiff(entries,order='<',magic=None):
    prefix=magic if magic is not None else b'II*\0' if order=='<' else b'MM\0*'
    start=8+2+12*len(entries)+4
    directory=bytearray(prefix+struct.pack(order+'IH',8,len(entries)))
    extra=bytearray()
    for entry in entries:
        tag,kind,data,*override=entry
        count=override[0] if override else len(data)//UNITS.get(kind,1)
        field=data.ljust(4,b'\0') if len(data)<=4 else struct.pack(order+'I',start+len(extra))
        if len(data)>4:extra+=data
        directory+=struct.pack(order+'HHI',tag,kind,count)+field
    return bytes(directory)+bytes(4)+bytes(extra)


def marker(code,payload):return bytes([255,code])+struct.pack('>H',len(payload)+2)+payload


def vectors():
    from PIL import Image
    cases=[]
    image=Image.frombytes('RGB',(17,13),bytes((i*73)&255 for i in range(17*13*3)))
    out=io.BytesIO();image.save(out,format='JPEG');base=out.getvalue()
    def jpeg(exif=None,mpf=None,jfif=False,hdr=False):
        prefix=b''
        if jfif:prefix+=marker(224,b'JFIF\0\1\1\1\0H\0H')
        if exif is not None:prefix+=marker(225,b'Exif\0\0'+exif)
        if mpf is not None:prefix+=marker(226,b'MPF\0'+mpf)
        if hdr:prefix+=marker(225,b' hdrgm:Version="1.0"')
        return base[:2]+prefix+base[2:]
    def add(name,data,valid=True,fmt='JPEG',frames=1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                opened=Image.open(io.BytesIO(data))
        except (OSError,SyntaxError,ValueError):
            assert not valid,f'{name}: expected accepted JPEG rejected'
            cases.append(dict(name=name,data=base64.b64encode(data).decode(),invalid=True));return
        assert valid,f'{name}: expected rejection did not occur'
        assert opened.format==fmt and getattr(opened,'n_frames',1)==frames,name
        assert opened.size==(17,13) and opened.mode=='RGB',name
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            loaded=opened.resize(opened.size);encoded=io.BytesIO();loaded.save(encoded,format='JPEG')
        cases.append(dict(name=name,data=base64.b64encode(data).decode(),invalid=False,format=fmt,frames=frames,
            expected=base64.b64encode(encoded.getvalue()).decode()))
        opened.close()
    add('plain JPEG',base)
    for order in ('<','>'):
        unit=(0x128,3,struct.pack(order+'H',2))
        for kind,raw,valid in [
            (1,b'H',False),(1,b'H\1',True),(1,b'H\0',True),
            (7,b'H',False),(7,b'H\1',True),
            (2,b'\0',False),(2,b'3\0',False),(2,b'3',False),(2,b'a\0',True),(2,b'12\0',True),
            (3,struct.pack(order+'H',72),True),(4,struct.pack(order+'I',72),True),
            (5,struct.pack(order+'II',72,1),True),(5,struct.pack(order+'II',1,0),True),
            (6,b'\xff',True),(8,struct.pack(order+'h',-72),True),(9,struct.pack(order+'i',-72),True),
            (10,struct.pack(order+'ii',72,1),True),(11,struct.pack(order+'f',float('nan')),True),
            (12,struct.pack(order+'d',float('inf')),True),(13,struct.pack(order+'I',72),True),(16,struct.pack(order+'Q',2**64-1),True),
        ]:
            exif=tiff([unit,(0x11a,kind,raw)],order)
            add(f'EXIF {order} type {kind} raw {raw.hex()}',jpeg(exif=exif),valid)
            add(f'JFIF bypass {order} {kind} {raw.hex()}',jpeg(exif=exif,jfif=True))
            add(f'no resolution unit {order} {kind} {raw.hex()}',jpeg(exif=tiff([(0x11a,kind,raw)],order)))
        # Zero/unknown values do not replace a previously read tag. A malformed
        # external payload stops IFD reading, preserving earlier valid entries.
        bad=(0x11a,1,b'H')
        add(f'duplicate empty preserves bad DPI {order}',jpeg(exif=tiff([unit,bad,(0x11a,1,b'',0)],order)),False)
        add(f'duplicate unknown preserves bad DPI {order}',jpeg(exif=tiff([unit,bad,(0x11a,99,b'')],order)),False)
        add(f'partial directory preserves bad DPI {order}',jpeg(exif=tiff([unit,bad,(999,4,b'\0',2**32-1)],order)),False)
        add(f'early broken field hides DPI {order}',jpeg(exif=tiff([(999,4,b'\0',2**32-1),unit,bad],order)))
    for raw in (b'',b'garbage',b'II+\0'+bytes(12),b'MM\0+'+bytes(12),b'Exif\0\0'):
        add('unreadable EXIF '+raw.hex(),jpeg(exif=raw))
    def records(order='<',attributes=(0,0)):
        return b''.join(struct.pack(order+'IIIHH',attribute,0,0,0,0) for attribute in attributes)
    for order in ('<','>'):
        count=lambda n:(0xb001,4,struct.pack(order+'I',n))
        entries=lambda raw,kind=7:(0xb002,kind,raw)
        for n in (0,1,2,3):
            add(f'MPF {order} count {n}',jpeg(mpf=tiff([count(n),entries(records(order,(0,)*max(1,n)))],order)),fmt='MPO' if n>1 else 'JPEG',frames=max(1,n))
        add(f'MPF {order} empty entry omitted',jpeg(mpf=tiff([count(2),entries(b'')],order)))
        add(f'MPF {order} missing number',jpeg(mpf=tiff([entries(records(order))],order)))
        add(f'MPF {order} missing entries',jpeg(mpf=tiff([count(2)],order)))
        for length in (1,15,16,17,31):
            add(f'MPF {order} truncated records {length}',jpeg(mpf=tiff([count(2),entries(records(order)[:length])],order)),False)
        add(f'MPF {order} unsupported format',jpeg(mpf=tiff([count(2),entries(records(order,(0x01000000,0)))],order)))
        add(f'MPF {order} unsupported before truncation',jpeg(mpf=tiff([count(2),entries(records(order,(0x01000000,)))],order)))
        add(f'MPF {order} unsupported second',jpeg(mpf=tiff([count(2),entries(records(order,(0,0x01000000)))],order)))
        add(f'MPF {order} HDR suppresses adoption',jpeg(mpf=tiff([count(2),entries(records(order))],order),hdr=True))
        add(f'MPF {order} HDR does not bypass corrupt entries',jpeg(mpf=tiff([count(2),entries(b'x')],order),hdr=True),False)
        add(f'MPF {order} BYTE records',jpeg(mpf=tiff([count(2),entries(records(order),1)],order)),fmt='MPO',frames=2)
        add(f'MPF {order} string records',jpeg(mpf=tiff([count(2),entries(records(order),2)],order)))
        add(f'MPF {order} numeric records',jpeg(mpf=tiff([count(2),entries(struct.pack(order+'I',1),4)],order)))
        for kind,raw,integer in [(1,b'2',False),(2,b'2\0',False),(3,struct.pack(order+'H',2),True),
                (5,struct.pack(order+'II',2,1),False),(6,b'\2',True),(8,struct.pack(order+'h',2),True),
                (9,struct.pack(order+'i',2),True),(11,struct.pack(order+'f',2),False),(12,struct.pack(order+'d',2),False),
                (13,struct.pack(order+'I',2),True),(16,struct.pack(order+'Q',2),True)]:
            add(f'MPF {order} number type {kind}',jpeg(mpf=tiff([(0xb001,kind,raw),entries(records(order))],order)),fmt='MPO' if integer else 'JPEG',frames=2 if integer else 1)
        add(f'MPF {order} negative count',jpeg(mpf=tiff([(0xb001,9,struct.pack(order+'i',-1)),entries(b'x')],order)))
        add(f'MPF {order} enormous count',jpeg(mpf=tiff([(0xb001,16,struct.pack(order+'Q',2**64-1)),entries(records(order))],order)),False)
        add(f'MPF {order} array number truncates',jpeg(mpf=tiff([(0xb001,4,struct.pack(order+'II',2,99)),entries(records(order))],order)),fmt='MPO',frames=2)
        add(f'MPF {order} partial trailing directory',jpeg(mpf=tiff([count(2),entries(records(order)),(999,4,b'\0',2**32-1)],order)),fmt='MPO',frames=2)
    for magic,order,entry_order,fmt in [(b'II\0*','<','<','MPO'),(b'MM*\0','>','>','JPEG'),(b'MM*\0','>','<','MPO'),(b'MM\0+','>','<','MPO'),(b'II+\0','<','<','JPEG')]:
        add('alternate TIFF magic '+magic.hex()+entry_order,jpeg(mpf=tiff([(0xb001,4,struct.pack(order+'I',2)),(0xb002,7,records(entry_order,(1,1)))],order,magic)),fmt=fmt,frames=2 if fmt=='MPO' else 1)
    for raw in (b'',b'garbage',b'II*\0',b'MM\0*'+bytes(4)):
        add('unreadable MPF '+raw.hex(),jpeg(mpf=raw))
    mpo=io.BytesIO();image.save(mpo,format='MPO',save_all=True,append_images=[Image.new('RGB',(7,9),(255,0,0))])
    add('real two-frame MPO uses first frame',mpo.getvalue(),fmt='MPO',frames=2)
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log),warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for case in cases:
                uri='opaque,'+case['data']
                expected=uri if case['invalid'] else 'data:image/jpeg;base64,'+case['expected']
                assert django_helper(uri)==expected,case['name']
    codec=work/'metadata-codec';build_codec(codec,log,tool_env)
    path=work/'jpeg-metadata.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    print(f'JPEG metadata oracle: {len(cases)} explicit EXIF/MPF first-frame cases',flush=True)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestJPEGMetadata'],cwd=ROOT/'BE_GO',check=True,timeout=180,
            env={**tool_env,'ASSOZETA_JPEG_METADATA_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec)})
    finally:path.unlink(missing_ok=True);codec.unlink(missing_ok=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-jpeg-metadata-',dir='/tmp') as directory:
        run(Path(directory),None,tool_env)
