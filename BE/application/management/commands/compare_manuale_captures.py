"""Measure screenshot drift without altering either capture bundle."""
import json
from pathlib import Path

import numpy as np
from PIL import Image
from django.core.management import BaseCommand, CommandError

from application.manuale.index import owned_path, file_digest

INPUTS = ('application_revision', 'source_hashes', 'tooling_hashes', 'fixture_version',
          'reference_date', 'viewport', 'locale', 'timezone', 'theme', 'device_scale_factor', 'backend',
          'fixture_profile', 'capture_format')


class Command(BaseCommand):
    help = 'Compare retained and current real capture reports; identical inputs permit at most 0.1% changed pixels.'

    def add_arguments(self, parser):
        parser.add_argument('--run', required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        run = Path(options['run'])
        baseline = run / 'capture-baseline'
        results, failed = [], False
        for old_file in sorted(baseline.glob('*.json')):
            old = json.loads(old_file.read_text())
            current = json.loads((run / old_file.name).read_text())
            if old['status'] != 'passed' or current['status'] != 'passed':
                raise CommandError('Variance comparison requires passed real captures')
            comparable = all(old.get(key) == current.get(key) for key in INPUTS)
            def artifacts(report):
                result = {str(Path('captures') / capture['path']): capture for capture in report['screenshots']}
                result.update({capture['master']['path']: capture['master'] for capture in report['screenshots']
                               if capture.get('master')})
                return result
            before, after = artifacts(old), artifacts(current)
            for relative in sorted(set(before) | set(after)):
                entry = {'scenario': current['id'], 'path': relative, 'identical_inputs': comparable}
                if relative not in before or relative not in after:
                    entry['status'] = 'checkpoint-changed'
                    failed |= comparable
                    results.append(entry)
                    continue
                previous = owned_path(baseline, relative)
                latest = owned_path(run, relative)
                if file_digest(previous) != before[relative]['sha256'] or file_digest(latest) != after[relative]['sha256']:
                    raise CommandError('Capture contents differ from their recorded hashes')
                with Image.open(previous) as image:
                    first = np.array(image.convert('RGBA'))
                with Image.open(latest) as image:
                    second = np.array(image.convert('RGBA'))
                if first.shape != second.shape:
                    entry['status'] = 'dimensions-changed'
                    failed |= comparable
                else:
                    fraction = float(np.any(first != second, axis=2).mean())
                    delta = float(np.abs(first.astype(np.int16) - second.astype(np.int16)).mean() / 255)
                    entry.update({'status': 'stable' if fraction <= .001 else 'changed',
                        'changed_pixel_fraction': round(fraction, 8), 'mean_channel_difference': round(delta, 8),
                        'identical_bytes': before[relative]['sha256'] == after[relative]['sha256']})
                    failed |= comparable and fraction > .001
                results.append(entry)
        report = {'status': 'failed' if failed else 'passed', 'maximum_changed_pixel_fraction': .001, 'screenshots': results}
        Path(options['output']).write_text(json.dumps(report, indent=2) + '\n')
        if failed:
            raise CommandError('Screenshots drifted under identical inputs; previous verified manual/index retained.')
        self.stdout.write('Capture variance measured for ' + str(len(results)) + ' checkpoints.')
