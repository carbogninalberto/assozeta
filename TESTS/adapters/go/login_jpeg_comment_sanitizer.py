"""Check JPEG comment boundaries, output budgets and truncation under ASan/UBSan."""
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from PIL import Image
from TESTS.adapters.django.login_jpeg import build_codec
from TESTS.adapters.django.production_runtime import require_production_python


def main():
    require_production_python()
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR')}
    with tempfile.TemporaryDirectory(prefix='az-jpeg-comment-sanitize-',dir='/tmp') as directory:
        work=Path(directory);normal=work/'normal';sanitized=work/'sanitized'
        build_codec(normal,None,env)
        native=ROOT/'BE_GO/pkg/storage/loginimage/native'
        flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','libjpeg','libwebpdemux','libwebp'],text=True,env=env))
        subprocess.run(['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer',
            *[str(native/name) for name in ('jpeg_encode.c','jpeg_decode.c','png_stream.c','webp_decode.c')],*flags,'-lz','-o',str(sanitized)],check=True,env=env,timeout=60)
        checks=0
        def check(name,args,data,code,expected=b''):
            nonlocal checks
            for executable in (normal,sanitized):
                result=subprocess.run([str(executable),*args],input=data,capture_output=True,timeout=5,
                    env={'PATH':env['PATH'],'LC_ALL':'C','ASAN_OPTIONS':'abort_on_error=1','UBSAN_OPTIONS':'halt_on_error=1'})
                assert result.returncode==code,(name,executable.name,result.returncode,result.stderr.decode(errors='replace'))
                assert not result.stderr,(name,result.stderr.decode(errors='replace'))
                assert result.stdout==expected,(name,'JPEG bytes or failure output differs')
                checks+=1
        for mode,channels in [('L',1),('RGB',3),('RGBX',4),('YCbCr',3),('CMYK',4)]:
            pixels=bytes((i*31+17)%256 for i in range(6*channels))
            for size in (1,65510,65511,65512,65513,65514,65532,65533):
                comment=bytes((i*17)%256 for i in range(size))
                image=Image.frombytes(mode,(3,2),pixels);out=io.BytesIO();image.save(out,format='JPEG',comment=comment)
                expected=out.getvalue();args=['encode',mode,'3','2',str(len(expected)),str(size)]
                check(f'{mode}/{size}/exact',args,comment+pixels,0,expected)
                check(f'{mode}/{size}/output-limit',[*args[:4],str(len(expected)-1),str(size)],comment+pixels,4)
                check(f'{mode}/{size}/short-raster',args,comment+pixels[:-1],2)
                check(f'{mode}/{size}/short-comment',args,comment[:-1],2)
            check(f'{mode}/oversize-marker',['encode',mode,'3','2','100000','65534'],b'',2)
        assert checks==330,checks
        print(json.dumps({'success':True,'native_process_checks':checks,'valid_comments':40,'modes':5,'scope':'JPEG comment encoding, exact budgets and truncated input; not full codec parity'}),flush=True)


if __name__=='__main__':main()
