"""Exact WebP native frames/error exits under ASan and UBSan, no skipped cases."""
import base64
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from TESTS.adapters.django.login_jpeg import build_codec
from TESTS.adapters.django.login_webp import vectors


def main():
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR')}
    cases=vectors()
    with tempfile.TemporaryDirectory(prefix='az-webp-sanitize-',dir='/tmp') as directory:
        work=Path(directory);normal=work/'normal';sanitized=work/'sanitized'
        build_codec(normal,None,env)
        build_codec(sanitized,None,env,extra_flags=('-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'))
        count=0
        def check(name,data,args,code,expected=None):
            nonlocal count
            outputs=[]
            for executable in (normal,sanitized):
                r=subprocess.run([str(executable),*args],input=data,capture_output=True,timeout=5,env={'PATH':env['PATH'],'LC_ALL':'C','ASAN_OPTIONS':'abort_on_error=1','UBSAN_OPTIONS':'halt_on_error=1'})
                assert r.returncode==code,(name,executable.name,r.returncode,r.stderr.decode(errors='replace'))
                assert not r.stderr,(name,r.stderr.decode(errors='replace'))
                assert code==0 or not r.stdout,(name,'failure exposed output')
                if expected is not None:assert r.stdout==expected,(name,'authored frame differs')
                outputs.append(r.stdout)
            assert outputs[0]==outputs[1],(name,'sanitizer changed output')
            count+=1
        for c in cases:
            data=base64.b64decode(c['data']);h=c['header']
            header=struct.pack('>4sIIB3xIII4x',b'AZWP',h['width'],h['height'],3 if h['mode']=='RGB' else 4,h['frames'],h['loop'],h['background']) if h else None
            for op in ('webp-info','webp-decode'):
                expected=header;invalid=h is None or op=='webp-decode' and c['pixels'] is None
                if op=='webp-decode' and not invalid:expected+=base64.b64decode(c['pixels'])
                check(c['name']+'/'+op,data,[op,str(len(data)),'67108864'],2 if invalid else 0,expected if not invalid else None)
        c=next(c for c in cases if c['name']=='lossless RGB/1/1/constant');data=base64.b64decode(c['data'])
        for op,minimum in [('webp-info',len(data)+8),('webp-decode',len(data)+11)]:
            base=[op,str(len(data)),str(minimum)]
            check('exact budget '+op,data,base,0)
            check('short budget '+op,data,[op,str(len(data)),str(minimum-1)],4)
            for index,value in [(1,'0'),(1,'-1'),(1,'9'*100),(2,'0'),(2,'-1'),(2,'9'*100)]:
                args=base.copy();args[index]=value;check('invalid protocol '+op,data,args,2)
            check('short stdin '+op,data[:-1],base,2)
            check('long stdin '+op,data+b'\0',base,2)
            check('short arguments '+op,data,base[:2],2)
        # Resource-only case: a valid animation container with a huge canvas
        # must fail before AnimDecoder allocates its two canvas buffers. It is
        # deliberately not decoded through Pillow or counted as parity.
        animation=bytearray(base64.b64decode(next(c['data'] for c in cases if c['name'].startswith('animation first frame'))))
        assert animation[12:16]==b'VP8X'
        animation[24:27]=(9999).to_bytes(3,'little');animation[27:30]=(9999).to_bytes(3,'little')
        for op in ('webp-info','webp-decode'):check('canvas budget before allocation '+op,bytes(animation),[op,str(len(animation)),'1048576'],4)
        print(f'WebP ASan/UBSan: {count} exact frame/error/protocol checks, no diagnostics',flush=True)


if __name__=='__main__':main()
