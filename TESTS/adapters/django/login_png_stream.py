"""Explicit PNG chunk/inflate boundaries plus independent sample expectations.

The delegating load_end observer records where pixel decoding finished, before
Pillow processes trailing metadata. Post-pixel failures are retained separately.
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
from unittest.mock import patch
import zlib

ROOT=Path(__file__).resolve().parents[3]


def vectors():
    from PIL import Image, PngImagePlugin
    from TESTS.adapters.django.login_png_pixels import vectors as pixels, chunk, file_bytes
    cases=[]
    original=PngImagePlugin.PngImageFile.load_end
    def add(name,data,spec,offset,length,sequence,expected,mode,invalid=False,position=None,remaining=0,out_sequence=None,post_invalid=False):
        observed=[]
        def end(image):
            observed.append((image.fp.tell(),image._PngImageFile__idat,image.png._seq_num))
            return original(image)
        try:
            with patch.object(PngImagePlugin.PngImageFile,'load_end',end),Image.open(io.BytesIO(data)) as image:
                image.load()
                actual=image.convert('L').tobytes() if mode=='1' else image.tobytes()
                assert actual==expected,name
        except (OSError,ValueError,SyntaxError) as error:
            assert invalid or post_invalid,(name,type(error).__name__,str(error))
        else:
            assert not invalid and not post_invalid,name
        if not invalid:
            assert len(observed)==1,name
            want_sequence=sequence if out_sequence is None else out_sequence
            got_position,got_remaining,got_sequence=observed[0]
            assert (got_position,got_remaining,got_sequence)==(position,remaining,None if want_sequence==-1 else want_sequence),(name,observed,position,remaining,want_sequence)
        cases.append(dict(name=name,data=base64.b64encode(data).decode(),spec={**spec,'data_offset':offset,'data_length':length,'sequence':sequence},
            expected=base64.b64encode(expected).decode(),mode=mode,invalid=invalid,post_invalid=post_invalid,
            position=position,remaining=remaining,sequence=sequence if out_sequence is None else out_sequence))
    for case in pixels():
        compressed=base64.b64decode(case['data']);data=file_bytes(case['spec'],compressed)
        offset=len(data)-len(compressed)-16
        consumed=min(len(compressed),65536) if case['name'].startswith('bad adler extra=65536') else len(compressed)
        add(case['name'],data,case['spec'],offset,len(compressed),-1,base64.b64decode(case['expected']),case['mode'],case['invalid'],offset+consumed,len(compressed)-consumed)
    spec=dict(width=3,height=2,depth=8,color=0,interlace=False)
    raw=b'\0\1\2\3\0\4\5\6';compressed=zlib.compress(raw);expected=b'\1\2\3\4\5\6'
    prefix=file_bytes(spec,b'')[:33];iend=chunk(b'IEND',b'')
    assert len(compressed)==16 and len(prefix)==33
    for corrupt in (False,True):
        packed=compressed if not corrupt else compressed[:-1]+bytes([compressed[-1]^1])
        for cut in range(17):
            data=prefix+chunk(b'IDAT',packed[:cut])+chunk(b'IDAT',packed[cut:])+iend
            invalid=corrupt and not 11<=cut<=15
            position=41+cut if cut>=11 else 69
            add(f'split {cut} corrupt={corrupt}',data,spec,41,cut,-1,expected,'L',invalid,position)
    for declared in (1000,70000):
        data=prefix+struct.pack('>I',declared)+b'IDAT'+compressed
        add(f'active overdeclared {declared}',data,spec,41,declared,-1,expected,'L',position=len(data),remaining=max(0,declared-65536))
    for surplus in (65520,65521,100000):
        payload=compressed+bytes(surplus)
        data=prefix+chunk(b'IDAT',payload)+iend
        add(f'active surplus {surplus}',data,spec,41,len(payload),-1,expected,'L',position=41+min(len(payload),65536),remaining=max(0,len(payload)-65536))
    for kind,invalid in ((b'DDAT',False),(b'IDAT',False),(b'tEXt',True),(b'bad!',True),(b'fdAT',True)):
        data=prefix+chunk(b'IDAT',compressed[:1])+chunk(kind,compressed[1:])+iend
        add(f'continuation {kind!r}',data,spec,41,1,-1,expected,'L',invalid,69)
    data=prefix+chunk(b'IDAT',compressed)+struct.pack('>I',1000)+b'IDATx'
    add('unread later IDAT truncated',data,spec,41,16,-1,expected,'L',position=57,post_invalid=True)
    data=prefix+chunk(b'IDAT',b'')+chunk(b'IDAT',b'')+chunk(b'IDAT',compressed)+iend
    add('empty data chunks',data,spec,41,0,-1,expected,'L',position=81)
    # fcTL establishes the sequence even without acTL; its full-canvas tile is
    # admitted by Pillow and the fdAT continuation must increment it.
    fctl=chunk(b'fcTL',struct.pack('>IIIIIHHBB',0,3,2,0,0,1,10,0,0))
    for seq in (0,1,2):
        data=prefix+fctl+chunk(b'IDAT',compressed[:1])+chunk(b'fdAT',struct.pack('>I',seq)+compressed[1:])+iend
        add(f'fdAT sequence {seq}',data,spec,79,1,0,expected,'L',seq!=1,111,out_sequence=1)
    assert len(cases)==852,len(cases)
    return cases


def run(work,log,tool_env):
    from TESTS.adapters.django.login_jpeg import build_codec, build_fault_codec
    cases=vectors();codec=work/'png-stream-codec';build_codec(codec,log,tool_env)
    fault=work/'png-stream-fault';build_fault_codec(fault,log,tool_env)
    path=work/'png-stream.json';path.write_text(json.dumps(cases));path.chmod(0o600)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestPNGStream'],cwd=ROOT/'BE_GO',
            env={**tool_env,'ASSOZETA_PNG_STREAM_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec),'ASSOZETA_LOGIN_JPEG_FAULT_CODEC':str(fault)},check=True,timeout=180)
    finally:
        path.unlink(missing_ok=True);codec.unlink(missing_ok=True);fault.unlink(missing_ok=True)
        Path(str(fault)+'.started').unlink(missing_ok=True)
    print(f'PNG stream oracle: {len(cases)} chunk/filter/sample cases',flush=True)
    return len(cases)


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-png-stream-',dir='/tmp') as directory:run(Path(directory),None,env)
