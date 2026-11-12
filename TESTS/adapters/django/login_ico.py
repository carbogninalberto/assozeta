"""Authored ICO samples/selection/alpha expectations checked against Django."""
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


def ico(frames):
    offset=6+16*len(frames);directory=[];payload=[]
    for data,w,h,bpp,colors in frames:
        directory.append(struct.pack('<4BHHII',w%256,h%256,colors,0,1,bpp,len(data),offset))
        payload.append(data);offset+=len(data)
    return b'\0\0\1\0'+struct.pack('<H',len(frames))+b''.join(directory+payload)


def vectors():
    from PIL import Image,PngImagePlugin
    from TESTS.adapters.django.login_bmp import bitmap
    from TESTS.adapters.django.login_gif import helper
    cases=[];compress=helper()
    def add(name,data,*,w=3,h=2,mode='RGBA',pixels=None,invalid=False):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            try:
                im=Image.open(io.BytesIO(data));im.load()
                assert not invalid,(name,'invalid image accepted')
                assert im.format=='ICO' and im.mode==mode and im.size==(w,h),(name,im.format,im.mode,im.size)
                actual=im.convert('L').tobytes() if mode=='1' else im.tobytes()
                assert actual==pixels,(name,'authored pixels differ',actual[:48],pixels and pixels[:48])
            except (OSError,ValueError,SyntaxError,IndexError,TypeError,struct.error,Image.DecompressionBombError):
                assert invalid,(name,'unexpected decode failure')
        expected=None
        if not invalid:
            target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h)))
            if target!=(w,h) or len(data)>1024*1024:
                image=Image.frombytes('L' if mode=='1' else mode,(w,h),pixels)
                if mode=='1':image=image.convert('1',dither=Image.Dither.NONE)
                if mode=='RGBA':
                    white=Image.new('RGB',(w,h),'white');white.paste(image,mask=image.getchannel('A'));image=white
                if len(data)>1024*1024:image=image.resize(target,Image.Resampling.LANCZOS)
                try:
                    out=io.BytesIO();image.save(out,format='JPEG');expected=out.getvalue()
                except (OSError,ValueError):pass
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        uri='opaque,'+enc(data)
        with contextlib.redirect_stdout(io.StringIO()),warnings.catch_warnings():
            warnings.simplefilter('ignore');actual=compress(uri)
        assert actual==(uri if expected is None else 'data:image/jpeg;base64,'+enc(expected)),(name,'Django file policy')
        cases.append(dict(name=name,data=enc(data),width=w,height=h,mode=mode,pixels=enc(pixels),invalid=invalid,keep=expected is None,jpeg=enc(expected)))
    def png(mode,w,h,pixels,comment=None):
        im=Image.frombytes('L' if mode=='1' else mode,(w,h),pixels)
        if mode=='1':im=im.convert('1',dither=Image.Dither.NONE)
        if mode=='P':im.putpalette([255,0,0]*256)
        out=io.BytesIO();info=PngImagePlugin.PngInfo()
        if comment:info.add_text('Comment',comment)
        im.save(out,format='PNG',pnginfo=info);return out.getvalue()
    for mode,c in [('1',1),('L',1),('LA',2),('RGB',3),('RGBA',4),('P',1)]:
        for w,h in [(1,1),(3,2),(200,100),(256,256)]:
            pixels=bytes([255 if mode=='1' else 73])*(w*h*c)
            data=ico([(png(mode,w,h,pixels,'discarded ICO metadata'),w,h,32,0)])
            add(f'PNG {mode} {w}x{h}',data,w=w,h=h,mode=mode,pixels=pixels)
    for bits in [1,4,8,16,24,32]:
        for w in [1,3,8,17,33]:
            h=2;stride=((w*bits+31)//32)*4
            palette=b'';colors=0
            if bits<=8:
                colors=2**bits;palette=b''.join(bytes([v,0,255-v,0]) for v in range(colors))
                row=bytes(stride);rgb=bytes([255,0,0])
            elif bits==16:row=(b'\0\x7c'*w).ljust(stride,b'\0');rgb=bytes([255,0,0])
            elif bits==24:row=(b'\x19\x32\x64'*w).ljust(stride,b'\0');rgb=bytes([100,50,25])
            else:row=b'\x19\x32\x64\x7f'*w;rgb=bytes([100,50,25])
            maskstride=((w+31)//32)*4
            for transparent in [False,True]:
                mask=bytes([255 if transparent else 0])*maskstride*h
                dib=bitmap(w,h*2,bits,row*h+mask,dib=True,colors=colors,palette=palette)
                alpha=127 if bits==32 else 0 if transparent else 255
                add(f'DIB {bits}/{w}/{transparent}',ico([(dib,w,h,bits,0)]),w=w,h=h,pixels=(rgb+bytes([alpha]))*(w*h))
    for alpha in range(256):
        dib=bitmap(1,2,32,bytes([25,50,100,alpha]),dib=True)
        add(f'32-bit alpha {alpha}',ico([(dib,1,1,32,0)]),w=1,h=1,pixels=bytes([100,50,25,alpha]))
    for mask in range(256):
        dib=bitmap(8,4,24,b'\x19\x32\x64'*16+bytes([mask,0,0,0,255-mask,0,0,0]),dib=True)
        expected=b''.join(bytes([100,50,25,255*(1-((v>>(7-x))&1))]) for v in (255-mask,mask) for x in range(8))
        add(f'AND bits and orientation {mask}',ico([(dib,8,2,24,0)]),w=8,h=2,pixels=expected)
    for topdown in (False,True):
        raw=bytes([1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16])
        dib=bitmap(2,-4 if topdown else 4,32,raw,dib=True)
        colors=[raw[i:i+3][::-1] for i in range(0,16,4)]
        if not topdown:colors=colors[2:]+colors[:2]
        alphas=[12,16,4,8]
        add(f'alpha always bottom up {topdown}',ico([(dib,2,2,32,0)]),w=2,h=2,pixels=b''.join(c+bytes([a]) for c,a in zip(colors,alphas)))
    for header_size in (12,40,52,56,64,108,124):
        dib=bitmap(1,3,24,b'\x19\x32\x64\0'+bytes(4),dib=True,size=header_size)
        add(f'header size and odd height {header_size}',ico([(dib,1,1,24,0)]),w=1,h=1,pixels=bytes([100,50,25,255]))
    for bits,comp,masks,row,rgb in [(16,3,[0xf800,0x7e0,0x1f],b'\0\xf8\0\0',b'\xff\0\0'),(32,3,[0xff0000,0xff00,0xff,0xff000000],b'\x19\x32\x64\x7f',bytes([100,50,25]))]:
        for header_size in (40,56,108,124):
            dib=bitmap(1,2,bits,row+bytes(4),dib=True,size=header_size,comp=comp,masks=masks)
            add(f'bitfield {bits}/{header_size}',ico([(dib,1,1,bits,0)]),w=1,h=1,pixels=rgb+bytes([127 if bits==32 else 255]))
    for comp,bits,raw in [(1,8,b'\x03\x01\0\1'),(2,4,b'\x03\x11\0\1')]:
        palette=b'\0\0\xff\0\0\xff\0\0'
        dib=bitmap(3,2,bits,raw+bytes(4),dib=True,comp=comp,colors=2,palette=palette)
        add(f'RLE {bits}',ico([(dib,3,1,bits,2)]),w=3,h=1,pixels=b'\0\xff\0\xff'*3)
    for value in (0,1,2,127,255):
        dib=bitmap(1,2,8,bytes([value,0,0,0])+bytes(4),dib=True,colors=2,palette=b'\0\0\xff\0\0\xff\0\0')
        rgb=b'\xff\0\0' if value==0 else b'\0\xff\0' if value==1 else bytes(3)
        add(f'partial palette index {value}',ico([(dib,1,1,8,2)]),w=1,h=1,pixels=rgb+b'\xff')
    for mask_length in range(8):
        dib=bitmap(3,4,24,b'\x19\x32\x64'*3+b'\0'*3+b'\x19\x32\x64'*3+b'\0'*3+bytes(mask_length),dib=True)
        data=bytearray(ico([(dib,3,2,24,0)]));struct.pack_into('<I',data,14,len(dib)+8-mask_length)
        add(f'truncated AND padding {mask_length}',bytes(data),pixels=(bytes([100,50,25,255])*6 if mask_length>=5 else None),invalid=mask_length<5)
    red=png('RGB',3,2,b'\xff\0\0'*6);blue=png('RGB',3,2,b'\0\0\xff'*6)
    for first,second in [(32,8),(8,32),(0,8),(8,0),(8,8)]:
        choose_first=(first or 256)<=(second or 256)
        add(f'lowest depth {first}/{second}',ico([(red,3,2,first,0),(blue,3,2,second,0)]),mode='RGB',pixels=(b'\xff\0\0' if choose_first else b'\0\0\xff')*6)
    for colors in [1,2,3,4,5,128,255]:
        choose_first=colors!=1
        add(f'color count depth {colors}',ico([(red,3,2,0,colors),(blue,3,2,8,0)]),mode='RGB',pixels=(b'\xff\0\0' if choose_first else b'\0\0\xff')*6)
    add('largest area first',ico([(blue,1,1,8,0),(red,3,2,32,0)]),mode='RGB',pixels=b'\xff\0\0'*6)
    add('actual frame dimensions replace directory',ico([(red,16,16,32,0)]),mode='RGB',pixels=b'\xff\0\0'*6)
    for size in [0,1,0xffffffff]:
        data=bytearray(ico([(red,3,2,32,0)]));struct.pack_into('<I',data,14,size)
        add(f'PNG ignores declared size {size}',bytes(data),mode='RGB',pixels=b'\xff\0\0'*6)
    valid=ico([(red,3,2,32,0)])
    for n in range(22):add(f'truncated directory {n}',valid[:n],invalid=True)
    for n in [22,23,29,30,38,45,len(valid)-30]:add(f'truncated PNG {n}',valid[:n],invalid=True)
    add('empty directory',ico([]),invalid=True)
    data=bytearray(valid);struct.pack_into('<I',data,18,0xffffffff);add('offset past EOF',bytes(data),invalid=True)
    for n in [1024*1024,1024*1024+1]:add(f'whole-file threshold {n}',valid+bytes(n-len(valid)),mode='RGB',pixels=b'\xff\0\0'*6)
    return cases


def run(work,log,tool_env):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors();codec=work/'ico-codec';fault=work/'ico-fault';path=work/'ico.json'
    build_codec(codec,log,tool_env);build_fault_codec(fault,log,tool_env)
    path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestICO'],cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_ICO_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'ICO oracle: {len(cases)} declared pixel/file cases',flush=True);return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-ico-',dir='/tmp') as directory:run(Path(directory),None,env)
