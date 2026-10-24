"""Authored CUR directory, lazy DIB and whole-file policy expectations."""
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


def cur(frames):
    offset=6+16*len(frames);directory=[];payload=[]
    for data,w,h in frames:
        directory.append(struct.pack('<4BHHII',w%256,h%256,0,0,0,0,len(data),offset))
        payload.append(data);offset+=len(data)
    return b'\0\0\2\0'+struct.pack('<H',len(frames))+b''.join(directory+payload)


def vectors():
    from PIL import Image,CurImagePlugin
    from TESTS.adapters.django.login_bmp import bitmap
    from TESTS.adapters.django.login_gif import helper
    cases=[];compress=helper()
    def add(name,data,*,w=1,h=1,mode='RGB',pixels=None,invalid=False,opening=None):
        header=None
        if invalid:
            assert opening in ('rejected','terminal'),(name,'missing explicit opening failure')
            try:
                CurImagePlugin.CurImageFile(io.BytesIO(data))
                raise AssertionError((name,'invalid CUR driver accepted'))
            except SyntaxError:
                assert opening=='rejected',(name,'recoverable failure expected')
            except OSError:
                assert opening=='terminal',(name,'terminal failure expected')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            try:
                im=Image.open(io.BytesIO(data))
                assert not invalid,(name,'invalid header accepted')
                assert im.format=='CUR' and im.mode==mode and im.size==(w,h),(name,im.format,im.mode,im.size)
                header=dict(width=w,height=h,mode=mode)
            except (OSError,ValueError,SyntaxError,IndexError,TypeError,struct.error,Image.DecompressionBombError):
                assert invalid,(name,'unexpected header failure')
            if header:
                try:
                    im.load();actual=im.convert('L').tobytes() if mode=='1' else im.tobytes()
                    assert pixels is not None and actual==pixels,(name,'pixels differ',actual[:48],pixels and pixels[:48])
                except (OSError,ValueError,SyntaxError,IndexError,TypeError,struct.error):
                    assert pixels is None,(name,'unexpected pixel failure')
        expected=None
        if header and pixels is not None:
            target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h)))
            if target!=(w,h) or len(data)>1024*1024:
                im=Image.frombytes('L' if mode=='1' else mode,(w,h),pixels)
                if mode=='1':im=im.convert('1',dither=Image.Dither.NONE)
                if mode=='RGBA':
                    white=Image.new('RGB',(w,h),'white');white.paste(im,mask=im.getchannel('A'));im=white
                if len(data)>1024*1024:im=im.resize(target,Image.Resampling.LANCZOS)
                try:
                    out=io.BytesIO();im.save(out,format='JPEG');expected=out.getvalue()
                except (OSError,ValueError):pass
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        uri='opaque,'+enc(data)
        with contextlib.redirect_stdout(io.StringIO()),warnings.catch_warnings():
            warnings.simplefilter('ignore');actual=compress(uri)
        assert actual==(uri if expected is None else 'data:image/jpeg;base64,'+enc(expected)),(name,'Django file policy')
        cases.append(dict(name=name,data=enc(data),header=header,opening=opening,pixels=enc(pixels),keep=expected is None,jpeg=enc(expected)))
    for bits in (1,4,8,16,24,32):
        for width in (1,3,8,17,33):
            stride=((width*bits+31)//32)*4
            colors=2**bits if bits<=8 else 0
            palette=b''.join(bytes([v,0,255-v,0]) for v in range(colors))
            mode='P' if bits<=8 else 'RGBA' if bits==32 else 'RGB'
            if bits<=8:row=bytes(stride);pixel=bytes(1)
            elif bits==16:row=(b'\0\x7c'*width).ljust(stride,b'\0');pixel=b'\xff\0\0'
            elif bits==24:row=(b'\x19\x32\x64'*width).ljust(stride,b'\0');pixel=bytes([100,50,25])
            else:row=bytes([25,50,100,127])*width;pixel=bytes([100,50,25,127])
            for mask in (b'',bytes([255])*stride*2):
                data=cur([(bitmap(width,4,bits,row*2+mask,dib=True,colors=colors,palette=palette),width,2)])
                add(f'bits {bits} width {width} mask {bool(mask)}',data,w=width,h=2,mode=mode,pixels=pixel*(width*2))
    for alpha in range(256):
        add(f'alpha at offset 22: {alpha}',cur([(bitmap(1,2,32,bytes([25,50,100,alpha]),dib=True),1,1)]),mode='RGBA',pixels=bytes([100,50,25,alpha]))
    for size in (12,40,52,56,64,108,124):
        for top in (False,True):
            if top and size==12:continue
            for odd in (False,True):
                height=5 if odd else 4
                raw=bytes([25,50,100,0,30,60,120,0])
                data=cur([(bitmap(1,-height if top else height,24,raw,size=size,dib=True),1,2)])
                pixels=bytes([100,50,25,120,60,30]) if top else bytes([120,60,30,100,50,25])
                add(f'header {size} top {top} odd {odd}',data,h=2,pixels=pixels)
    red=bitmap(1,2,32,bytes([0,0,255,127]),dib=True);blue=bitmap(1,2,32,bytes([255,0,0,63]),dib=True)
    for a,b,c,d in ((1,1,2,2),(2,2,3,2),(2,2,2,3),(2,3,3,2),(255,255,0,0),(0,0,1,1)):
        add(f'selection {a}/{b}/{c}/{d}',cur([(red,a,b),(blue,c,d)]),pixels=bytes([0,0,255] if c>a and d>b else [255,0,0]))
    for offset in (0,22,23,31,255):
        data=bytearray(cur([(red,1,1)]));struct.pack_into('<I',data,18,offset)
        if offset>22:data=data[:22]+bytes(offset-22)+data[22:]
        add(f'offset alpha {offset}',bytes(data),mode='RGBA' if offset==22 else 'RGB',pixels=bytes([255,0,0,127] if offset==22 else [255,0,0]))
    for size in (0,1,0xffffffff):
        data=bytearray(cur([(red,1,1)]));struct.pack_into('<I',data,14,size);data[10:14]=b'\xff'*4
        add(f'ignored size/hotspots {size}',bytes(data),mode='RGBA',pixels=bytes([255,0,0,127]))
    for bits,comp,masks,row,mode,pixels in ((16,3,[0xf800,0x7e0,0x1f],b'\0\xf8\0\0','RGB',b'\xff\0\0'),(32,3,[0xff0000,0xff00,0xff,0xff000000],bytes([25,50,100,127]),'RGBA',bytes([100,50,25,127]))):
        for size in (40,56,108,124):
            effective_mode='RGB' if bits==32 and size==40 else mode
            add(f'bitfield {bits}/{size}',cur([(bitmap(1,2,bits,row,dib=True,size=size,comp=comp,masks=masks),1,1)]),mode=effective_mode,pixels=pixels[:3] if effective_mode=='RGB' else pixels)
    for bits,comp,raw in ((8,1,b'\3\1\0\1'),(4,2,b'\3\x11\0\1')):
        add(f'RLE {bits}',cur([(bitmap(3,2,bits,raw,dib=True,comp=comp,colors=2,palette=b'\0\0\xff\0\0\xff\0\0'),3,1)]),w=3,mode='P',pixels=b'\1'*3)
    for bits in (1,8):
        palette=b''.join(bytes([v,v,v,0]) for v in ([0,255] if bits==1 else range(256)))
        raw=b'\x80\0\0\0' if bits==1 else b'\x49\0\0\0'
        add(f'grayscale {bits}',cur([(bitmap(1,2,bits,raw,dib=True,palette=palette),1,1)]),mode='LA')
    # Gray cursors decode both DIB halves: AND transparency over XOR intensity.
    # The display-order top half is the AND mask, irrespective of row direction.
    for bits in (1,8):
        palette=b''.join(bytes([v,v,v,0]) for v in ([0,255] if bits==1 else range(256)))
        for top in (False,True):
            for mask in ((0,255) if bits==1 else (0,1,63,127,128,254,255)):
                xor=255 if bits==1 else 73
                and_row=bytes([0xff if mask else 0]) if bits==1 else bytes([mask])*3
                xor_row=b'\xe0' if bits==1 else bytes([xor])*3
                rows=[and_row.ljust(4,b'\0'),xor_row.ljust(4,b'\0')]
                raw=b''.join(rows if top else rows[::-1])
                alpha=255-mask
                pixels=bytes([(xor*alpha+127)//255,alpha])*3
                data=cur([(bitmap(3,-2 if top else 2,bits,raw,dib=True,palette=palette),3,1)])
                add(f'masked gray {bits}/{top}/{mask}',data,w=3,mode='LA',pixels=pixels)
                # Final row padding is optional; its pixel bytes are required.
                add(f'masked gray short {bits}/{top}/{mask}',data[:-4],w=3,mode='LA')
        data=cur([(bitmap(3,3,bits,bytes(12),dib=True,palette=palette),3,1)])
        add(f'masked odd DIB height {bits}',data,w=3,mode='LA')
    valid=cur([(red,1,1)])
    for count in (2,3,4,5):
        data=bytearray(valid);struct.pack_into('<H',data,4,count)
        add(f'overlapping and partial directory {count}',bytes(data),mode='RGBA',pixels=bytes([255,0,0,127]) if count<5 else None,invalid=count==5,opening='rejected' if count==5 else None)
    for n in range(len(valid)):
        add(f'truncation {n}',valid[:n],invalid=n<62,mode='RGBA',opening=('terminal' if 26<=n<62 else 'rejected') if n<62 else None)
    add('empty directory',cur([]),invalid=True,opening='rejected')
    data=bytearray(valid);struct.pack_into('<I',data,18,0xffffffff);add('offset beyond EOF',bytes(data),invalid=True,opening='rejected')
    data=cur([(b'\x89PNG\r\n\x1a\n'+bytes(64),1,1)]);add('PNG is not accepted',data,invalid=True,opening='terminal')
    for n in (1024*1024,1024*1024+1):add(f'file threshold {n}',valid+bytes(n-len(valid)),mode='RGBA',pixels=bytes([255,0,0,127]))
    data=cur([(bitmap(200,200,24,dib=True),200,100)])
    add('lazy missing pixels',data,w=200,h=100)
    for size in (0,1,4,8,16,24,32,48,72):
        add(f'unsupported DIB header {size}',cur([(struct.pack('<I',size)+bytes(128),1,1)]),invalid=True,opening='terminal')
    for n in range(12):
        data=bitmap(1,2,32,dib=True,comp=3)+bytes(n)
        add(f'truncated bitfield masks {n}',cur([(data,1,1)]),invalid=True,opening='rejected')
    for bits in (0,2,3,15,31,64):
        add(f'unsupported depth {bits}',cur([(bitmap(1,2,bits,dib=True),1,1)]),invalid=True,opening='terminal')
    for comp in (4,5,6,0xffffffff):
        add(f'unsupported compression {comp}',cur([(bitmap(1,2,24,dib=True,comp=comp),1,1)]),invalid=True,opening='terminal')
    add('oversized palette',cur([(bitmap(1,2,8,dib=True,colors=65537),1,1)]),invalid=True,opening='terminal')
    for w,h in ((0,2),(1,0),(1,1)):
        add(f'zero final size {w}/{h}',cur([(bitmap(w,h,24,dib=True),1,1)]),invalid=True,opening='rejected')
    add('unsupported depth precedes zero dimensions',cur([(bitmap(0,0,0,dib=True),1,1)]),invalid=True,opening='terminal')
    for size in (12,40,52,56,64,108,124):
        for alpha in (0,127):
            data=bitmap(1,2,32,bytes([25,50,100,alpha]),dib=True,size=size)
            add(f'raw alpha header {size}/{alpha}',cur([(data,1,1)]),mode='RGBA',pixels=bytes([100,50,25,alpha]))
    add('BMP wrapper is not a DIB',cur([(bitmap(1,2,24,bytes(8)),1,1)]),invalid=True,opening='terminal')
    return cases

def run(work,log,tool_env):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors();codec=work/'cur-codec';fault=work/'cur-fault';path=work/'cur.json'
    build_codec(codec,log,tool_env);build_fault_codec(fault,log,tool_env)
    path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestCUR'],cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_CUR_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'CUR oracle: {len(cases)} declared header/pixel/file cases',flush=True);return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-cur-',dir='/tmp') as directory:run(Path(directory),None,env)
