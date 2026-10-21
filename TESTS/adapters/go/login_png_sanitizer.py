"""Check native PNG frames and exact error exits under ASan/UBSan.

The ordinary helper is separately compared to explicit Pillow samples by the Go
stream oracle. Invalid inputs must exit 2 without output; a sanitizer abort must
never be hidden behind the Go adapter's generic decoding-error classification.
"""
import base64
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import zlib

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from TESTS.adapters.django.login_jpeg import build_codec
from TESTS.adapters.django.login_png_stream import vectors


def main():
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR')}
    cases=vectors()
    with tempfile.TemporaryDirectory(prefix='az-png-sanitize-',dir='/tmp') as directory:
        work=Path(directory);normal=work/'normal';sanitized=work/'sanitized'
        build_codec(normal,None,env)
        native=ROOT/'BE_GO/pkg/storage/loginimage/native'
        flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','libjpeg'],text=True,env=env))
        subprocess.run(['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer',
            str(native/'jpeg_encode.c'),str(native/'jpeg_decode.c'),str(native/'png_stream.c'),*flags,'-lz','-o',str(sanitized)],check=True,env=env,timeout=60)
        def check(name,data,args,code):
            outputs=[]
            for executable in (normal,sanitized):
                result=subprocess.run([str(executable),*args],input=data,capture_output=True,timeout=5,
                    env={'PATH':env['PATH'],'LC_ALL':'C','ASAN_OPTIONS':'abort_on_error=1','UBSAN_OPTIONS':'halt_on_error=1'})
                assert result.returncode==code,(name,executable.name,result.returncode,result.stderr.decode(errors='replace'))
                assert not result.stderr,(name,result.stderr.decode(errors='replace'))
                assert code==0 or not result.stdout,(name,'invalid input exposed bytes')
                outputs.append(result.stdout)
            assert outputs[0]==outputs[1],(name,'sanitized frame differs')
        valid=invalid=0
        for case in cases:
            spec=case['spec'];data=base64.b64decode(case['data'])
            channels={0:1,2:3,3:1,4:2,6:4}[spec['color']]
            args=['png-inflate',str(len(data)),str(spec['width']),str(spec['height']),str(channels*spec['depth']),str(int(spec['interlace'])),
                str(spec['data_offset']),str(spec['data_length']),str(spec['sequence']),str(16*1024*1024)]
            check(case['name'],data,args,2 if case['invalid'] else 0)
            if case['invalid']:invalid+=1
            else:valid+=1
        data=zlib.compress(bytes((0,42)))
        base=['png-inflate',str(len(data)),'1','1','8','0','0',str(len(data)),'-1','2']
        protocol=[(1,'0',2),(1,'-1',2),(1,'9'*100,2),(2,'0',2),(3,'0',2),(4,'3',2),(5,'2',2),
            (6,str(len(data)+1),2),(7,'4294967296',2),(8,'-2',2),(8,'4294967296',2),
            (9,'0',2),(9,'1',4),(2,'9223372036854775807',4),(3,'9223372036854775807',4)]
        for index,value,code in protocol:
            args=base.copy();args[index]=value
            check(f'protocol argument {index}={value}',data,args,code)
        check('short arguments',data,base[:3],2)
        check('short stdin',data[:-1],base,2)
        check('extra stdin',data+bytes(1),base,2)
        print(f'PNG ASan/UBSan: {valid} exact frames, {invalid} exact invalid-input exits, {len(protocol)+3} malformed-protocol/budget cases; no diagnostics',flush=True)


if __name__=='__main__':main()
