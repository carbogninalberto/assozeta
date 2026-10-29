"""Selective and bounded-parallel browser capture for the Italian manual.

Concurrency 1 (the default) keeps the historical sequential capture in
``manuale.py`` unchanged. With a higher concurrency the selected workflows are
partitioned deterministically into independent shards:

* shard 0 runs in the primary run directory against the primary services, exactly
  like the sequential path for its subset;
* every further shard owns a private Docker Compose project (its own Postgres
  database, Redis, MinIO object storage, renderer, API, UI and worker), private
  host ports, a private ``/manual-run`` directory and its own browser process.

Every workflow is still reseeded before it runs, so shards never share mutable
application state. Results are merged into the primary run only after every
report, screenshot, master and referenced artifact has been validated; duplicate,
missing, conflicting or inconsistent results are rejected. Extra shard stacks are
always removed, including on failure.
"""
import json
import os
import re
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CONCURRENCY_ENV = 'MANUALE_CAPTURE_CONCURRENCY'
WORKFLOWS_ENV = 'MANUALE_CAPTURE_WORKFLOWS'
SHARD_CPUS_ENV = 'MANUALE_CAPTURE_SHARD_CPUS'
SHARD_MEMORY_ENV = 'MANUALE_CAPTURE_SHARD_MEMORY'
MAX_CONCURRENCY = 4
# Every shard beyond the first starts a full private service stack. Budget two
# vCPUs and 4 GiB per concurrently capturing stack: a GitHub-hosted 4-vCPU,
# 16 GiB runner resolves ``auto`` to 2.
CPUS_PER_SHARD = 2
MEMORY_PER_SHARD = 4 * 1024 ** 3
# Per-workflow reseed and browser start-up cost, in checkpoint units.
WORKFLOW_OVERHEAD = 2
SHARDS_DIRECTORY = 'capture-shards'
MANIFEST = 'capture-shards.json'
WORKFLOW_ID = re.compile(r'[a-z0-9][a-z0-9-]*')
# Port-dependent settings written by configure_runtime; a shard replaces each.
ORIGIN_SETTINGS = ('APP_URL', 'SITE_ADDRESS', 'CSRF_TRUSTED_ORIGINS')
PORT_SETTINGS = ('DEV_UI_PORT', 'DEV_API_PORT', 'DEV_MINIO_CONSOLE_PORT')
# Report fields that must be identical across all shards of one capture batch.
SHARED_REPORT_FIELDS = ('capture_id', 'application_revision', 'tooling_hashes', 'fixture_version',
                        'reference_date', 'viewport', 'locale', 'timezone', 'theme',
                        'device_scale_factor', 'backend', 'capture_format')
REQUIRED_REPORT_FIELDS = ('id', 'status', 'source_hashes', 'fixture_profile', 'screenshots', 'checks',
                          *SHARED_REPORT_FIELDS)
SHA256 = re.compile(r'[0-9a-f]{64}')
# Run-directory roots that browser workflows write and later phases read.
RUN_ARTIFACT_ROOTS = ('captures', 'masters', 'downloads', 'receipt-pdfs', 'browser-print-pdfs', 'diagnostics')


class ShardError(RuntimeError):
    """A shard plan, execution or merge cannot be trusted."""


