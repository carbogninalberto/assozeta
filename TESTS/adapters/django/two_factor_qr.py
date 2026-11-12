"""Independent QR decoder comparison for deterministic synthetic provisioning data."""
import json
import subprocess
from .accounts import ROOT


def run(work,tool_env):
    import segno
    import zxingcpp
    from PIL import Image
    from TESTS.contracts.qr import decode_svg_qr
    path=work/'qr-vectors.json'
    try:
        subprocess.run(['go','run',str(ROOT/'TESTS/adapters/go/testdata/qr_vectors.go'),str(path)],cwd=ROOT/'BE_GO',env=tool_env,check=True,timeout=60)
        cases=json.loads(path.read_text());assert len(cases)==256
        for case in cases:
            reference=segno.make(case['uri'])
            for name,svg in [('django',reference.svg_data_uri()),('go',case['svg'])]:
                actual,image=decode_svg_qr(svg)
                assert actual==case['uri'],(name,'QR content differs')
                assert image.size==reference.symbol_size(),(name,'QR version/quiet-zone differs')
                codes=zxingcpp.read_barcodes(image.resize((image.width*6,image.height*6),Image.Resampling.NEAREST),formats=zxingcpp.BarcodeFormat.QRCode)
                assert len(codes)==1 and codes[0].text==case['uri'] and codes[0].ec_level==reference.error,(name,'QR detection/correction level differs')
        print('512 synthetic QR symbols decode in pure and detector modes with expected payload, dimensions and correction level',flush=True)
        return 512
    finally:path.unlink(missing_ok=True)
