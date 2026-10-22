"""Verify the shipped static JPEG helper against the pinned Pillow oracle.

Run after building BE_GO/Dockerfile. The test runner and synthetic fixture are
mounted read-only; the image runs without network access as its default user.
"""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.login_jpeg import vectors
from TESTS.adapters.django.login_jpeg_decode import vectors as decode_vectors
from TESTS.adapters.django.login_jpeg_header import vectors as header_vectors
from TESTS.adapters.django.login_jpeg_metadata import vectors as metadata_vectors
from TESTS.adapters.django.login_jpeg_file import vectors as file_vectors
from TESTS.adapters.django.login_png_pixels import vectors as png_vectors
from TESTS.adapters.django.login_png_header import vectors as png_header_vectors
from TESTS.adapters.django.login_bilevel import vectors as bilevel_vectors
from TESTS.adapters.django.login_png_stream import vectors as stream_vectors
from TESTS.adapters.django.login_png_file import vectors as png_file_vectors
from TESTS.adapters.django.login_bmp import vectors as bmp_vectors
from TESTS.adapters.django.login_ppm import vectors as ppm_vectors
from TESTS.adapters.django.login_pcx import vectors as pcx_vectors
from TESTS.adapters.django.login_tga import vectors as tga_vectors
from TESTS.adapters.django.login_qoi import vectors as qoi_vectors
from TESTS.adapters.django.login_xbm import vectors as xbm_vectors
from TESTS.adapters.django.login_sgi import vectors as sgi_vectors
from TESTS.adapters.django.login_sun import vectors as sun_vectors
from TESTS.adapters.django.login_gbr import vectors as gbr_vectors
from TESTS.adapters.django.login_msp import vectors as msp_vectors
from TESTS.adapters.django.login_gif import vectors as gif_vectors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    tool_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    architecture = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Architecture}}', args.image], text=True).strip()
    if architecture not in ('amd64', 'arm64'):
        raise RuntimeError(f'unsupported image test architecture: {architecture}')
    with tempfile.TemporaryDirectory(prefix='az-jpeg-image-', dir='/tmp') as directory:
        work = Path(directory); work.chmod(0o755)
        (work / 'jpeg.json').write_text(json.dumps(vectors()))
        (work / 'jpeg.json').chmod(0o644)
        (work / 'decode.json').write_text(json.dumps(decode_vectors()))
        (work / 'decode.json').chmod(0o644)
        (work / 'header.json').write_text(json.dumps(header_vectors()))
        (work / 'header.json').chmod(0o644)
        (work / 'metadata.json').write_text(json.dumps(metadata_vectors()))
        (work / 'metadata.json').chmod(0o644)
        (work / 'file.json').write_text(json.dumps(file_vectors()))
        (work / 'file.json').chmod(0o644)
        (work / 'png.json').write_text(json.dumps(png_vectors()))
        (work / 'png.json').chmod(0o644)
        (work / 'png-header.json').write_text(json.dumps(png_header_vectors()))
        (work / 'png-header.json').chmod(0o644)
        (work / 'bilevel.json').write_text(json.dumps(bilevel_vectors()))
        (work / 'bilevel.json').chmod(0o644)
        (work / 'png-stream.json').write_text(json.dumps(stream_vectors()))
        (work / 'png-stream.json').chmod(0o644)
        (work / 'png-file.json').write_text(json.dumps(png_file_vectors()))
        (work / 'png-file.json').chmod(0o644)
        (work / 'bmp.json').write_text(json.dumps(bmp_vectors()))
        (work / 'bmp.json').chmod(0o644)
        (work / 'ppm.json').write_text(json.dumps(ppm_vectors()))
        (work / 'ppm.json').chmod(0o644)
        (work / 'pcx.json').write_text(json.dumps(pcx_vectors()))
        (work / 'pcx.json').chmod(0o644)
        (work / 'tga.json').write_text(json.dumps(tga_vectors()))
        (work / 'tga.json').chmod(0o644)
        (work / 'qoi.json').write_text(json.dumps(qoi_vectors()))
        (work / 'qoi.json').chmod(0o644)
        (work / 'xbm.json').write_text(json.dumps(xbm_vectors()))
        (work / 'xbm.json').chmod(0o644)
        (work / 'sgi.json').write_text(json.dumps(sgi_vectors()))
        (work / 'sgi.json').chmod(0o644)
        (work / 'sun.json').write_text(json.dumps(sun_vectors()))
        (work / 'sun.json').chmod(0o644)
        (work / 'gbr.json').write_text(json.dumps(gbr_vectors()))
        (work / 'gbr.json').chmod(0o644)
        (work / 'gif.json').write_text(json.dumps(gif_vectors()))
        (work / 'gif.json').chmod(0o644)
        (work / 'msp.json').write_text(json.dumps(msp_vectors()))
        (work / 'msp.json').chmod(0o644)
        subprocess.run(['go', 'test', '-c', '-tags=imageintegration', '-o', str(work / 'jpeg.test'), './pkg/storage/loginimage'],
            cwd=ROOT / 'BE_GO', env={**tool_env, 'GOOS': 'linux', 'GOARCH': architecture, 'CGO_ENABLED': '0'}, check=True, timeout=180)
        (work / 'jpeg.test').chmod(0o755)
        identity = subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'id', args.image, '-u'], text=True).strip()
        assert identity == '10001', 'image no longer runs as expected unprivileged user'
        for name in ('Pillow-LICENSE', 'libjpeg-turbo-LICENSE.md', 'libjpeg-turbo-README.ijg', 'loginimage-NOTICE', 'libjpeg-turbo-version.txt', 'zlib-version.txt', 'zlib-LICENSE'):
            notice = subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'cat', args.image, '/app/licenses/' + name], text=True)
            assert notice.strip(), f'missing image notice: {name}'
            if name == 'zlib-version.txt':
                assert re.fullmatch(r'zlib-static-\d+\.\d+\.\d+-r\d+\n', notice), 'missing zlib package version'
                print(notice.strip(), flush=True)
            if name == 'zlib-LICENSE': assert 'Copyright' in notice and 'This notice may not be removed' in notice
            if name == 'Pillow-LICENSE': assert notice == (ROOT / 'BE_GO/pkg/storage/loginimage/LICENSE.pillow').read_text()
            if name == 'loginimage-NOTICE': assert notice == (ROOT / 'BE_GO/pkg/storage/loginimage/NOTICE').read_text()
            if name == 'libjpeg-turbo-version.txt':
                assert re.fullmatch(r'libjpeg-turbo-static-\d+\.\d+\.\d+-r\d+\n', notice), 'missing exact codec package version'
                print(notice.strip(), flush=True)
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--mount', f'type=bind,source={work},target=/fixtures,readonly',
            '--env', 'ASSOZETA_PNG_FILE_FIXTURE=/fixtures/png-file.json',
            '--env', 'ASSOZETA_BMP_FIXTURE=/fixtures/bmp.json',
            '--env', 'ASSOZETA_PPM_FIXTURE=/fixtures/ppm.json',
            '--env', 'ASSOZETA_PCX_FIXTURE=/fixtures/pcx.json',
            '--env', 'ASSOZETA_TGA_FIXTURE=/fixtures/tga.json',
            '--env', 'ASSOZETA_QOI_FIXTURE=/fixtures/qoi.json',
            '--env', 'ASSOZETA_XBM_FIXTURE=/fixtures/xbm.json',
            '--env', 'ASSOZETA_SGI_FIXTURE=/fixtures/sgi.json',
            '--env', 'ASSOZETA_SUN_FIXTURE=/fixtures/sun.json',
            '--env', 'ASSOZETA_GBR_FIXTURE=/fixtures/gbr.json',
            '--env', 'ASSOZETA_MSP_FIXTURE=/fixtures/msp.json',
            '--env', 'ASSOZETA_GIF_FIXTURE=/fixtures/gif.json',
            '--env', 'ASSOZETA_PNG_STREAM_FIXTURE=/fixtures/png-stream.json',
            '--env', 'ASSOZETA_BILEVEL_FIXTURE=/fixtures/bilevel.json',
            '--env', 'ASSOZETA_PNG_HEADER_FIXTURE=/fixtures/png-header.json',
            '--env', 'ASSOZETA_PNG_PIXELS_FIXTURE=/fixtures/png.json',
            '--env', 'ASSOZETA_JPEG_FILE_FIXTURE=/fixtures/file.json',
            '--env', 'ASSOZETA_JPEG_METADATA_FIXTURE=/fixtures/metadata.json',
            '--env', 'ASSOZETA_JPEG_HEADER_FIXTURE=/fixtures/header.json',
            '--env', 'ASSOZETA_LOGIN_JPEG_DECODE_FIXTURE=/fixtures/decode.json',
            '--env', 'ASSOZETA_LOGIN_JPEG_FIXTURE=/fixtures/jpeg.json', '--env', 'ASSOZETA_LOGIN_JPEG_CODEC=/app/image-codec',
            '--entrypoint', '/fixtures/jpeg.test', args.image, '-test.run', '^Test(JPEG(AgainstPillow|NativeProtocol|DecodeAgainstPillow|DecodeNativeProtocol|MarkerAgainstPillow|MetadataAgainstPillow|FileAgainstPillow)|PNGScanlinesAgainstPillow|PNGHeaderAgainstPillow|BilevelAgainstPillow|PNGStreamAgainstPillow|PNGStreamNativeProtocol|PNGFileAgainstPillow|BMPAgainstPillow|PPMAgainstPillow|PCXAgainstPillow|TGAAgainstPillow|QOIAgainstPillow|XBMAgainstPillow|SGIAgainstPillow|SUNAgainstPillow|GBRAgainstPillow|MSPAgainstPillow|GIFAgainstPillow)$', '-test.v'],
            check=True, timeout=180)


if __name__ == '__main__':
    main()