def parse_workflow_list(values):
    """Return sorted unique workflow ids from comma/whitespace separated values."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    ids = set()
    for value in values:
        for item in re.split(r'[\s,]+', value or ''):
            if not item:
                continue
            if not WORKFLOW_ID.fullmatch(item):
                raise ValueError('Invalid workflow id: ' + item)
            ids.add(item)
    return sorted(ids)


def selected_workflows(cli_values=(), environ=None):
    """Merge ``--workflow``/``--workflows`` values; the environment applies only when the CLI is silent."""
    environ = os.environ if environ is None else environ
    ids = parse_workflow_list(list(cli_values or ()))
    if ids:
        return ids
    return parse_workflow_list(environ.get(WORKFLOWS_ENV, ''))


def host_resources():
    cpus = os.cpu_count() or 1
    try:
        memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (AttributeError, OSError, ValueError):
        memory = MEMORY_PER_SHARD
    return cpus, memory


def resolve_concurrency(value=None, *, environ=None, resources=None):
    """Explicit value, else the environment, else 1 (the historical sequential path).

    ``auto`` derives a bounded value from host CPUs and memory.
    """
    environ = os.environ if environ is None else environ
    raw = value if value not in (None, '') else environ.get(CONCURRENCY_ENV, '')
    raw = str(raw).strip().lower()
    if raw == '':
        return 1
    if raw == 'auto':
        cpus, memory = resources or host_resources()
        return max(1, min(MAX_CONCURRENCY, cpus // CPUS_PER_SHARD, memory // MEMORY_PER_SHARD))
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_CONCURRENCY:
        raise ValueError(f'{CONCURRENCY_ENV} must be auto or an integer from 1 to {MAX_CONCURRENCY}')
    return int(raw)


def workflow_weight(recipe):
    return len(recipe.get('checkpoints') or ()) + WORKFLOW_OVERHEAD


def partition(recipes, selected, concurrency):
    """Deterministic longest-processing-time partition in registry order.

    The result depends only on the registry and the selected set, never on the
    order of ``selected``. Each shard lists its workflows in registry order.
    """
    if concurrency < 1:
        raise ValueError('Capture concurrency must be positive')
    known = [recipe for recipe in recipes]
    ids = [recipe['id'] for recipe in known]
    if len(set(ids)) != len(ids):
        raise ShardError('Duplicate workflow ids in the recipe registry')
    wanted = set(selected)
    unknown = wanted - set(ids)
    if unknown:
        raise ValueError('Unknown workflows: ' + ', '.join(sorted(unknown)))
    chosen = [(index, recipe) for index, recipe in enumerate(known) if recipe['id'] in wanted]
    count = min(concurrency, len(chosen))
    if count == 0:
        return []
    loads = [0] * count
    shards = [[] for _ in range(count)]
    for index, recipe in sorted(chosen, key=lambda item: (-workflow_weight(item[1]), item[0])):
        target = min(range(count), key=lambda shard: (loads[shard], shard))
        loads[target] += workflow_weight(recipe)
        shards[target].append(index)
    return [[known[index]['id'] for index in sorted(shard)] for shard in shards]


def shard_project(state, index):
    return f"{state['project']}-s{index}"


def read_env(text):
    lines = []
    for line in text.splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            lines.append((key, value))
    return lines


def shard_env(main_env_text, *, project, ports, shard_dir, env_file):
    """Copy the primary private configuration, replacing project, ports and origins."""
    origin = 'http://127.0.0.1:' + str(ports['DEV_UI_PORT'])
    updates = {'COMPOSE_PROJECT_NAME': project, 'APP_HOST': '127.0.0.1:' + str(ports['DEV_UI_PORT']),
               **{key: origin for key in ORIGIN_SETTINGS}, **{key: str(ports[key]) for key in PORT_SETTINGS},
               # Containers load this shard's configuration and lifecycle lock directory.
               'ASSOZETA_ENV_FILE': str(env_file), 'ASSOZETA_SELFHOST_DIR': str(shard_dir)}
    values = dict(read_env(main_env_text))
    values.update(updates)
    return '\n'.join(key + '=' + value for key, value in values.items()) + '\n', origin


def shard_override(main_override, *, run, shard_dir, cpus=None, memory=None):
    """Mount the shard directory as ``/manual-run``; images and other mounts stay shared."""
    override = json.loads(json.dumps(main_override))
    primary = f'{run}:/manual-run'
    replaced = 0
    for service in override.get('services', {}).values():
        volumes = service.get('volumes')
        if volumes:
            if primary in volumes:
                replaced += 1
            service['volumes'] = [f'{shard_dir}:/manual-run' if volume == primary else volume for volume in volumes]
            if any(volume.endswith(':/manual-run') and volume != f'{shard_dir}:/manual-run' for volume in service['volumes']):
                raise ShardError('Shard override would share the primary run directory')
    if not replaced:
        raise ShardError('Primary override does not mount the run directory')
    for name in ('api', 'worker', 'ui', 'renderer', 'postgres'):
        if cpus or memory:
            service = override['services'].setdefault(name, {})
            if cpus:
                service['cpus'] = float(cpus)
            if memory:
                service['mem_limit'] = str(memory)
    return override


def safe_relative(value):
    if not isinstance(value, str) or not value:
        raise ShardError('Missing artifact path')
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts or value != relative.as_posix() or value.startswith('./'):
        raise ShardError('Unowned artifact path: ' + value)
    return relative


def referenced_artifacts(report, *, outputs_only=True):
    """Every ``{path, sha256}`` run artifact recorded by a workflow report, as run-relative paths.

    Screenshot paths are recorded relative to ``captures/``. References outside
    :data:`RUN_ARTIFACT_ROOTS` (for example an application fixture uploaded by
    the browser) are inputs, not run outputs, and are not merged.
    """
    found = []

    def walk(value, screenshot=False):
        if isinstance(value, dict):
            if isinstance(value.get('path'), str) and 'sha256' in value:
                found.append(('captures/' + value['path'] if screenshot else value['path'], value['sha256']))
            for key, item in value.items():
                if key == 'screenshots' and isinstance(item, list):
                    for entry in item:
                        walk(entry, True)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(report)
    return [(relative, digest) for relative, digest in found
            if not outputs_only or relative.split('/', 1)[0] in RUN_ARTIFACT_ROOTS]


def validate_report(directory, recipe, expected):
    """Validate one passed workflow report and return ``{relative path: sha256}`` it owns."""
    identifier = recipe['id']
    file = Path(directory) / (identifier + '.json')
    if not file.is_file():
        raise ShardError('Missing capture report for ' + identifier)
    try:
        report = json.loads(file.read_text())
    except ValueError as error:
        raise ShardError('Unreadable capture report for ' + identifier) from error
    if not isinstance(report, dict) or report.get('id') != identifier:
        raise ShardError('Capture report identity mismatch for ' + identifier)
    missing = [key for key in REQUIRED_REPORT_FIELDS if key not in report]
    if missing:
        raise ShardError(f'Capture report schema mismatch for {identifier}: missing {", ".join(missing)}')
    if report['status'] != 'passed':
        raise ShardError('Capture report is not passed for ' + identifier)
    for key, value in expected.items():
        if report.get(key) != value:
            raise ShardError(f'Capture report for {identifier} disagrees on {key}')
    if (report['fixture_profile'] or 'baseline') != recipe.get('fixture', 'baseline'):
        raise ShardError('Wrong fixture profile in capture report for ' + identifier)
    checkpoints = sorted(point['id'] for point in recipe.get('checkpoints', ()))
    if sorted(shot.get('checkpoint') for shot in report['screenshots']) != checkpoints:
        raise ShardError('Incomplete screenshots for ' + identifier)
    prefix = recipe.get('capture_prefix') or ''
    owned = {identifier + '.json': None}
    for shot in report['screenshots']:
        if not prefix or not str(shot.get('path', '')).startswith(prefix):
            raise ShardError('Screenshot outside the workflow prefix for ' + identifier)
        for part in ('master', 'stability'):
            if not isinstance(shot.get(part), dict):
                raise ShardError(f'Screenshot {part} missing for {identifier}')
    for relative, _ in referenced_artifacts(report, outputs_only=False):
        if relative.split('/', 1)[0] not in RUN_ARTIFACT_ROOTS and (Path(directory) / safe_relative(relative)).exists():
            # A run output under an unregistered root would silently not be merged.
            raise ShardError(f'Unregistered run artifact root for {identifier}: {relative}')
    for relative, digest in referenced_artifacts(report):
        path = safe_relative(relative)
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ShardError(f'Invalid artifact hash for {identifier}: {relative}')
        target = Path(directory) / path
        if not target.is_file() or target.is_symlink():
            raise ShardError(f'Missing artifact for {identifier}: {relative}')
        if sha256(target) != digest:
            raise ShardError(f'Artifact hash mismatch for {identifier}: {relative}')
        if owned.get(relative, digest) != digest:
            raise ShardError(f'Conflicting artifact for {identifier}: {relative}')
        owned[relative] = digest
    return report, owned


def sha256(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def expected_report_fields(state):
    return {'capture_id': state['capture_id'], 'application_revision': state['application_input']['revision'],
            'tooling_hashes': state['tooling_hashes'], 'backend': 'real'}


def validate_merge(recipes, shards, state):
    """Validate every shard result before anything is merged.

    ``shards`` is a list of ``{'index', 'dir', 'workflows', 'scenarios'}``.
    Returns the ordered scenario list and the files to copy per shard.
    """
    by_id = {recipe['id']: recipe for recipe in recipes}
    order = {recipe['id']: index for index, recipe in enumerate(recipes)}
    assigned, executed = {}, {}
    for shard in shards:
        for identifier in shard['workflows']:
            if identifier in assigned:
                raise ShardError('Workflow assigned to several shards: ' + identifier)
            assigned[identifier] = shard['index']
        for scenario in shard['scenarios']:
            identifier = scenario.get('id')
            if identifier in executed:
                raise ShardError('Duplicate workflow result: ' + str(identifier))
            if assigned.get(identifier) != shard['index']:
                raise ShardError('Workflow result from an unassigned shard: ' + str(identifier))
            executed[identifier] = scenario.get('status')
    missing = sorted(set(assigned) - set(executed))
    if missing:
        raise ShardError('Incomplete shard results: ' + ', '.join(missing))
    failed = sorted(identifier for identifier, status in executed.items() if status != 'passed')
    if failed:
        raise ShardError('Capture batch failed for ' + ', '.join(failed))
    expected = expected_report_fields(state)
    shared, claims, copies = None, {}, []
    for shard in shards:
        files = {}
        for identifier in shard['workflows']:
            report, owned = validate_report(shard['dir'], by_id[identifier], expected)
            values = {key: report.get(key) for key in SHARED_REPORT_FIELDS}
            if shared is None:
                shared = values
            elif values != shared:
                differing = sorted(key for key in SHARED_REPORT_FIELDS if values[key] != shared[key])
                raise ShardError('Shard capture environments differ: ' + ', '.join(differing))
            for relative in owned:
                if relative in claims:
                    raise ShardError(f'Artifact claimed by {claims[relative]} and {identifier}: {relative}')
                claims[relative] = identifier
            files.update(owned)
        copies.append((shard, files))
    scenarios = sorted(({'id': identifier, 'status': status} for identifier, status in executed.items()),
                       key=lambda item: order[item['id']])
    return scenarios, copies


def copy_validated(run, copies):
    """Copy validated files from extra shard directories, rechecking every hash."""
    run = Path(run)
    for shard, files in copies:
        source_root = Path(shard['dir'])
        if source_root.resolve() == run.resolve():
            continue
        for relative, digest in sorted(files.items()):
            source, target = source_root / relative, run / relative
            if digest is not None and sha256(source) != digest:
                raise ShardError('Shard artifact changed during merge: ' + relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.merge-tmp')
            shutil.copy2(source, temporary)
            temporary.replace(target)


def copy_diagnostics(run, shard, recipes):
    """Retain failed reports and their diagnostics, as the sequential path does."""
    run, source_root = Path(run), Path(shard['dir'])
    if source_root.resolve() == run.resolve():
        return
    for identifier in shard['workflows']:
        report = source_root / (identifier + '.json')
        if report.is_file() and not report.is_symlink():
            shutil.copy2(report, run / report.name)
        diagnostics = source_root / 'diagnostics' / identifier
        if diagnostics.is_dir() and not diagnostics.is_symlink():
            for file in sorted(diagnostics.rglob('*')):
                if file.is_file() and not file.is_symlink():
                    target = run / file.relative_to(source_root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, target)


def owned_shard_dir(run, index):
    return Path(run) / SHARDS_DIRECTORY / str(index)


def check_owned(state, record):
    project = record.get('project', '')
    if not re.fullmatch(re.escape(state['project']) + r'-s[1-9][0-9]?', project):
        raise ShardError('Refusing teardown of an unowned shard project')
    directory = Path(record.get('dir', ''))
    if directory.resolve() != owned_shard_dir(state['run'], record['index']).resolve():
        raise ShardError('Refusing an unowned shard directory')


def shard_compose(runner, shard_state, *args):
    return runner.compose(shard_state, *args)


def teardown_command(shard_state):
    files = [Path(shard_state['env_file']), Path(shard_state['override'])]
    if all(file.is_file() for file in files):
        return ['docker', 'compose', '--env-file', shard_state['env_file'], '--project-name', shard_state['project'],
                '-f', str(Path(shard_state['application']) / 'selfhost/compose.dev.yml'), '-f', shard_state['override'],
                '--profile', 'tools', 'down', '--volumes', '--remove-orphans']
    return ['docker', 'compose', '--project-name', shard_state['project'], 'down', '--volumes', '--remove-orphans']


def teardown_shard(runner, state, record, *, log_run):
    """Remove one extra shard's containers, volumes and private files."""
    check_owned(state, record)
    shard_state = {**state, 'project': record['project'], 'env_file': record['env_file'],
                   'override': record['override'], 'run': record['dir']}
    runner.execute(teardown_command(shard_state), Path(log_run),
                   f"Shard {record['index']}: remove its services and data volumes")
    remaining = subprocess.check_output(['docker', 'ps', '-aq', '--filter',
                                         'label=com.docker.compose.project=' + record['project']], text=True).strip()
    if remaining:
        raise ShardError(f"Shard {record['index']} containers remain after cleanup")


