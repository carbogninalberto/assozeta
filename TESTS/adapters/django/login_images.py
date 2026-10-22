"""Data-URI/file composition contracts; unimplemented formats are explicit gaps."""
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
    from TESTS.adapters.django.login_gif import gif,helper
    from TESTS.adapters.django.login_bmp import bitmap
    from TESTS.adapters.django.login_gbr import gbr
    from TESTS.adapters.django.login_msp import msp
    from TESTS.adapters.django.login_cur import cur
    from TESTS.adapters.django.login_pcd import pcd as pcd_file
    # Test the default preinit-before-init registry ordering used by Image.open.
    Image.preinit();Image.init()
    cases=[];compress=helper()
    def encoded(mode,size,pixels):
        im=Image.frombytes(mode,size,pixels)
        if mode=='RGBA':
            bg=Image.new('RGB',size,'white');bg.paste(im,mask=im.getchannel('A'));im=bg
        out=io.BytesIO();im.save(out,format='JPEG');return out.getvalue()
    def add(name,data=None,*,original=False,mode='L',size=(3,2),pixels=bytes([73])*6,uri=None,gap=False):
        if uri is None:uri='opaque,'+base64.b64encode(data).decode()
        expected=uri if original else 'data:image/jpeg;base64,'+base64.b64encode(encoded(mode,size,pixels)).decode()
        with contextlib.redirect_stdout(io.StringIO()):actual=compress(uri)
        assert actual==expected,(name,'Django result disagrees with declared pixels/policy')
        cases.append(dict(name=name,uri=uri,expected=expected,gap=gap))
    for fmt in ('JPEG','PNG','BMP','PPM','PCX','TGA','SGI','GIF','QOI'):
        for w,h in ((1,1),(3,2),(200,100)):
            mode='RGB' if fmt=='QOI' else 'L';pixels=bytes([73])*(w*h*(3 if mode=='RGB' else 1));im=Image.frombytes(mode,(w,h),pixels)
            out=io.BytesIO()
            if fmt=='QOI':data=b'qoif'+struct.pack('>II',w,h)+bytes([3,0])+(b'\xfe'+b'\x49'*3)*(w*h)+bytes(8)
            else:
                im.save(out,format=fmt,**({'optimize':False} if fmt=='GIF' else {}));data=out.getvalue()
            add(f'{fmt} {w}x{h}',data,size=(w,h),mode=mode,pixels=pixels,original=w==200)
    for fmt in ('PNG','TGA','SGI'):
        pixels=bytes([5,50,100,127])*6;im=Image.frombytes('RGBA',(3,2),pixels);out=io.BytesIO();im.save(out,format=fmt)
        add(f'{fmt} alpha',out.getvalue(),mode='RGBA',pixels=pixels)
    add('DIB',bitmap(3,2,24,b'\x49'*12*2,dib=True),mode='RGB',pixels=b'\x49'*18)
    im=Image.new('L',(3,2),73);out=io.BytesIO();im.save(out,format='PCX');pcx=out.getvalue()
    add('DCX first frame',struct.pack('<II',0x3ade68b1,4100)+bytes(4092)+pcx,pixels=bytes(6))
    add('GBR',gbr(3,2)+b'\x49'*6)
    add('SUN',struct.pack('>8I',0x59a66a95,3,2,8,8,1,0,0)+b'\x49'*8)
    add('MSP',msp(3,2,payload=b'\xe0\xe0'),mode='1',pixels=b'\xff'*6)
    xbm=b'#define sample_width 3\n#define sample_height 2\nstatic char sample_bits[] = {0x07,0x07};'
    add('XBM',xbm,mode='1',pixels=b'\xff'*6)
    # A SUN signature can be accepted by the earlier GBR plugin.
    add('GBR precedes SUN',struct.pack('>8I',0x59a66a95,1,1,8,1,1,0,0)+b'\x49',original=True)
    add('SUN after rejected GBR',struct.pack('>8I',0x59a66a95,1,1,8,0,1,0,0)+b'\x49',size=(1,1),pixels=b'\x49')
    add('terminal GBR size failure precedes valid QOI',b'qoif'+struct.pack('>II',1,3)+bytes([4,0])+b'\xff\x49\0\0\0\1\1'+bytes(8),original=True)
    add('GIF first-frame color palette',gif(3,2,b'\0\1\0\1\0\1',global_palette=b'\xff\0\0\0\xff\0'),original=True)
    add('PNG palette JPEG failure',_palette_png(),original=True)
    for raw in (b'',b'plain bytes',b'\xff\xd8\xff',b'\x89PNG\r\n\x1a\n',b'GIF89a',b'BM',b'P6',b'qoif',b'DanM',b'\x01\xda',b'#define missing_width',b'\x0a\x05'):
        add(f'malformed {raw!r}',raw,original=True)
    for uri in ('','https://example.test/avatar.png','opaque,','opaque,A','opaque,====','opaque,AA==\N{SNOWMAN}','\N{SNOWMAN},AA==','first,second,third','x,\N{SNOWMAN}'):
        add(f'URI {uri!r}',uri=uri,original=True)
    raw=b'P5\n3 2\n255\n'+b'\x49'*6
    for header in ('data:image/png;base64','data:image/jpeg;base64','\N{SNOWMAN}'):
        add(f'opaque header {header}',uri=header+','+base64.b64encode(raw).decode())
    text=base64.b64encode(raw).decode()
    add('first comma separates payload',uri='opaque,ignored,'+text,original=True)
    add('Base64 whitespace and punctuation',uri='x,'+'! \n'.join(text))
    for n in (1024*1024,1024*1024+1):
        data=raw+b'\0'*(n-len(raw));target=(3,2) if n==1024*1024 else (200,133)
        add(f'size policy {n}',data,size=target,pixels=b'\x49'*(target[0]*target[1]))
    # TIFF remains a gap; ICO and WebP use concrete file processors.
    for fmt in ('WEBP','TIFF','ICO'):
        im=Image.new('RGB',(16,16),(73,73,73));out=io.BytesIO();im.save(out,format=fmt,**({'lossless':True} if fmt=='WEBP' else {}))
        add('unimplemented TIFF' if fmt=='TIFF' else 'ICO PNG frame' if fmt=='ICO' else 'WebP frame',out.getvalue(),mode='RGB',size=(16,16),pixels=b'\x49'*768,gap=fmt=='TIFF')
    pixels=bytes([10,20,30,127])*6;im=Image.frombytes('RGBA',(3,2),pixels);out=io.BytesIO();im.save(out,format='WEBP',lossless=True,exact=True)
    add('WebP alpha',out.getvalue(),mode='RGBA',pixels=pixels)
    pixels=bytes([100,50,25,127])*6
    cursor=cur([(bitmap(3,4,32,bytes([25,50,100,127])*6,dib=True),3,2)])
    add('CUR alpha',cursor,mode='RGBA',pixels=pixels)
    add('CUR lazy passthrough',cur([(bitmap(200,200,24,dib=True),200,100)]),original=True)
    tga=bytearray(18);tga[2]=2;struct.pack_into('<HH',tga,12,1,1);tga[16]=24
    add('TGA after empty CUR directory',bytes(tga)+bytes([73])*3,mode='RGB',size=(1,1),pixels=bytes([73])*3)
    def collision(dib):
        data=bytearray(cur([(dib,1,1)]));struct.pack_into('<H',data,12,1);struct.pack_into('<I',data,14,0x00180001)
        return bytes(data)
    dib=bitmap(1,2,32,bytes([25,50,100,127]),dib=True)
    add('CUR precedes TGA',collision(dib),mode='RGBA',size=(1,1),pixels=bytes([100,50,25,127]))
    add('terminal CUR header precedes TGA',collision(struct.pack('<I',4)),original=True)
    add('TGA after truncated CUR header word',collision(b''),mode='RGB',size=(1,1),pixels=bytes([0,0,22]))
    add('TGA after zero CUR dimensions',collision(bitmap(0,0,24,dib=True)),mode='RGB',size=(1,1),pixels=bytes([0,0,22]))
    add('TGA after truncated CUR bitfields',collision(bitmap(1,2,32,dib=True,comp=3)),mode='RGB',size=(1,1),pixels=bytes([0,0,22]))
    qoi=b'qoif'+struct.pack('>II',1,1)+bytes([3,0])+b'\xfe\x49\x49\x49'+bytes(8)
    pcd=qoi+bytes(2048-len(qoi))+b'PCD_'+bytes(1535)
    add('PCD before QOI retains original',pcd,original=True)
    add('QOI after truncated PCD header',pcd[:-1],mode='RGB',size=(1,1),pixels=bytes([73])*3)
    add('PCD frame',pcd_file(),mode='RGB',size=(768,512),pixels=bytes([99])*(768*512*3))
    add('PCD reference rotation failure',pcd_file(orientation=1),original=True)
    return cases


def _palette_png():
    from PIL import Image
    im=Image.new('P',(3,2));im.putpalette([255,0,0]*256);out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()


def run(work,log,tool_env):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors();codec=work/'dispatch-codec';fault=work/'dispatch-fault';path=work/'dispatch.json'
    build_codec(codec,log,tool_env);build_fault_codec(fault,log,tool_env)
    path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestImageProcessor'],cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_IMAGE_DISPATCH_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    result=dict(component_checks_passed=True,parity_cases=sum(not c['gap'] for c in cases),open_gaps=[c['name'] for c in cases if c['gap']],complete_image_parity=False,gaps_exhaustive=False)
    print(json.dumps(result),flush=True);return result


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-images-',dir='/tmp') as directory:run(Path(directory),None,env)
