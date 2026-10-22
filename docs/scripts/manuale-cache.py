#!/usr/bin/env python3
"""Portable capture inputs for CI. This archive is not an installable manual."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_STATE = ('format', 'run_id', 'capture_id', 'status', 'release', 'reference_date',
                'application_input', 'manual_input', 'tooling_hashes', 'selected_recipes', 'evidence_reuse')


def owned(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or '.git' in relative.parts:
        raise ValueError('Unowned cache path: ' + str(relative))
    if any(part.startswith('.env') and not part.endswith('.example') for part in relative.parts):
        raise ValueError('Private configuration cannot enter a capture cache')
    target = Path(root).resolve()
    for part in relative.parts:
        target /= part
        if target.is_symlink():
            raise ValueError('Symlink in capture cache: ' + str(relative))
    return target


def export_cache(run, output):
    run = Path(run).resolve()
    state = json.loads((run / 'run.json').read_text())
    application, manual = Path(state['application']), Path(state['manual'])
    manifest = json.loads((run / 'manifest.json').read_text())
    registry = json.loads((application / 'docs/manuale/recipes.json').read_text())
    expected = {recipe['id'] for recipe in registry['recipes']}
    if not expected or expected != {item['id'] for item in manifest['scenarios'] if item.get('status') == 'passed'}:
        raise ValueError('Only a complete capture baseline can be exported')
    files = {'manifest.json', 'run.json'}
    for scenario in manifest['scenarios']:
        report_path = scenario['report_path']
        files.add(report_path)
        report = json.loads(owned(run, report_path).read_text())
        for frame in report['screenshots']:
            files.update(('captures/' + frame['path'], frame['master']['path']))
            if frame.get('stability'):
                files.add(frame['stability']['path'])
    if state.get('evidence_reuse'):
        files.add('evidence-reuse.json')
        plan = json.loads((run / 'evidence-reuse.json').read_text())
        files.update(item['origin_manifest']['path'] for item in plan['recipes'].values())
    sources = set(subprocess.check_output(['git', '-C', str(application), 'ls-files', '-z']).decode().split('\0')) - {''}
    sources.update(state['application_input'].get('uncommitted_files', {}))
    public = {key: state[key] for key in PUBLIC_STATE if key in state}
    public.update(application='application', manual='manual', run='.')
    payload = {'format': 1, 'purpose': 'capture-cache-not-release', 'state': public}
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        temporary = Path(temporary)
        bundle = temporary / 'application.bundle'
        subprocess.run(['git', '-C', str(application), 'bundle', 'create', str(bundle), 'HEAD'], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        staged = temporary / 'cache.tar.gz'
        with tarfile.open(staged, 'w:gz', compresslevel=1) as archive:
            def add_bytes(name, data, mode=0o644):
                owned(temporary, name)
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), mode
                archive.addfile(info, io.BytesIO(data))

            def add_file(name, file):
                if file.is_symlink() or not file.is_file():
                    raise ValueError('Cache input is not a regular file: ' + str(file))
                add_bytes(name, file.read_bytes(), file.stat().st_mode & 0o777)

            add_bytes('cache.json', json.dumps(payload, sort_keys=True).encode())
            add_file('application.bundle', bundle)
            for relative in sorted(files - {'run.json'}):
                add_file('proof/' + relative, owned(run, relative))
            for relative in sorted(sources):
                file = owned(application, relative)
                if file.is_file():
                    add_file('application/' + relative, file)
            manual_files = {page['path'] for page in manifest['pages']}
            manual_files.add('.manuale-evidence.json')
            for relative in sorted(manual_files):
                add_file('manual/' + relative, owned(manual, relative))
            frontend = run / 'frontend-build.json'
            if frontend.is_file():
                add_file('proof/frontend-build.json', frontend)
                for relative in json.loads(frontend.read_text())['artifacts']:
                    add_file('application/UI/dist/public/' + relative, owned(application / 'UI/dist/public', relative))
                ui = json.loads(Path(state['override']).read_text())['services']['ui']
                # The baseline configuration is compared, never executed. Keep
                # only the UI command and the public build variables we supply.
                environment = {key: value for key, value in ui['environment'].items()
                               if key in ('DEPLOY_ENV', 'OEM_ENV', 'ASSOZETA_MANUAL_BUILD', 'NODE_OPTIONS')}
                add_bytes('proof/compose.override.json', json.dumps({'services': {'ui': {
                    'command': ui['command'], 'environment': environment}}}).encode())
        staged.replace(output)
    return {'path': str(output), 'workflows': len(expected), 'purpose': payload['purpose']}


def import_cache(archive_path, output, manual_repository, *, register=True):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Cache import needs a new owned output directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        temporary = Path(temporary)
        unpacked = temporary / 'unpacked'
        unpacked.mkdir()
        with tarfile.open(archive_path, 'r:gz') as archive:
            names = set()
            for member in archive:
                if (not member.isfile() or member.name in names
                        or not (member.name in ('cache.json', 'application.bundle')
                                or member.name.startswith(('proof/', 'application/', 'manual/')))):
                    raise ValueError('Unexpected capture-cache member: ' + member.name)
                names.add(member.name)
                target = owned(unpacked, member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open('wb') as destination:
                    shutil.copyfileobj(source, destination)
                target.chmod(member.mode & 0o777)
        payload = json.loads((unpacked / 'cache.json').read_text())
        if payload.get('format') != 1 or payload.get('purpose') != 'capture-cache-not-release':
            raise ValueError('Not a supported capture cache')
        result = temporary / 'result'
        result.mkdir()
        subprocess.run(['git', 'clone', '--quiet', str(unpacked / 'application.bundle'), str(result / 'application')],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        state = payload['state']
        for relative, value in state['application_input'].get('uncommitted_files', {}).items():
            if value is None:
                owned(result / 'application', relative).unlink(missing_ok=True)
        shutil.copytree(unpacked / 'application', result / 'application', dirs_exist_ok=True)
        shutil.copytree(unpacked / 'manual', result / 'manual')
        shutil.copytree(unpacked / 'proof', result, dirs_exist_ok=True)
        state.update(run=str(output), application=str(output / 'application'), manual=str(output / 'manual'),
                     manual_repository=str(Path(manual_repository).resolve()), services_removed=True)
        if (result / 'compose.override.json').is_file():
            state['override'] = str(output / 'compose.override.json')
        (result / 'run.json').write_text(json.dumps(state, indent=2) + '\n')
        result.replace(output)
    # Discovery does not confer validity. The normal target-side verifier still
    # checks reports, snapshots, dependencies, review bindings and every PNG.
    if register:
        spec = importlib.util.spec_from_file_location('manual_cache_runner', ROOT / 'docs/scripts/manuale.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        runner.remember_run(state)
    return {'run': str(output), 'purpose': payload['purpose']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('export', 'import'))
    parser.add_argument('--run', type=Path)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manual-repo', type=Path)
    args = parser.parse_args()
    if args.command == 'export':
        if not args.run:
            parser.error('--run is required')
        result = export_cache(args.run, args.archive)
    else:
        if not args.output or not args.manual_repo:
            parser.error('--output and --manual-repo are required')
        result = import_cache(args.archive, args.output, args.manual_repo)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