def remove_shard_files(run, record):
    """Append shard logs to the primary log, then delete the shard directory and its credentials."""
    directory = Path(record['dir'])
    if not directory.exists():
        return
    log = directory / 'execution.log'
    if log.is_file():
        with (Path(run) / 'execution.log').open('ab') as stream:
            stream.write(f"\n--- capture shard {record['index']} ---\n".encode())
            stream.write(log.read_bytes())
    timings = directory / 'timings.json'
    if timings.is_file():
        primary = Path(run) / 'timings.json'
        report = json.loads(primary.read_text()) if primary.exists() else {'format': 1, 'events': []}
        for event in json.loads(timings.read_text()).get('events', []):
            report['events'].append({**event, 'shard': record['index']})
        temporary = primary.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
        temporary.replace(primary)
    shutil.rmtree(directory)


def teardown_recorded(runner, state):
    """Cleanup hook: remove any extra shard stack recorded as active (e.g. after a crash)."""
    errors = []
    for record in state.get('capture_shards', {}).get('shards', []):
        if record.get('index', 0) == 0 or not record.get('active'):
            continue
        try:
            teardown_shard(runner, state, record, log_run=state['run'])
            remove_shard_files(state['run'], record)
            record['active'] = False
        except Exception as error:  # keep cleaning the remaining shards
            errors.append(error)
    if errors:
        raise ShardError('Shard cleanup failed: ' + '; '.join(str(error) for error in errors))


