"""Mode-1 nearest-neighbour pixels and actual Django PNG size decisions."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]


def pattern(width,height,name):
    if name=='black':return bytes(width*height)
    if name=='white':return bytes([255])*(width*height)
    if name=='checker':return bytes(255*((x+y)%2) for y in range(height) for x in range(width))
    state=42;out=bytearray()
    for _ in range(width*height):
        state=(1664525*state+1013904223)&0xffffffff;out.append(255*(state>>31))
    return bytes(out)


def vectors():
    from PIL import Image
    cases=[]
    def add(width,height,out_width,out_height,name):
        pixels=pattern(width,height,name)
        image=Image.frombytes('L',(width,height),pixels).convert('1',dither=Image.Dither.NONE)
        assert image.convert('L').tobytes()==pixels
        resized=image.resize((out_width,out_height),Image.Resampling.LANCZOS)
        assert resized.mode=='1' and resized.size==(out_width,out_height)
        expected=resized.convert('L').tobytes()
        assert set(expected)<=set((0,255))
        if name in ('black','white'):assert expected==bytes([pixels[0]])*(out_width*out_height)
        if (width,height)==(out_width,out_height):assert expected==pixels
        if name=='checker' and (width,height,out_width,out_height) in ((2,1,7,1),(1,2,1,7)):
            assert expected==bytes([0,0,0,0,255,255,255]),'Pillow accumulated-coordinate boundary changed'
        output=io.BytesIO();resized.save(output,format='JPEG')
        encoded=output.getvalue()
        with Image.open(io.BytesIO(encoded)) as jpeg:assert jpeg.mode=='L' and jpeg.size==resized.size
        cases.append(dict(name=f'{width}x{height} -> {out_width}x{out_height} {name}',width=width,height=height,
            out_width=out_width,out_height=out_height,pixels=base64.b64encode(pixels).decode(),
            expected=base64.b64encode(expected).decode(),jpeg=base64.b64encode(encoded).decode()))
    for shape in [(1,1,200,200),(2,1,7,1),(1,2,1,7),(2,2,3,3),(17,13,200,152),(289,100,199,69),
            (257,129,17,13),(200,100,200,100),(31,33,9,8),(9,8,31,33),(17,13,17,9),(17,13,9,13)]:
        for name in ('black','white','checker','noise'):add(*shape,name)
    for source in (1,2,3,7,14,31,127,257,289):
        for target in (1,2,3,7,14,31,127,257,289):
            add(source,1,target,1,'checker');add(1,source,1,target,'checker')
    policy=[]
    for size,target,same in [((200,100),(200,100),True),((17,13),(200,152),False),((289,100),(199,69),False),
            ((1,400),(0,200),False),((400,1),(200,0),False),((31,17),(200,109),False)]:
        image=Image.frombytes('L',size,pattern(*size,'noise')).convert('1',dither=Image.Dither.NONE)
        png=io.BytesIO();image.save(png,format='PNG');data=png.getvalue()
        for length in (len(data),1024*1024,1024*1024+1):
            keep=(same and length<=1024*1024) or (length>1024*1024 and 0 in target)
            expected=None;out_size=target if length>1024*1024 else size
            if not keep:
                resized=image.resize(out_size,Image.Resampling.LANCZOS);assert resized.mode=='1'
                out=io.BytesIO();resized.save(out,format='JPEG');expected=out.getvalue()
            policy.append(dict(name=f'PNG {size} bytes {length}',data=base64.b64encode(data+bytes(length-len(data))).decode(),
                keep=keep,width=out_size[0],height=out_size[1],jpeg=base64.b64encode(expected).decode() if expected is not None else None))
    assert len(cases)==210 and len(policy)==18
    return dict(pixels=cases,policy=policy)


def run(work,log,tool_env,django_helper=None):
    from TESTS.adapters.django.login_jpeg import build_codec
    cases=vectors()
    if django_helper is not None:
        with contextlib.redirect_stdout(log):
            for case in cases['policy']:
                uri='opaque,'+case['data'];expected=uri if case['keep'] else 'data:image/jpeg;base64,'+case['jpeg']
                assert django_helper(uri)==expected,case['name']
    codec=work/'bilevel-codec';build_codec(codec,log,tool_env)
    path=work/'bilevel.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestBilevel'],cwd=ROOT/'BE_GO',
        env={**tool_env,'ASSOZETA_BILEVEL_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec)},check=True,timeout=180)
    finally:path.unlink(missing_ok=True);codec.unlink(missing_ok=True)
    print('Bilevel oracle: 210 nearest/JPEG vectors and 18 PNG policy cases',flush=True)
    return len(cases['pixels'])+len(cases['policy'])


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-bilevel-',dir='/tmp') as directory:run(Path(directory),None,env)
