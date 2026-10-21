"""BMP/DIB oracle with declared samples and explicit malformed/RLE outcomes.

Pillow's file reader must agree with the declared header/pixels. JPEG expectations
are encoded from those samples, independently of BMP decoding. The accounts
adapter additionally runs every whole-file expectation through the Django helper.
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

ROOT = Path(__file__).resolve().parents[3]


def bitmap(w, h, bits, pixels=b'', *, size=40, dib=False, comp=0, colors=0, palette=b'', masks=None, offset=None, gap=b''):
    if size == 12:
        header = struct.pack('<IHHHH', size, w, h, 1, bits)
    else:
        header = struct.pack('<IIIHHIIiiII', size, w & 0xffffffff, h & 0xffffffff, 1, bits, comp, 0, 0, 0, colors, 0) + bytes(size-40)
    if masks is not None:
        words = b''.join(struct.pack('<I', v) for v in masks)
        if size == 40: header += words[:12]
        else: header = header[:40] + words[:size-40] + header[40+len(words):]
    if dib: return header + palette + gap + pixels
    if offset is None: offset = 14 + len(header) + len(palette) + len(gap)
    return b'BM' + bytes(8) + struct.pack('<I', offset) + header + palette + gap + pixels


def vectors():
    from PIL import Image
    cases = []
    def jpeg(mode, size, pixels, target=None):
        if mode == 'P': return None
        if mode == 'RGBA':
            pixels = bytes((pixels[i+c]*pixels[i+3] + 255*(255-pixels[i+3]) + 127)//255 for i in range(0,len(pixels),4) for c in range(3))
            mode = 'RGB'
        image = Image.frombytes('L' if mode == '1' else mode, size, pixels)
        if mode == '1': image = image.convert('1', dither=Image.Dither.NONE)
        if target is not None: image = image.resize(target, Image.Resampling.LANCZOS)
        out = io.BytesIO(); image.save(out, format='JPEG'); return out.getvalue()
    def add(name, data, *, mode='RGB', raw='BGR', w=3, h=2, pixels=None, invalid=False, lazy=False, target=None):
        header = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore'); image = Image.open(io.BytesIO(data))
            assert not invalid, (name, 'invalid header accepted')
            assert image.mode == mode and image.size == (w,h), (name, image.mode, image.size)
            assert image.tile[0].args[0] == raw, (name, image.tile)
            header = dict(width=w, height=h, mode=mode, rawmode=raw, dataoffset=image.tile[0].offset,
                          headerbytes=image.fp.tell(), format=image.format, topdown=image.tile[0].args[-1] == 1,
                          pixelwarning=w*h>89478485)
        except (OSError, ValueError, SyntaxError, struct.error, Image.DecompressionBombError):
            assert invalid, (name, 'header unexpectedly rejected')
        if header is not None and not lazy:
            try:
                image.load()
                actual = image.convert('L').tobytes() if mode == '1' else image.tobytes()
                assert pixels is not None and actual == pixels, (name, 'pixels differ', actual[:80], pixels and pixels[:80])
            except (OSError, ValueError, SyntaxError, struct.error):
                assert pixels is None, (name, 'declared pixels rejected')
        expected = None if invalid or lazy or pixels is None else jpeg(mode, (w,h), pixels, target)
        # Independent whole-file policy, including lazy bypass and error fallback.
        actual_jpeg = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore'); image = Image.open(io.BytesIO(data))
            ratio = min(200/image.width, 200/image.height)
            dimensions = (int(image.width*ratio), int(image.height*ratio))
            if dimensions != image.size or len(data)>1024*1024:
                if image.mode == 'RGBA':
                    white = Image.new('RGB', image.size, (255,255,255)); white.paste(image, mask=image.split()[3]); image=white
                image = image.resize(dimensions, Image.Resampling.LANCZOS) if len(data)>1024*1024 else image.resize(image.size)
                out = io.BytesIO(); image.save(out, format='JPEG'); actual_jpeg=out.getvalue()
        except (OSError, ValueError, SyntaxError, struct.error, Image.DecompressionBombError): pass
        assert actual_jpeg == expected, (name, 'whole-file policy differs')
        enc=lambda b: base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name, data=enc(data), header=header, pixels=enc(pixels), lazy=lazy, jpeg=enc(expected), keep=expected is None))

    rgb = bytes([10,20,30,40,50,60,70,80,90,100,110,120,130,140,150,160,170,180])
    for dib in (False,True):
        for size in (12,40,52,56,64,108,124):
            for top in (False,True):
                if top and size==12: continue
                rows=[rgb[:9],rgb[9:]]
                if not top: rows.reverse()
                packed=b''.join(bytes(row[i+c] for i in range(0,9,3) for c in (2,1,0))+b'abc' for row in rows)
                data=bitmap(3,-2 if top else 2,24,packed,size=size,dib=dib)
                add(f'24bit dib={dib} header={size} top={top}',data,pixels=rgb)
                for missing in range(1,5):
                    add(f'24bit truncated {missing} dib={dib} header={size} top={top}',data[:-missing],pixels=rgb if missing<=3 else None)
    # Every recognized mask and all quantized 5/6-bit channel levels.
    masks=[(32,(0xff0000,0xff00,0xff,0),'BGRX'),(32,(0xff000000,0xff0000,0xff00,0),'XBGR'),
           (32,(0xff000000,0xff00,0xff,0),'BGXR'),(32,(0xff000000,0xff0000,0xff00,0xff),'ABGR'),
           (32,(0xff,0xff00,0xff0000,0xff000000),'RGBA'),(32,(0xff0000,0xff00,0xff,0xff000000),'BGRA'),
           (32,(0xff000000,0xff00,0xff,0xff0000),'BGAR'),(32,(0,0,0,0),'BGRA'),
           (24,(0xff0000,0xff00,0xff,123),'BGR'),(16,(0xf800,0x7e0,0x1f,123),'BGR;16'),(16,(0x7c00,0x3e0,0x1f,123),'BGR;15')]
    for bits,mask,raw in masks:
        mode='RGBA' if 'A' in raw else 'RGB'
        if bits==16:
            green=63 if raw=='BGR;16' else 31; shift=11 if green==63 else 10
            values=[((i%32)<<shift)|((i%(green+1))<<5)|(31-i%32) for i in range(64)]
            packed=b''.join(struct.pack('<H',v) for v in values)
            pixels=bytes(v for i in range(64) for v in ((i%32)*255//31,(i%(green+1))*255//green,(31-i%32)*255//31))
            w=64
        else:
            w=3; samples=[dict(R=10,G=20,B=30,A=0,X=252),dict(R=80,G=90,B=100,A=128,X=253),dict(R=180,G=190,B=200,A=255,X=254)]
            packed=bytes(s[c] for s in samples for c in raw);packed+=bytes((-len(packed))%4)
            pixels=bytes(s[c] for s in samples for c in mode)
        for dib in (False,True):
            for size in (40,52,56,64,108,124):
                if size<56 and bits==32 and mask[3]!=0: continue
                add(f'masks {mask} {raw} dib={dib} size={size}',bitmap(w,1,bits,packed,size=size,dib=dib,comp=3,masks=mask),w=w,h=1,mode=mode,raw=raw,pixels=pixels)
    for dib in (False,True):
        for bits in (1,4,8):
            for size in (12,40):
                padding=3 if size==12 else 4
                for gray in (False,True):
                    n=1<<bits
                    palette=b''.join(bytes([i if n!=2 else i*255]*3 if gray else [i,255-i,99])+bytes(padding-3) for i in range(n))
                    raw='1' if gray and bits==1 else 'L' if gray else {1:'P;1',4:'P;4',8:'P'}[bits]
                    mode=raw if gray else 'P';w=3
                    packed=b'\xa5\x23\x45\0' if bits<=4 else b'\x01\x80\xff\0'
                    pixels=(bytes([255,0,255]) if bits==1 else packed[:3]) if gray else (bytes([1,0,1]) if bits==1 else bytes([10,5,2]) if bits==4 else packed[:3])
                    add(f'palette {bits} dib={dib} size={size} gray={gray}',bitmap(w,1,bits,packed,size=size,dib=dib,palette=palette),w=w,h=1,mode=mode,raw=raw,pixels=pixels)
    for bits in (1,4,8):
        for count in (1,2,3,16,255,256):
            palette=b''.join(bytes([i if count!=2 else i*255])*3+b'\xff' for i in range(count))
            mode='1' if count==2 else 'L'; raw=mode
            add(f'nonstandard gray palette {bits}/{count}',bitmap(3,1,bits,b'\xa5\x23\x45\0',colors=count,palette=palette),w=3,h=1,mode=mode,raw=raw,pixels=bytes([255,0,255]) if mode=='1' else b'\xa5\x23\x45')
    palette=b''.join(bytes([i])*3+b'\0' for i in range(3))
    add('gray palette raw stride too small',bitmap(9,1,4,bytes(12),colors=3,palette=palette),w=9,h=1,mode='L',raw='L')
    for n in range(13):
        data=bitmap(1,1,8,colors=3,palette=palette[:n])
        # All three RGB triples are present at length 11, regardless of padding.
        mode='L' if n>=11 else 'P'
        add(f'partial palette {n}',data,w=1,h=1,mode=mode,raw=mode)
    for bits in (16,32):
        raw='BGR;15' if bits==16 else 'BGRX'
        packed=b'\xff\xff\0\0' if bits==16 else b'\x1e\x14\x0a\xff'
        add(f'raw {bits}',bitmap(1,1,bits,packed),w=1,h=1,raw=raw,pixels=bytes([255]*3) if bits==16 else bytes([10,20,30]))
    # RLE explicit cases: clipping, empty lines, odd absolute counts, delta bug.
    streams=[
        ('encoded',3,2,b'\3\x42\3\x99',None), # x remains full without EOL.
        ('rows',3,2,b'\3\x42\0\0\3\x99',b'\x42'*3+b'\x99'*3),
        ('clip',3,1,b'\xff\x42',b'\x42'*3),
        ('eol pad',3,1,b'\1\x42\0\0',b'\x42\0\0'),
        ('empty eol',3,1,b'\0\0\1\x42\0\0',b'\x42\0\0'),
        ('early eob',3,1,b'\1\x42\0\1',None),
        ('no eob',3,1,b'\3\x42',b'\x42'*3),
        ('absolute',3,1,b'\0\3\x11\x22\x33',b'\x11\x22\x33'),
        ('absolute over row',3,2,b'\0\6\1\2\3\4\5\6',bytes(range(1,7))),
        ('absolute short enough',3,1,b'\0\5\1\2\3',b'\1\2\3'),
        ('absolute short insufficient',3,1,b'\0\5\1\2',None),
        ('delta second pair',3,2,b'\1\x42\0\2\x99\x99\1\1\1\x77',b'\x42\0\0\0\0\x77'),
        ('delta overshoot',3,1,b'\0\2\x99\x99\xff\xff',bytes(3)),
        ('delta first short',3,1,b'\0\2\1',None),
        ('delta second short',3,1,b'\0\2\1\1\1',None),
        ('absolute odd alignment',3,2,b'\0\3\1\2\3\0\0\0\3\4',b'\1\2\3\4\4\4'),
    ]
    gray=b''.join(bytes([i])*3+b'\0' for i in range(256))
    add('RLE8 odd absolute offset',bitmap(3,-2,8,b'\0\3\1\2\3\0\0\3\4',comp=1,palette=gray,gap=b'x'),mode='L',raw='L',pixels=b'\1\2\3\4\4\4')
    for name,w,h,stream,decoded in streams:
        for top in (False,True):
            for dib in (False,True):
                pixels=decoded if top or decoded is None else b''.join(decoded[i*w:(i+1)*w] for i in reversed(range(h)))
                add(f'RLE8 {name} top={top} dib={dib}',bitmap(w,-h if top else h,8,stream,comp=1,dib=dib,palette=gray),w=w,h=h,mode='L',raw='L',pixels=pixels)
    for name,w,h,stream,expected in [
        ('encoded odd',3,1,b'\3\xab',b'\x0a\x0b\x0a'),
        ('absolute even',4,1,b'\0\4\x12\x34',b'\1\2\3\4'),
        ('absolute odd loses last',3,1,b'\0\3\x12\0',None),
        ('absolute odd EOL pads',3,1,b'\0\3\x12\0\0\0',b'\1\2\0'),
        ('absolute odd follows encoded',3,1,b'\0\3\x12\0\1\x34',None),
        ('absolute overshoots',3,1,b'\0\4\x12\x34',b'\1\2\3'),
    ]:
        for bits in (4,8):
            add(f'RLE4 {name} bits={bits}',bitmap(w,-h,bits,stream,comp=2,palette=gray,colors=256),w=w,h=h,mode='L',raw='L',pixels=expected)
    for bits in (1,4,8,16,24,32):
        for comp in (1,2):
            palette=b'\1\2\3\0'* (1<<bits) if bits<=8 else b''
            raw={1:'P;1',4:'P;4',8:'P',16:'BGR;15',24:'BGR',32:'BGRX'}[bits]
            add(f'RLE arbitrary depth {bits}/{comp}',bitmap(1,1,bits,b'\1\xab',comp=comp,palette=palette),w=1,h=1,mode='P' if bits<=8 else 'RGB',raw=raw,pixels=bytes([171 if comp==1 else 10]) if bits<=8 else None)
    for comp in (1,2):
        add(f'RLE mode1 rejected {comp}',bitmap(1,1,1,b'\1\xab',comp=comp,palette=b'\0'*4+b'\xff'*3+b'\0'),w=1,h=1,mode='1',raw='1')
    # Header rejection, ignored fields, offsets and admission without pixel reads.
    base=bitmap(3,2,24,b'\0'*24)
    for cut in range(54):add(f'truncated header {cut}',base[:cut],invalid=True)
    for size in (0,4,8,13,39,41,128,0xffffffff):
        data=bytearray(base);struct.pack_into('<I',data,14,size);add(f'bad header size {size}',bytes(data),invalid=True)
    for bits in (0,2,15,48,65535):add(f'bad depth {bits}',bitmap(3,2,bits),invalid=True)
    for comp in (4,5,6,0xffffffff):add(f'bad compression {comp}',bitmap(3,2,24,comp=comp),invalid=True)
    for count in (257,65536):
        add(f'large missing palette {count}',bitmap(3,2,8,colors=count),mode='P',raw='P')
        palette=b''.join(bytes([i%256])*3+b'\0' for i in range(count))
        add(f'wrapped gray palette {count}',bitmap(3,1,8,b'\1\2\3\0',colors=count,palette=palette),h=1,mode='L',raw='L',pixels=b'\1\2\3')
        add(f'oversize colored palette {count}',bitmap(3,1,8,b'\1\2\3\0',colors=count,palette=b'\1\2\3\0'*count),h=1,mode='P',raw='P')
    for n in range(1023,1029):
        add(f'palette raw length {n}',bitmap(1,1,8,colors=257,palette=b'\1'*n,offset=10),w=1,h=1,mode='P',raw='P',pixels=b'\x0a' if n<1028 else None)
    for count in (65537,0xffffffff):add(f'bad palette count {count}',bitmap(3,2,8,colors=count),invalid=True)
    for bits in (1,4,8,16,24,32):add(f'bad masks {bits}',bitmap(3,2,bits,comp=3,masks=(1,2,4,8)),invalid=True)
    for w,h in ((0,1),(1,0),(178956971,1),(0xffffffff,1),(1,0xfeffffff)):
        add(f'bad dimensions {w}/{h}',bitmap(w,h,24),invalid=True)
    for field,width,value in ((2,4,1),(6,4,0xffffffff),(26,2,0),(26,2,42),(34,4,0xffffffff),(38,4,0xffffffff),(46,4,0xffffffff)):
        data=bytearray(base);struct.pack_into('<I' if width==4 else '<H',data,field,value)
        add(f'ignored field {field}/{value}',bytes(data),pixels=bytes(18))
    for off in (0,1,54,55,100,0xffffffff):
        # Offset 1 deliberately decodes header bytes as two bottom-up BGR rows.
        pixels=bytes([0,40,0,3,0,0,0,0,0,0,0,77,0,0,0,0,0,0]) if off==1 else None
        add(f'data offset {off}',bitmap(3,2,24,offset=off),pixels=pixels)
    add('offset gap',bitmap(3,2,24,bytes(24),gap=b'ignored'),pixels=bytes(18))
    # Offset equal to header end is corrected by 4*colors, even for OS/2.
    for size in (12,40):
        pal=(b'\0'*3+b'\xff'*3) if size==12 else b'\0'*4+b'\xff'*3+b'\0'
        data=bitmap(3,1,1,b'\xa0\0\0\0',size=size,palette=pal,offset=14+size)
        add(f'palette offset adjustment {size}',data,w=3,h=1,mode='1',raw='1',pixels=b'\0\0\0' if size==12 else b'\xff\0\xff')
    for dib in (False,True):
        add(f'lazy no pixels dib={dib}',bitmap(200,100,24,dib=dib),w=200,h=100,lazy=True)
    add('pixel warning lazy',bitmap(200,447393,24),w=200,h=447393,pixels=None)
    for n in (1024*1024,1024*1024+1):
        data=bitmap(3,2,24,bytes(24)); data+=bytes(n-len(data))
        add(f'size threshold {n}',data,pixels=bytes(18),target=(200,133) if n>1024*1024 else None)
    return cases


def run(work, log, tool_env, django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec, build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data']; expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'bmp-codec';build_codec(codec,log,tool_env)
    fault=work/'bmp-fault';build_fault_codec(fault,log,tool_env)
    path=work/'bmp.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestBMP'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_BMP_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'BMP/DIB oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-bmp-',dir='/tmp') as directory:run(Path(directory),None,env)