def prepare_shard(runner, state, index, workflows, limits):
    directory = owned_shard_dir(state['run'], index)
    if directory.exists():
        raise ShardError('Stale capture shard directory: ' + str(directory))
    directory.mkdir(parents=True, mode=0o700)
    (directory / '.operations').mkdir(mode=0o700)
    ports = {key: runner.free_port() for key in PORT_SETTINGS}
    project = shard_project(state, index)
    env_file = directory / 'shard.env'
    text, origin = shard_env(Path(state['env_file']).read_text(), project=project, ports=ports,
                             shard_dir=directory, env_file=env_file)
    env_file.write_text(text)
    env_file.chmod(0o600)
    override = shard_override(json.loads(Path(state['override']).read_text()), run=state['run'],
                              shard_dir=directory, **limits)
    override_file = directory / 'compose.override.json'
    runner.write_json(override_file, override)
    shard_state = {**state, 'run': str(directory), 'project': project, 'env_file': str(env_file),
                   'override': str(override_file), 'origin': origin, 'ports': ports,
                   'selected_recipes': list(workflows), 'capture_shard': {'index': index, 'workflows': list(workflows)}}
    for key in ('capture_shards', 'cache', 'evidence_reuse'):
        shard_state.pop(key, None)
    runner.write_json(directory / 'run.json', shard_state)
    record = {'index': index, 'project': project, 'dir': str(directory), 'env_file': str(env_file),
              'override': str(override_file), 'workflows': list(workflows), 'active': True}
    return shard_state, record


