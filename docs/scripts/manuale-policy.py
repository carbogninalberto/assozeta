"""Manual CI admission policy. Hash compatibility never confers producer trust."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = {'.github/workflows/manuale.yml', '.github/workflows/manuale-release.yml',
             '.github/workflows/publish-images.yml'}
FULL_INPUTS = ('BE/application/manuale/', 'BE/application/mcp_server/',
               'BE/application/agent/', 'BE/application/chat/',
               'BE/application/management/commands/evaluate_manuale.py',
               'BE/application/management/commands/seed_',
               'BE/application/management/commands/run_manuale_instance.py',
               'BE/Dockerfile', 'BE/entrypoint.sh', 'BE/requirements',
               'UI/src/routes/manuale/', 'docs/scripts/manuale',
               'selfhost/bin/', 'selfhost/compose', 'selfhost/updater/',
               'selfhost/tests/manual-release-smoke', 'TESTS/contracts/test_manual.py',
               'TESTS/browser/manual-release.mjs', 'TESTS/mcp/manual.py',
               'docs/manuale/requirements-offline.txt', '.github/actions/prepare-storage/',
               '.github/workflows/manuale', '.github/workflows/publish-images.yml')
SHA = re.compile(r'[a-f0-9]{40}\Z')


def pinned_source(path=ROOT / 'docs/manuale/source.json', override=None):
    source = json.loads(Path(path).read_text())
    if source.get('repository') != 'Bakney/manuale':
        raise ValueError('Manual authoring repository must be Bakney/manuale')
    revision = override or source.get('revision', '')
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise ValueError('Manual source must be an immutable full Git revision; update docs/manuale/source.json')
    return {'repository': source['repository'], 'revision': revision}


def verification_decision(state, *, force_full=False):
    registry = json.loads((Path(state['application']) / 'docs/manuale/recipes.json').read_text())
    all_ids = [item['id'] for item in registry['recipes']]
    candidates = state.get('cache', {}).get('decisions', [])
    cache = [item for item in candidates if item.get('selected')]
    changed = sorted({path for item in cache for path in item.get('changed_inputs', [])})
    infrastructure = [path for path in changed if path.startswith(FULL_INPUTS)
                      and not path.startswith('docs/scripts/tests/')]
    infrastructure.extend(decision['path'] for candidate in cache
                          for decision in candidate.get('input_decisions', [])
                          if decision['reason'] in ('shared-dependency', 'unknown-production-dependency',
                                                    'unknown-tooling', 'unknown-manual-input'))
    selected = list(state['selected_recipes'])
    reused = list(state.get('evidence_reuse', {}).get('reused_recipes', []))
    if force_full or infrastructure:
        mode = 'full'
        reasons = ['explicit or scheduled full verification'] if force_full else [
            'verification or appliance compatibility changed: ' + path for path in infrastructure]
    elif not any(item.get('result') == 'checked' for item in cache):
        mode = 'full'
        reasons = ['No complete compatible trusted baseline; verify fresh content and appliance compatibility.']
    elif selected:
        mode = 'affected'
        reasons = ['Fresh checks required for affected or incompatible workflows.']
    else:
        mode = 'reuse'
        reasons = ['Every workflow has compatible retained evidence; Git SHA/release alone does not invalidate it.']
    return {'format': 1, 'mode': mode, 'historical_rehearsal': mode == 'full',
            'fresh_workflows': all_ids if mode == 'full' else selected,
            'reused_workflows': [] if mode == 'full' else reused,
            'reasons': reasons, 'changed_inputs': changed, 'cache_decisions': candidates,
            'application_revision': state['application_input']['revision'],
            'manual_revision': state['manual_input']['revision'], 'release': state['release'],
            'runtime_checks': ['package integrity', 'exact revision and release', 'startup',
                               'reader and screenshots', 'authentication and authorization', 'MCP and assistant']}


def producer_rejection(run, artifact, repository, *, accepted_source):
    if run.get('status') != 'completed' or run.get('conclusion') != 'success':
        return 'producer workflow did not complete successfully'
    if run.get('repository', {}).get('full_name') != repository or run.get('head_repository', {}).get('full_name') != repository:
        return 'producer repository is untrusted'
    if run.get('path', '').split('@', 1)[0] not in WORKFLOWS:
        return 'producer workflow is not approved'
    if run.get('event') not in ('push', 'pull_request', 'release', 'workflow_dispatch', 'schedule'):
        return 'producer event is not approved'
    if not accepted_source:
        return 'producer source has not been accepted into the default branch'
    if artifact.get('expired') or artifact.get('workflow_run', {}).get('id') != run.get('id'):
        return 'artifact does not belong to the successful producer run'
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', artifact.get('digest', '')):
        return 'GitHub artifact digest is missing'
    return None


def gh_json(endpoint):
    return json.loads(subprocess.check_output(['gh', 'api', endpoint]))


def accepted_source(run, repository, default_ref, *, root=ROOT, api=gh_json):
    revision = run.get('head_sha', '')
    if not SHA.fullmatch(revision):
        return False
    def ancestor(commit):
        return subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', commit, default_ref],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if ancestor(revision):
        return True
    # Squash/rebase merges need the server's merged PR record, not a caller's
    # claim that a branch or artifact is trusted. Unmerged PRs never qualify.
    if run.get('event') == 'pull_request':
        for pr in api(f'repos/{repository}/commits/{revision}/pulls'):
            if (pr.get('merged_at') and pr.get('head', {}).get('sha') == revision
                    and pr.get('head', {}).get('repo', {}).get('full_name') == repository
                    and pr.get('base', {}).get('repo', {}).get('full_name') == repository
                    and ancestor(pr.get('merge_commit_sha', ''))):
                return True
    return False


def verify_archive_download(path, artifact, destination):
    with Path(path).open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if artifact.get('digest') != 'sha256:' + digest:
        raise ValueError('Downloaded baseline differs from the authenticated GitHub artifact digest')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != 'manuale-baseline.tar.gz' or entries[0].file_size > 4 * 1024**3:
            raise ValueError('Unexpected baseline artifact contents or size')
        if entries[0].is_dir() or (entries[0].external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError('Baseline artifact is not a regular file')
        with archive.open(entries[0]) as source, Path(destination).open('xb') as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
    return Path(destination)


def fetch_baseline(repository, output, manual_repository, cache_module, *, root=ROOT, api=gh_json):
    """Admit only successful reviewed CI producers; then validate archive bytes.

    The GitHub API, rather than any self-authored cache field, is the provenance
    authority. PR artifacts become eligible only after their source is merged.
    """
    report = {'format': 1, 'status': 'missing', 'candidates': []}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    info = api(f'repos/{repository}')
    default_ref = 'refs/remotes/origin/' + info['default_branch']
    subprocess.run(['git', '-C', str(root), 'fetch', '--no-tags', 'origin',
                    info['default_branch'] + ':' + default_ref], check=True, stdout=subprocess.DEVNULL)
    artifacts = api(f'repos/{repository}/actions/artifacts?name=manuale-verified-baseline&per_page=30')['artifacts']
    for artifact in artifacts:
        identifier = artifact.get('workflow_run', {}).get('id')
        decision = {'artifact_id': artifact['id'], 'producer_run': identifier}
        report['candidates'].append(decision)
        if not identifier:
            decision.update(result='rejected', reason='missing producer run')
            continue
        run = api(f'repos/{repository}/actions/runs/{identifier}')
        reason = producer_rejection(run, artifact, repository, accepted_source=accepted_source(
            run, repository, default_ref, root=root, api=api))
        if reason:
            decision.update(result='rejected', reason=reason)
            continue
        try:
            with tempfile.TemporaryDirectory(prefix='manual-baseline-', dir=output.parent) as work:
                work = Path(work)
                downloaded = work / 'artifact.zip'
                with downloaded.open('wb') as stream:
                    subprocess.run(['gh', 'api', f'repos/{repository}/actions/artifacts/{artifact["id"]}/zip'],
                                   check=True, stdout=stream)
                archive = work / 'manuale-baseline.tar.gz'
                verify_archive_download(downloaded, artifact, archive)
                candidate = work / 'candidate'
                cache_module.import_cache(archive, candidate, manual_repository, register=False)
                state = json.loads((candidate / 'run.json').read_text())
                revision = state['application_input']['revision']
                if run['event'] == 'pull_request':
                    tree = subprocess.check_output(['git', '-C', str(candidate / 'application'),
                        'rev-parse', revision + '^{tree}'], text=True).strip()
                    accepted_trees = set(subprocess.check_output(['git', '-C', str(root),
                        'log', default_ref, '--format=%T'], text=True).splitlines())
                    if tree not in accepted_trees:
                        raise ValueError('PR verification tree is not present in accepted default-branch history')
                elif revision != run['head_sha']:
                    raise ValueError('Cached source does not match the authenticated producer commit')
                # Relocation is performed by the importer; never rewrite sealed
                # reports or the application/manual revisions of the producer.
                state.update(run=str(output.resolve()), application=str(output.resolve() / 'application'),
                             manual=str(output.resolve() / 'manual'))
                if state.get('override'):
                    state['override'] = str(output.resolve() / 'compose.override.json')
                (candidate / 'run.json').write_text(json.dumps(state, indent=2) + '\n')
                candidate.replace(output)
            decision.update(result='accepted', reason='successful approved CI producer and intact verified baseline')
            report.update(status='available', run=str(output), producer_run=identifier,
                          artifact_id=artifact['id'], artifact_digest=artifact['digest'])
            return report
        except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError, zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
            decision.update(result='rejected', reason='baseline integrity or source admission failed ('
                            + type(exc).__name__ + '); fresh verification required')
    return report
