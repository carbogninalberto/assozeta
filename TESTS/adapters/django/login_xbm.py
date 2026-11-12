"""Declared XBM header, scanner samples and whole-file login outcomes."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import warnings

ROOT=Path(__file__).resolve().parents[3]


def xbm(w,h):
    return f'#define im_width {w}\n#define im_height {h}\nstatic char im_bits[] = {{\n'.encode()


def vectors():
    from PIL import Image
    cases=[]
    def jpeg(size,pixels,target=None):
        im=Image.frombytes('L',size,pixels).convert('1',dither=Image.Dither.NONE)
        if target is not None:im=im.resize(target,Image.Resampling.LANCZOS)
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,data,*,w=8,h=1,pixels=None,invalid=False,lazy=False,offset=None):
        expected_header=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            offset=data.index(b'_bits[] = {')+len(b'_bits[] = {\n') if offset is None else offset
            assert im.format=='XBM' and im.mode=='1' and im.size==(w,h) and im.tile[0].offset==offset,(name,im.format,im.mode,im.size,im.tile)
            expected_header=dict(width=w,height=h,mode='1',dataoffset=offset,headerbytes=offset,pixelwarning=w*h>89478485)
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,Image.DecompressionBombError):assert invalid,(name,'header rejected')
        if expected_header is not None and not lazy:
            try:
                im.load();actual=im.convert('L').tobytes()
                assert pixels is not None and actual==pixels,(name,'pixels differ',actual[:90],pixels and pixels[:90])
            except (OSError,ValueError,SyntaxError,IndexError):assert pixels is None,(name,'pixels rejected')
        target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h))) if len(data)>1024*1024 and not invalid else None
        expected=None if invalid or lazy or pixels is None else jpeg((w,h),pixels,target)
        actual=None
        try:
            with warnings.catch_warnings():warnings.simplefilter('ignore');im=Image.open(io.BytesIO(data))
            ratio=min(200/im.width,200/im.height);size=(int(im.width*ratio),int(im.height*ratio))
            if size!=im.size or len(data)>1024*1024:
                im=im.resize(size,Image.Resampling.LANCZOS) if len(data)>1024*1024 else im.resize(im.size)
                out=io.BytesIO();im.save(out,format='JPEG');actual=out.getvalue()
        except (OSError,ValueError,SyntaxError,IndexError,EOFError,Image.DecompressionBombError):pass
        assert actual==expected,(name,'file policy differs')
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),pixelmode='1',lazy=lazy,keep=expected is None,jpeg=enc(expected)))

    def bits(value,n=8):return bytes(((value>>i)&1)*255 for i in range(n))
    for value in range(256):
        for spec in ('02x','02X'):
            add(f'packed byte {value}/{spec}',xbm(8,1)+b'0x'+format(value,spec).encode(),pixels=bits(value))
    for byte in range(256):
        nibble=byte-48 if 48<=byte<=57 else byte-65+10 if 65<=byte<=70 else byte-97+10 if 97<=byte<=102 else 0
        add(f'first nibble {byte}',xbm(8,1)+b'x'+bytes([byte])+b'f',pixels=bits(nibble*16+15))
        add(f'second nibble {byte}',xbm(8,1)+b'xF'+bytes([byte]),pixels=bits(240+nibble))
        data=b'#define a'+bytes([byte])+b'_width 8\n#define b_height 1\n_bits[] = {\nxff'
        add(f'byte in macro name {byte}',data,pixels=bits(255) if byte!=10 else None,invalid=byte==10)
    for w in (1,2,3,7,8,9,15,16,17,31,32,33,63,64,65,127,128,129):
        values=[(x*13+y*47)%256 for y in range(3) for x in range((w+7)//8)]
        pixels=b''.join(bits(values[y*((w+7)//8)+x//8])[x%8:x%8+1] for y in range(3) for x in range(w))
        payload=b','.join(f'0x{v:02x}'.encode() for v in values)
        add(f'row layout width={w}',xbm(w,3)+payload,w=w,h=3,pixels=pixels)
        add(f'last operand truncated width={w}',xbm(w,3)+payload[:-1],w=w,h=3)
    for source,value in [(b'x01',1),(b'Xff',None),(b'255',None),(b'x1',None),(b'x',None),(b'',None),(b'x1,',16),(b'xxf',15),(b'xfx',240),(b'0x1}',16),(b'garbage ! xab trailing x',171),(b'0xabcd',171),(b'xzz',0),(b'x\n\t',0)]:
        add(f'scanner {source!r}',xbm(8,1)+source,pixels=None if value is None else bits(value))
    for padding in (65534,65535,65536,65537,131071):
        add(f'read boundary {padding}',xbm(8,1)+b' '*padding+b'x81',pixels=bits(129))
        add(f'truncated read boundary {padding}',xbm(8,1)+b' '*padding+b'x8')
    for whitespace in (b' ',b'\t',b'\n',b'\r',b'\v',b'\f'):
        for count in (1,8,9,10,16):
            add(f'prefix whitespace {whitespace!r}/{count}',whitespace*count+xbm(8,1)+b'xff',pixels=bits(255) if whitespace==b'\n' and count==1 else None,invalid=not (whitespace==b'\n' and count==1))
    for byte in (0,28,133,160,255):add(f'non ASCII whitespace {byte}',bytes([byte])+xbm(8,1)+b'xff',invalid=True)
    for sep in (b' ',b'\t',b' \t '):
        for end in (b'\n',b'\r',b'\r\n',b'\n\r',b'\r\n\n\r'):
            data=b'#define'+sep+b'_width'+sep+b'8'+end+b'#define'+sep+b'other_height'+sep+b'1'+end+b'_bits[] = {\nxff'
            valid=sep!=b'\t' and end in (b'\n',b'\r\n')
            add(f'header whitespace {sep!r}/{end!r}',data,pixels=bits(255) if valid else None,invalid=not valid)
    for width in (b'0008',b'0'*200+b'8'):
        add(f'leading numeric zeros {len(width)}',b'#define a_width '+width+b'\n#define b_height 0001\n_bits[] = {\nxff',pixels=bits(255) if len(width)<60 else None,invalid=len(width)>=60)
    for width in (b'+8',b'-8',b'8_',b'8 ',b'8;',b'8.0',b'',b'0',b'9'*100):
        valid=width in (b'+8',b'8 ')
        add(f'width spelling {width[:10]!r}',b'#define a_width '+width+b'\n#define b_height 1\n_bits[] = {\nxff',pixels=bits(255) if valid else None,invalid=not valid)
    for header in (b'#define a_width 8\n #define b_height 1\n_bits[]',b'#define a_height 1\n#define b_width 8\n_bits[]',b'#define a_width 8\n#define b_height 1_bits[]',b'#define a_width 8\n#define b_height 1\nbits[]',b'#define a_width 8\n#define b_height 1\n_bits[ ]'):
        add(f'invalid grammar {header!r}',header+b'xff',invalid=True)
    for hotspot in (b'#define a_x_hot 1\n#define a_y_hot 2\n',b'#define a_b_x_hot 1\n#define a_b_y_hot 2\n',b'#define a_x_hot -1\n#define a_y_hot nonsense\n',b'#define a_x_hot '+b'9'*100+b'\n#define a_y_hot '+b'8'*100+b'\n'):
        valid=b'nonsense' not in hotspot and len(hotspot)<75
        add(f'hotspot {hotspot[:25]!r}/{len(hotspot)}',b'#define a_width 8\n#define b_height 1\n'+hotspot+b'_bits[] = {\nxff',pixels=bits(255) if valid else None,invalid=not valid)
    add('greedy width before carriage return',b'#define a_width 99\r#define b_width 8\n#define c_height 1\n_bits[] = {\nxff',pixels=bits(255))
    add('greedy height before carriage return',b'#define a_width 8\n#define b_height 99\r#define c_height 1\n_bits[] = {\nxff',pixels=bits(255))
    add('first declaration selects data',xbm(8,1)+b'xff\0\xff_bits[]x01',pixels=bits(255))
    for end in (511,512,513):
        head=b'#define a_width 8\n#define b_height 1\n'
        data=head+b' '*(end-len(head)-7)+b'_bits[] = {\nxff'
        add(f'header window marker end={end}',data,invalid=True)
    for n in range(len(xbm(200,100))-1):
        add(f'short lazy header {n}',xbm(200,100)[:n],w=200,h=100,invalid=True)
    add('lazy header without final newline',xbm(200,100)[:-1],w=200,h=100,lazy=True,offset=len(xbm(200,100))-1)
    add('lazy header without pixels',xbm(200,100),w=200,h=100,lazy=True)
    for prefix in (b'/* note */\n',b'/* note\nmore\n*/\n',b'/*'+b'a'*200+b'*/\n',b'/* x */\n'*100):
        add(f'block comments {len(prefix)}',prefix+xbm(8,1)+b'xff',pixels=bits(255))
    for extra in (b'#define unused 9\n',b'#define x_x_hot -1\n#define x_y_hot 2\n',b'#define im_width 9\n#define im_width 8\n'):
        head=xbm(8,1);split=head.index(b'static')
        add(f'additional defines {extra!r}',head[:split]+extra+head[split:]+b'xff',pixels=bits(255))
    for extra in (b'#define unused nope\n',b'// unsupported comment\n',b' /* indented comment */\n',b'\n\n'):
        head=xbm(8,1);split=head.index(b'static')
        add(f'invalid intervening line {extra!r}',head[:split]+extra+head[split:]+b'xff',invalid=True)
    for length in (74,75,76):
        prefix=b'#define im_width 8\n#define im_height 1\n'
        line=b's'*(length-len(b'_bits[] = {'))+b'_bits[] = {\n'
        add(f'declaration line boundary {length}',prefix+line+b'xff',pixels=bits(255) if length<=75 else None,invalid=length>75,offset=len(prefix)+min(75,len(line)))
    add('dimension order reversed',b'#define im_height 1\n#define im_width 8\n_bits[] = {\nxff',pixels=bits(255))
    add('unterminated comment',b'/* comment\n'+xbm(8,1)+b'xff',invalid=True)
    add('underscored width',b'#define im_width 0_8\n#define im_height 1\n_bits[] = {\nxff',pixels=bits(255))
    add('unused large integer',b'#define ignored '+b'9'*50+b'\n'+xbm(8,1)+b'xff',pixels=bits(255))
    add('legacy inline pixels rejected',b'#define im_width 8\n#define im_height 1\nstatic char im_bits[] = {0xff};',invalid=True)
    add('pixel warning',xbm(10000,10000),w=10000,h=10000)
    add('pixel hard limit',xbm(178956971,1),invalid=True)
    for size in (1024*1024,1024*1024+1):
        data=xbm(3,2)+b'x05x02';add(f'file size threshold {size}',data+b' '*(size-len(data)),w=3,h=2,pixels=bytes([255,0,255,0,255,0]))
    return cases


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'xbm-codec';build_codec(codec,log,tool_env)
    fault=work/'xbm-fault';build_fault_codec(fault,log,tool_env)
    path=work/'xbm.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestXBM'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_XBM_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'XBM oracle: {len(cases)} header/pixel/whole-file cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-xbm-',dir='/tmp') as directory:run(Path(directory),None,env)