def start_shard(runner, shard_state, label):
    """Start a private stack from the already built run images; no image build happens here."""
    run = Path(shard_state['run'])
    runner.execute(runner.compose(shard_state, 'config', '--quiet'), run, label + 'validate isolated service configuration')
    runner.execute(runner.compose(shard_state, 'up', '-d', '--wait', 'postgres', 'redis', 'minio', 'renderer'), run,
                   label + 'start private dependencies')
    runner.execute(runner.compose(shard_state, '--profile', 'tools', 'run', '--rm', 'minio-init'), run,
                   label + 'initialize private object storage')
    runner.execute(runner.compose(shard_state, '--profile', 'tools', 'run', '--rm', 'migrate'), run,
                   label + 'migrate private database')
    runner.execute(runner.compose(shard_state, 'up', '-d', '--wait', 'api', 'ui', 'worker'), run,
                   label + 'start application and worker')


def run_shard(runner, shard_state, recipes, *, primary, abort, label):
    """Run one shard's workflows sequentially, reseeding before every workflow."""
    run = Path(shard_state['run'])
    environment = {**os.environ, 'ASSOZETA_MANUAL_RUN': str(run), 'ASSOZETA_MANUAL_APPLICATION': shard_state['application']}
    scenarios, browser_error, fatal = [], None, None
    try:
        if not primary:
            start_shard(runner, shard_state, label)
        for recipe in recipes:
            if abort.is_set():
                break
            runner.execute(runner.management(shard_state, 'seed_manuale', '--reference-date', shard_state['reference_date'],
                                             '--origin', shard_state['origin'], '--output', '/manual-run/browser-input.json',
                                             *runner.recipe_seed_options(recipe)),
                           run, label + 'reset owned scenario state and browser session for ' + recipe['id'])
            try:
                runner.execute(['node', str(Path(shard_state['application']) / recipe['script'])], run,
                               label + 'verify real application scenario ' + recipe['id'], env=environment,
                               cwd=shard_state['application'])
            except (RuntimeError, subprocess.CalledProcessError) as error:
                browser_error = browser_error or error
                scenarios.append({'id': recipe['id'], 'status': 'failed'})
            else:
                scenarios.append({'id': recipe['id'], 'status': 'passed'})
    except BaseException as error:
        # Startup or reseed failures stop every shard, like the sequential batch.
        fatal = error
        abort.set()
    return {'scenarios': scenarios, 'browser_error': browser_error, 'fatal': fatal}


