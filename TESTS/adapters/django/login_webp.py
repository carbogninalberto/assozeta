"""Authored WebP samples, first-frame policy and malformed-container contracts."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]


def vectors():
    from PIL import Image
    from TESTS.adapters.django.login_gif import helper
    compress=helper();cases=[]
    def add(name,data,*,w=3,h=2,mode='RGB',pixels=None,invalid=False,frames=1,loop=1,background=0xffffffff):
        expected_header=None
        try:
            im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid container accepted')
            assert im.format=='WEBP' and im.mode==mode and im.size==(w,h),(name,im.mode,im.size)
            bg=im.info['background'];color=(bg[3]<<24)|(bg[0]<<16)|(bg[1]<<8)|bg[2]
            assert im.n_frames==frames and im.info['loop']==loop and color==background,(name,im.n_frames,im.info)
            expected_header=dict(width=w,height=h,mode=mode,frames=frames,loop=loop,background=background)
        except (OSError,ValueError,EOFError):assert invalid,(name,'container rejected')
        if expected_header is not None:
            try:im.load();assert pixels is not None and im.tobytes()==pixels,(name,'declared decoded samples differ')
            except (OSError,ValueError,EOFError):assert pixels is None,(name,'pixels rejected')
        expected=None
        if not invalid:
            target=(int(w*min(200/w,200/h)),int(h*min(200/w,200/h)))
            if (target!=(w,h) or len(data)>1024*1024) and pixels is not None:
                image=Image.frombytes(mode,(w,h),pixels)
                if mode=='RGBA':
                    white=Image.new('RGB',(w,h),'white');white.paste(image,mask=image.getchannel('A'));image=white
                if len(data)>1024*1024:image=image.resize(target,Image.Resampling.LANCZOS)
                out=io.BytesIO();image.save(out,format='JPEG');expected=out.getvalue()
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        uri='opaque,'+enc(data)
        with contextlib.redirect_stdout(io.StringIO()):actual=compress(uri)
        assert actual==(uri if expected is None else 'data:image/jpeg;base64,'+enc(expected)),(name,'Django output differs')
        cases.append(dict(name=name,data=enc(data),header=expected_header,pixels=enc(pixels),keep=expected is None,jpeg=enc(expected)))
    def encoded(mode,w,h,pixels,**options):
        out=io.BytesIO();Image.frombytes(mode,(w,h),pixels).save(out,format='WEBP',**options);return out.getvalue()
    for mode,c in [('RGB',3),('RGBA',4)]:
        for w,h in [(1,1),(3,2),(7,9),(16,16),(31,17),(200,100),(201,101)]:
            for pattern in ('constant','gradient'):
                pixels=bytes([73])*(w*h*c) if pattern=='constant' else bytes((i*37+19)%256 for i in range(w*h*c))
                add(f'lossless {mode}/{w}/{h}/{pattern}',encoded(mode,w,h,pixels,lossless=True,exact=True),w=w,h=h,mode=mode,pixels=pixels)
    for alpha in (0,1,2,63,127,128,254,255):
        pixels=bytes([10,20,30,alpha])*6
        # An entirely opaque RGBA bitstream has no alpha flag and opens as RGB.
        mode='RGB' if alpha==255 else 'RGBA';decoded=bytes([10,20,30])*6 if alpha==255 else pixels
        add(f'alpha {alpha}',encoded('RGBA',3,2,pixels,lossless=True,exact=True),mode=mode,pixels=decoded)
    # Explicit quantized grayscale samples, characterized independently of the
    # Go decoder. At quality 75/100 these seven constants remain exact.
    values=(0,1,42,73,128,254,255)
    decoded={0:(0,5,44,73,130,250,255),50:(0,2,43,73,128,254,255),75:values,100:values}
    for quality,outputs in decoded.items():
        for source,target in zip(values,outputs):
            add(f'lossy grayscale {quality}/{source}',encoded('RGB',8,8,bytes([source])*192,quality=quality),w=8,h=8,pixels=bytes([target])*192)
    for alpha in (63,127,255):
        first=Image.new('RGBA',(7,5),(20,40,60,alpha));second=Image.new('RGBA',(7,5),(100,120,140,255))
        for background in [(0,0,0,0),(11,22,33,44)]:
            out=io.BytesIO();first.save(out,format='WEBP',save_all=True,append_images=[second],lossless=True,duration=[13,17],loop=3,background=background)
            color=(background[3]<<24)|(background[0]<<16)|(background[1]<<8)|background[2]
            mode='RGB' if alpha==255 else 'RGBA';pixels=bytes([20,40,60] if alpha==255 else [20,40,60,alpha])*35
            add(f'animation first frame {alpha}/{background}',out.getvalue(),w=7,h=5,mode=mode,pixels=pixels,frames=2,loop=3,background=color)
    for metadata in ({'icc_profile':b'opaque ICC'},{'exif':b'opaque EXIF'},{'xmp':b'opaque XMP'},{'icc_profile':b'I','exif':b'E','xmp':b'X'}):
        add(f'metadata {sorted(metadata)}',encoded('RGB',3,2,b'\x49'*18,lossless=True,**metadata),pixels=b'\x49'*18)
    sample=encoded('RGB',3,2,b'\x49'*18,lossless=True)
    for n in range(16):add(f'truncated prefix {n}',sample[:n],invalid=True)
    for size in (0,4,12,0xffffffff):
        data=bytearray(sample);struct.pack_into('<I',data,4,size);add(f'bad RIFF size {size}',bytes(data),invalid=True)
    for n in (1024*1024,1024*1024+1):add(f'whole-file size {n}',sample+bytes(n-len(sample)),pixels=b'\x49'*18)
    for w,h in ((3,2),(200,100)):
        data=encoded('RGB',w,h,b'\x49'*(w*h*3),lossless=True)
        for n in range(16,len(data)):add(f'truncated container {w}/{n}',data[:n],invalid=True)
        # These entropy bytes leave the container and dimensions valid, while
        # first-frame decoding fails. Unchanged-size login inputs stay lazy.
        for offset in [25,26,27,28,29,30,*range(32,len(data)-1)]:
            broken=bytearray(data);broken[offset]^=255
            add(f'deferred pixel corruption {w}/{offset}',bytes(broken),w=w,h=h)
    def chunk(kind,payload):return kind+struct.pack('<I',len(payload))+payload+bytes(len(payload)%2)
    for alpha in (127,255):
        for blend in (0,2):
            pixels=bytes([20,40,60,alpha])*4;frame=encoded('RGBA',2,2,pixels,lossless=True,exact=True)
            vp8x=chunk(b'VP8X',bytes([0x12,0,0,0])+(5).to_bytes(3,'little')+(3).to_bytes(3,'little'))
            anim=chunk(b'ANIM',struct.pack('<IH',0x2c0b1621,2))
            rect=b''.join(v.to_bytes(3,'little') for v in (1,1,1,1,13))+bytes([blend])
            body=b'WEBP'+vp8x+anim+chunk(b'ANMF',rect+frame[12:])
            data=b'RIFF'+struct.pack('<I',len(body))+body
            expected=b''.join(bytes([20,40,60,alpha]) if x in (2,3) and y in (2,3) else bytes(4) for y in range(4) for x in range(6))
            add(f'partial first canvas {alpha}/{blend}',data,w=6,h=4,mode='RGBA',pixels=expected,loop=2,background=0x2c0b1621)
    return cases


def run(work,log,tool_env):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors();codec=work/'webp-codec';fault=work/'webp-fault';path=work/'webp.json'
    build_codec(codec,log,tool_env);build_fault_codec(fault,log,tool_env)
    path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestWebP'],cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_WEBP_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'WebP oracle: {len(cases)} declared header/pixel/file cases',flush=True);return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-webp-',dir='/tmp') as directory:run(Path(directory),None,env)
