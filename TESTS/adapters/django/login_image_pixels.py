"""Pillow pixel oracle and actual Django login image decision characterization.

This also checks transformed JPEG bytes; file decoding and the full service remain open.
"""
import base64
import binascii
import io
import itertools
import json
import subprocess
from .accounts import ROOT
from .login_jpeg import build_codec


def run(work, log, tool_env):
    from PIL import Image
    from application.utils.api_utils import compress_base64
    from django.apps import apps
    before={m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    policies=[]
    # Expected dimensions and passthrough outcomes are independent of the
    # implementation; exact encoded payload lengths exercise the strict boundary.
    for name,w,h,size,ow,oh,keep,invalid in [
        ('exact max width',200,100,1048576,200,100,True,False),
        ('exact max height',100,200,None,100,200,True,False),
        ('small encoded image keeps dimensions',100,50,None,100,50,False,False),
        ('large dimensions at exact disk limit',400,200,1048576,400,200,False,False),
        ('one byte over limit',400,200,1048577,200,100,False,False),
        ('small dimensions but large disk data enlarge',100,50,1048577,200,100,False,False),
        ('thin image below limit',1,10000,None,1,10000,False,False),
        ('thin image zero output falls back',1,10000,1048577,1,10000,True,True),
        ('floating-point truncation preserved',289,100,1048577,199,69,False,False),
        ('square resize',513,513,1048577,200,200,False,False),
    ]:
        image=Image.new('RGB',(w,h),(20,40,60));encoded=io.BytesIO();image.save(encoded,format='PNG');data=encoded.getvalue()
        if size is not None:
            assert len(data)<=size
            data+=b'\0'*(size-len(data))
        uri='opaque-header,'+base64.b64encode(data).decode('ascii')
        actual=compress_base64(uri)
        assert (actual==uri)==keep, f'{name}: wrong Django passthrough decision'
        if not keep: assert actual.startswith('data:image/jpeg;base64,'), 'Django output header changed'
        with Image.open(io.BytesIO(base64.b64decode(actual.split(',',1)[1]))) as output:
            assert output.size==(ow,oh), f'{name}: wrong Django dimensions'
        policies.append({'name':name,'width':w,'height':h,'size':len(data),'out_width':ow,'out_height':oh,'keep':keep,'invalid':invalid})
    assert compress_base64('not-an-image')=='not-an-image', 'Django malformed-image fallback changed'
    vectors=[]
    for mode,c in [('L',1),('RGB',3),('RGBA',4),('CMYK',4),('YCbCr',3),('RGBX',4)]:
        for w,h,ow,oh in [(1,1,200,200),(2,3,3,2),(17,13,7,5),(201,99,200,98),(301,207,200,137),
                (31,29,31,200),(31,29,200,29),(9,7,9,7),(511,257,200,100)]:
            raw=bytes(((i*73)^(i//7*19)+11)&255 for i in range(w*h*c))
            image=Image.frombytes(mode,(w,h),raw)
            # Same pixel stages as compress_base64, before lossy JPEG encoding.
            if mode=='RGBA':
                prepared=Image.new('RGB',image.size,(255,255,255));prepared.paste(image,mask=image.split()[3])
            else: prepared=image
            output=prepared.resize((ow,oh),Image.Resampling.LANCZOS)
            if (w,h)==(ow,oh) and mode!='RGBA': assert output.tobytes()==raw, 'identity resize changed source bytes'
            if (w,h)==(1,1): assert output.tobytes()==prepared.tobytes()*(ow*oh), 'constant image not preserved'
            jpeg=io.BytesIO();output.save(jpeg,format='JPEG')
            vectors.append({'name':f'{mode} {w}x{h} to {ow}x{oh}','width':w,'height':h,'mode':mode,
                'pixels':base64.b64encode(raw).decode('ascii'),'out_width':ow,'out_height':oh,'out_mode':output.mode,
                'expected':base64.b64encode(output.tobytes()).decode('ascii'),'expected_jpeg':base64.b64encode(jpeg.getvalue()).decode('ascii')})
    alpha=Image.frombytes('RGBA',(4,1),bytes([1,2,3,0,10,20,30,128,20,30,40,255,0,0,0,1]))
    flat=Image.new('RGB',alpha.size,(255,255,255));flat.paste(alpha,mask=alpha.split()[3])
    assert flat.tobytes()==bytes([255,255,255,132,137,142,20,30,40,254,254,254]), 'explicit alpha rounding changed'
    data_uris=[]
    # Explicit known results anchor the permissive-padding behavior; exhaustive
    # short-input differential vectors then probe CPython's edge combinations.
    for payload,expected in [('TQ==',b'M'),('===TQ==ignored',b'M'),('T! Q=\n=after',b'M'),('TQ=A=',b'M\0'),('TWFu====',b'Man')]:
        assert base64.b64decode(payload)==expected, 'explicit Python base64 expectation changed'
    for length in range(7):
        for chars in itertools.product('AB=/!',repeat=length):
            payload=''.join(chars)
            try: result=base64.b64decode(payload)
            except (binascii.Error,ValueError): bad=True;result=b''
            else: bad=False
            data_uris.append({'input':'opaque,'+payload,'bad':bad,'expected':base64.b64encode(result).decode('ascii')})
    for payload in ('TQ==é','éTQ==','TQ==😀','TQ==\u00a0'):
        try: base64.b64decode(payload)
        except ValueError: pass
        else: raise AssertionError('non-ASCII data URI accepted by Python')
        data_uris.append({'input':'unicode-header😀,'+payload,'bad':True,'expected':''})
    print(f'Django/Pillow: {len(policies)} real image decisions, {len(vectors)} exact pixel vectors and {len(data_uris)} base64 differential vectors established',flush=True)
    path=work/'login-image-pixels.json';path.write_text(json.dumps({'policies':policies,'pixels':vectors,'data_uris':data_uris}));path.chmod(0o600)
    codec=work/'pixel-image-codec';build_codec(codec,log,tool_env)
    try:
        subprocess.run(['go','test','-race','-tags=imageintegration','./pkg/storage/loginimage','-run','^TestLoginImageAgainstPillow'],cwd=ROOT/'BE_GO',check=True,timeout=180,
            env={**tool_env,'ASSOZETA_LOGIN_IMAGE_PIXELS_FIXTURE':str(path),'ASSOZETA_LOGIN_JPEG_CODEC':str(codec)})
    finally:
        path.unlink(missing_ok=True);codec.unlink(missing_ok=True)
    after={m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    assert after==before,'pixel characterization changed database state'
    return len(vectors)