def capture_sharded(runner, state, plan):
    """Run ``plan`` (from :func:`partition`) concurrently and merge it into the primary run."""
    run = Path(state['run'])
    registry = runner.recipe_registry(Path(state['application']))['recipes']
    by_id = {recipe['id']: recipe for recipe in registry}
    limits = {'cpus': os.environ.get(SHARD_CPUS_ENV) or None, 'memory': os.environ.get(SHARD_MEMORY_ENV) or None}
    if limits['cpus'] is not None:
        try:
            if float(limits['cpus']) <= 0:
                raise ValueError
        except ValueError:
            raise ValueError(SHARD_CPUS_ENV + ' must be a positive number') from None
    if limits['memory'] is not None and not re.fullmatch(r'[1-9][0-9]*[kmg]?', limits['memory'].lower()):
        raise ValueError(SHARD_MEMORY_ENV + ' must be a Docker memory size such as 3g')
    # A previous interrupted attempt may have left recorded stacks; remove them first.
    teardown_recorded(runner, state)
    stale = run / SHARDS_DIRECTORY
    if stale.exists():
        shutil.rmtree(stale)
    (run / MANIFEST).unlink(missing_ok=True)
    records = [{'index': 0, 'project': state['project'], 'dir': str(run), 'workflows': list(plan[0]), 'active': True}]
    shard_states = [state]
    abort = threading.Event()
    results = [None] * len(plan)
    batch = {'format': 1, 'status': 'running', 'capture_id': state['capture_id'], 'scenarios': []}
    capture_error = None
    try:
        state['capture_shards'] = {'format': 1, 'concurrency': len(plan), 'shards': records}
        for index, workflows in enumerate(plan[1:], start=1):
            shard_state, record = prepare_shard(runner, state, index, workflows, limits)
            shard_states.append(shard_state)
            records.append(record)
            runner.write_json(run / 'run.json', state)
        runner.write_json(run / 'run.json', state)

        def work(index):
            recipes = [by_id[identifier] for identifier in plan[index]]
            results[index] = run_shard(runner, shard_states[index], recipes, primary=index == 0, abort=abort,
                                       label=f'Shard {index}: ')
        with ThreadPoolExecutor(max_workers=len(plan), thread_name_prefix='manuale-capture-shard') as pool:
            for future in [pool.submit(work, index) for index in range(len(plan))]:
                future.result()
        scenarios = [scenario for result in results for scenario in result['scenarios']]
        order = {recipe['id']: index for index, recipe in enumerate(registry)}
        batch['scenarios'] = sorted(scenarios, key=lambda item: order.get(item['id'], len(order)))
        fatal = next((result['fatal'] for result in results if result['fatal'] is not None), None)
        if fatal is not None:
            raise fatal
        failures = [entry['id'] for entry in batch['scenarios'] if entry['status'] == 'failed']
        if failures:
            first = next(result['browser_error'] for result in results if result['browser_error'] is not None)
            raise RuntimeError('Capture batch failed for ' + ', '.join(failures) + '; first failure: ' + str(first)) from first
        shards = [{'index': index, 'dir': shard_states[index]['run'], 'workflows': plan[index],
                   'scenarios': results[index]['scenarios']} for index in range(len(plan))]
        batch['scenarios'], copies = validate_merge(registry, shards, state)
        copy_validated(run, copies)
        runner.write_json(run / MANIFEST, {
            'format': 1, 'capture_id': state['capture_id'], 'concurrency': len(plan),
            'shards': [{'index': index, 'workflows': plan[index],
                        'files': {relative: digest for relative, digest in sorted(files.items()) if digest}}
                       for index, (_, files) in enumerate(copies)]})
    except BaseException as error:
        capture_error = error
        for index, record in enumerate(records[1:], start=1):
            try:
                copy_diagnostics(run, {'dir': record['dir'], 'workflows': record['workflows']}, registry)
            except OSError:
                pass
        raise
    finally:
        cleanup_errors = []
        for record in records[1:]:
            try:
                teardown_shard(runner, state, record, log_run=run)
                record['active'] = False
            except Exception as error:
                cleanup_errors.append(error)
            try:
                if not record['active']:
                    remove_shard_files(run, record)
            except OSError as error:
                cleanup_errors.append(error)
        if not any(record['active'] for record in records[1:]) and (run / SHARDS_DIRECTORY).is_dir():
            # Only directories without a started stack remain (e.g. a failed preparation);
            # they may hold copied private configuration.
            try:
                shutil.rmtree(run / SHARDS_DIRECTORY)
            except OSError as error:
                cleanup_errors.append(error)
        # The primary services restore their baseline exactly as the sequential batch does.
        try:
            runner.execute(runner.management(state, 'seed_manuale', '--reference-date', state['reference_date'],
                                             '--origin', state['origin'], '--output', '/manual-run/browser-input.json'),
                           run, 'Restore baseline after the capture batch')
            state['fixture_reset'] = 'passed'
        except Exception as error:
            state['fixture_reset'] = 'failed'
            cleanup_errors.append(error)
        records[0]['active'] = False
        batch['status'] = 'failed' if capture_error or cleanup_errors else 'passed'
        runner.write_json(run / 'capture-execution.json', batch)
        runner.write_json(run / 'run.json', state)
        if cleanup_errors and capture_error is None:
            raise ShardError('Capture shard cleanup failed: ' + '; '.join(str(error) for error in cleanup_errors))
