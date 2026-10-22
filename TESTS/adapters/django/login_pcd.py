"""Authored PhotoYCC samples and Pillow 13 PCD rotation/file-policy cases."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from PIL import Image
ROOT=Path(__file__).resolve().parents[3]
W,H,OFFSET=768,512,96*2048


def rgb(y,cb,cr):
    light=int(1.3584*y+.5)
    values=(light+int(1.8215*(cr-137)+.5),light+int(-.9271435*(cr-137)+.5)+int(-.4302726*(cb-156)+.5),light+int(2.2179*(cb-156)+.5))
    return bytes(max(0,min(255,v)) for v in values)


def pcd(y=73,cb=156,cr=137,orientation=0):
    header=bytearray(OFFSET);header[2048:2052]=b'PCD_';header[3586]=orientation
    return bytes(header)+(bytes([y])*(W*2)+bytes([cb])*(W//2)+bytes([cr])*(W//2))*(H//2)


def vectors():
    from TESTS.adapters.django.login_gif import helper
    compress=helper();cases=[]
    def add(name,data,*,orientation=0,pixels=None,invalid=False):
        header=None
        try:
            im=Image.open(io.BytesIO(data))
            assert not invalid,(name,'invalid header accepted')
            size=(H,W) if orientation&3 in (1,3) else (W,H)
            assert im.format=='PCD' and im.mode=='RGB' and im.size==size
            expected_rotate=90 if orientation&3==1 else 270 if orientation&3==3 else None
            assert im.tile_post_rotate==expected_rotate
            header=dict(width=size[0],height=size[1],orientation=orientation&3)
        except (OSError,SyntaxError,ValueError,IndexError):
            assert invalid,(name,'unexpected open failure')
        if header:
            try:
                im.load();assert pixels is not None and im.tobytes()==pixels,(name,'authored samples differ')
            except (OSError,ValueError):
                assert pixels is None,(name,'unexpected pixel failure')
        expected=None
        if pixels is not None:
            image=Image.frombytes('RGB',size,pixels)
            if len(data)>1024*1024:image=image.resize((133,200) if orientation&3 in (1,3) else (200,133),Image.Resampling.LANCZOS)
            out=io.BytesIO();image.save(out,format='JPEG');expected=out.getvalue()
        enc=lambda b:base64.b64encode(b).decode() if b is not None else None
        uri='opaque,'+enc(data)
        with contextlib.redirect_stdout(io.StringIO()):actual=compress(uri)
        assert actual==(uri if expected is None else 'data:image/jpeg;base64,'+enc(expected)),(name,'Django file policy')
        cases.append(dict(name=name,data=enc(data),header=header,pixels=enc(pixels),keep=expected is None,jpeg=enc(expected)))
    anchors=[((0,156,137),(0,0,0)),((73,156,137),(99,99,99)),((188,156,137),(255,255,255)),((255,0,0),(97,255,1)),((255,255,255),(255,196,255)),((0,0,255),(215,0,0)),((0,255,0),(0,85,220))]
    for values,color in anchors:
        assert rgb(*values)==bytes(color),(values,rgb(*values),color)
        for orientation in (0,1,2,3):
            add(f'constant {values}/{orientation}',pcd(*values,orientation),orientation=orientation,pixels=bytes(color)*(W*H))
    for orientation in (4,5,6,7,128,129,130,255):
        add(f'orientation mask {orientation}',pcd(orientation=orientation),orientation=orientation,pixels=bytes([99])*(W*H*3))
    # Independent arithmetic, not pixels copied from a PCD decoder. Each chroma
    # sample applies to a two-by-two block; luma differs for every pixel/row.
    payload=bytearray();pixels=bytearray()
    for y in range(0,H,2):
        rows=[bytes((x+3*(y+row))%256 for x in range(W)) for row in (0,1)]
        cb=bytes((x+5*(y//2))%256 for x in range(W//2));cr=bytes((7*x+11*(y//2))%256 for x in range(W//2))
        payload.extend(rows[0]+rows[1]+cb+cr)
        for row in rows:
            for x in range(W):pixels.extend(rgb(row[x],cb[x//2],cr[x//2]))
    for orientation in (0,1,2,3):
        expected=bytes(pixels)
        if orientation in (1,3):
            # Independent index mapping for a quarter turn, with no image API.
            expected=b''.join(pixels[(y*W+x)*3:(y*W+x+1)*3]
                              for x in (range(W-1,-1,-1) if orientation==1 else range(W))
                              for y in (range(H) if orientation==1 else range(H-1,-1,-1)))
        data=pcd(orientation=orientation)[:OFFSET]+bytes(payload)
        add(f'chroma cell and luma sweep {orientation}',data,orientation=orientation,pixels=expected)
        add(f'large chroma cell sweep {orientation}',data+bytes(1024*1024+1-len(data)),orientation=orientation,pixels=expected)
    data=pcd()
    for n in (0,2047,2048,2051,2052,3585,3586,3587,4095,4096,196607,196608,len(data)-1):
        add(f'truncated {n}',data[:n],invalid=n<3587)
    for n in (1024*1024,1024*1024+1):add(f'file threshold {n}',data+bytes(n-len(data)),pixels=bytes([99])*(W*H*3))
    for signature in (b'pcd_',b'PCD\0',b'NOT!',bytes(4)):
        damaged=bytearray(data);damaged[2048:2052]=signature;add(f'bad signature {signature!r}',bytes(damaged),invalid=True)
    return cases

def run(work,log,tool_env):
    from TESTS.adapters.django.login_jpeg import build_codec,build_fault_codec
    cases=vectors();codec=work/'pcd-codec';fault=work/'pcd-fault';path=work/'pcd.json'
    build_codec(codec,log,tool_env);build_fault_codec(fault,log,tool_env)
    path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestPCD'],cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_PCD_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        for p in (path,codec,fault,Path(str(fault)+'.started')):p.unlink(missing_ok=True)
    print(f'PCD oracle: {len(cases)} declared header/pixel/file cases',flush=True);return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-pcd-',dir='/tmp') as directory:run(Path(directory),None,env)
