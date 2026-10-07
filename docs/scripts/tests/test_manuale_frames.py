"""Image-format/crop checks use unit images, never simulated browser evidence."""
import importlib.util
import json
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('frame_test_index', ROOT / 'BE/application/manuale/index.py')
index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(index)


def png(width, height):
    def chunk(kind, contents):
        return struct.pack('>I', len(contents)) + kind + contents + struct.pack('>I', zlib.crc32(kind + contents))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\0' * width * 3) * height)) + chunk(b'IEND', b''))


class CaptureFrameTests(unittest.TestCase):
    def test_editorial_caption_does_not_change_capture_provenance(self):
        frame = {'path': 'images/step.png', 'sha256': 'a' * 64, 'checkpoint': 'saved',
                 'width': 300, 'height': 200,
                 'master': {'path': 'masters/images/step.png', 'sha256': 'b' * 64}}
        self.assertTrue(index.matches_capture({**frame, 'caption': 'Controlla il risultato'}, frame))
        for changes in ({'sha256': 'c' * 64}, {'checkpoint': 'different'}, {'width': 999},
                        {'master': {'path': '../outside.png'}}, {'extra_proof': True}, {'caption': {'html': 'unsafe'}}):
            with self.subTest(changes=changes):
                self.assertFalse(index.matches_capture({**frame, **changes}, frame))

    def test_crop_bounds_round_coordinates_and_reject_empty_or_nonfinite_regions(self):
        module = (ROOT / 'selfhost/tests/browser/manuale/frame.mjs').as_uri()
        script = "import {viewportCrop} from " + json.dumps(module) + ";\n" + r'''
console.log(JSON.stringify({
    crop: viewportCrop({x: 1900.4, y: 1060.2, width: 50, height: 80}),
    errors: [{x: 0,y: 0,width: 0,height: 10}, {x: 2000,y: 0,width: 10,height: 10},
        {x: Infinity,y: 0,width: 10,height: 10}].map(box => {try {viewportCrop(box); return false;} catch {return true;}})
}));
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout), {'crop': {'x': 1900, 'y': 1060, 'width': 20, 'height': 20},
                                                     'errors': [True, True, True]})

    def test_real_png_dimensions_and_crop_master_hashes_are_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            master_path, capture_path = 'masters/images/step.png', 'captures/images/step.png'
            for relative, contents in [(master_path, png(1920, 1080)), (capture_path, png(300, 200))]:
                target = root / relative
                target.parent.mkdir(parents=True)
                target.write_bytes(contents)
            capture = {'path': 'images/step.png', 'sha256': index.file_digest(root / capture_path), 'width': 300, 'height': 200,
                       'clip': {'x': 100, 'y': 100, 'width': 300, 'height': 200},
                       'master': {'path': master_path, 'sha256': index.file_digest(root / master_path), 'width': 1920, 'height': 1080}}
            index.verify_capture_frame(root, capture)
            with self.assertRaises(index.EvidenceError):
                index.verify_capture_frame(root, {**capture, 'clip': {**capture['clip'], 'x': 1900}})
            with self.assertRaises(index.EvidenceError):
                index.verify_capture_frame(root, {**capture, 'master': {**capture['master'], 'path': '../outside.png'}})
            (root / master_path).write_bytes(png(1280, 720))
            capture['master'].update(width=1280, height=720, sha256=index.file_digest(root / master_path))
            with self.assertRaisesRegex(index.EvidenceError, 'not Full HD'):
                index.verify_capture_frame(root, capture)

    def test_same_state_stability_proof_retains_and_checks_both_real_image_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contents = png(1920, 1080)
            for relative in ('masters/images/step.png', 'masters/repeats/images/step.png', 'captures/images/step.png'):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(contents)
            capture = {'path': 'images/step.png', 'width': 1920, 'height': 1080, 'sha256': index.digest(contents),
                       'master': {'path': 'masters/images/step.png', 'width': 1920, 'height': 1080, 'sha256': index.digest(contents)},
                       'stability': {'scope': 'same-state-consecutive-captures', 'path': 'masters/repeats/images/step.png',
                                     'sha256': index.digest(contents), 'changed_pixel_fraction': 0}}
            index.verify_capture_frame(root, capture)
            for change in ({'scope': 'fresh-run'}, {'path': 'masters/images/step.png'}, {'changed_pixel_fraction': .01}, {'sha256': '0' * 64}):
                with self.subTest(change=change), self.assertRaises(index.EvidenceError):
                    index.verify_capture_frame(root, {**capture, 'stability': {**capture['stability'], **change}})
            (root / capture['stability']['path']).write_bytes(contents + b'changed')
            with self.assertRaises(index.EvidenceError):
                index.verify_capture_frame(root, capture)

    def test_uncropped_image_must_match_master_and_corrupt_images_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contents = png(1920, 1080)
            for relative in ('masters/images/step.png', 'captures/images/step.png'):
                target = root / relative
                target.parent.mkdir(parents=True)
                target.write_bytes(contents)
            capture = {'path': 'images/step.png', 'width': 1920, 'height': 1080, 'sha256': index.digest(contents),
                       'master': {'path': 'masters/images/step.png', 'width': 1920, 'height': 1080, 'sha256': index.digest(contents)}}
            index.verify_capture_frame(root, capture)
            different = contents + b'unit-only changed image bytes'
            (root / 'captures/images/step.png').write_bytes(different)
            capture['sha256'] = index.digest(different)
            with self.assertRaisesRegex(index.EvidenceError, 'differs from its Full HD master'):
                index.verify_capture_frame(root, capture)
            with self.assertRaisesRegex(index.EvidenceError, 'not a PNG'):
                index.png_dimensions(b'broken capture')


if __name__ == '__main__':
    unittest.main()
