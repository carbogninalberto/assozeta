"""Phase 7 part B: one release gate on every publication entry point (CONTRACT P7 v1).

Boundary: the workflow files as GitHub would run them, simulated by TESTS/handoff/p7_workflow.py
(job graph, `if` conditions, step conditions, matrix entries) for every trigger of
.github/workflows/publish-images.yml that can publish a release: `release: published`,
`workflow_dispatch` with release_tag (each boolean input on), and a `push` of tags if one is ever
added. manuale-release.yml reaches a release only through publish-images (workflow_call); its own
dispatch is a rehearsal and must publish nothing releasable (pinned with every other workflow below).

Contract (publish-images.yml):
  - job `release-qualification` runs in every release context and runs
    `tools/release/qualify.py check` (part A) on the candidate it built; the installed lanes it
    depends on (test_p5_installed, test_p6_installed, test_p7_installed) run against the candidate's
    images (ASSOZETA_HANDOFF_P5_IMAGES), never images rebuilt from source;
  - every job that publishes release artifacts (registry pushes, tags, release assets) depends on it
    and does not run unless it succeeded (failure, cancelled or skipped all block);
  - release images are the qualified digests, promoted (`tools/release/qualify.py promote`), not
    rebuilt: no docker/build-push-action with push runs downstream of the qualification;
  - `tools/release/qualify.py verify-published` runs before the bundle is attached;
  - the qualification document is attached to the release as assozeta-release-qualification.json.

Run: python3 -B -m unittest -v TESTS.handoff.test_p7_publication   (seconds)
"""
import copy
import json
import re
import unittest

from TESTS.handoff import p7_release as r
from TESTS.handoff import p7_workflow as w

RED = r.RED
PUBLISH = 'publish-images.yml'
QUALIFY = 'release-qualification'
BAD_RESULTS = ('failure', 'cancelled', 'skipped')
PUBLISHING = re.compile(r'gh release (upload|create)|docker push|imagetools create|qualify\.py promote|'
                        r'gh api .*releases', re.I)
INSTALLED = ('TESTS.handoff.test_p5_installed', 'TESTS.handoff.test_p6_installed', 'TESTS.handoff.test_p7_installed')


def builds_and_pushes(step, scope_value):
    return str(step.get('uses', '')).startswith('docker/build-push-action') and w.truthy(scope_value)


def push_value(step, job, context, results, entry):
    value = (step.get('with') or {}).get('push', False)
    if isinstance(value, bool):
        return value
    scope = dict(context, needs={need: {'result': results[need], 'outputs': {}} for need in w.needs_of(job)},
                 matrix=entry, steps={})
    return w.evaluate(value, scope)


def publishing_steps(workflow, job):
    """Steps of a job that publish release artifacts (detected by what they do, not by their names)."""
    found = []
    for step in job.get('steps') or []:
        text = w.step_text(step)
        pushes = (str(step.get('uses', '')).startswith('docker/build-push-action')
                  and (step.get('with') or {}).get('push', False) not in (False, 'false'))
        if PUBLISHING.search(text) or pushes:
            found.append(step)
    return found


class Publication(r.ContractCase, unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.workflow = w.load(PUBLISH)
        self.jobs = self.workflow['jobs']
        self.contexts = w.release_contexts(self.workflow)
        self.assertIn('release', self.contexts, 'fixture: publish-images publishes on release')
        self.assertTrue(any(label.startswith('workflow_dispatch') for label in self.contexts),
                        'fixture: publish-images publishes on a manual dispatch with a release tag')

    def require_qualification_job(self):
        if QUALIFY not in self.jobs:
            self.fail(f'{RED}: {PUBLISH} has no {QUALIFY} job; every release entry point must qualify the candidate '
                      'with tools/release/qualify.py check before publication')
        return self.jobs[QUALIFY]

    def publishers(self):
        """Jobs that publish, except the candidate build (the qualification job and the jobs it depends on)."""
        candidate = {QUALIFY} | w.upstream(self.jobs, QUALIFY)
        for name in candidate:
            others = [step for step in publishing_steps(self.workflow, self.jobs[name])
                      if not str(step.get('uses', '')).startswith('docker/build-push-action')]
            self.assertEqual(others, [], f'{name} builds the candidate; it may push candidate images only')
        return {name: job for name, job in self.jobs.items()
                if name not in candidate and publishing_steps(self.workflow, job)}

    def enabled_steps(self, name, context, results):
        job = self.jobs[name]
        if job.get('uses'):
            return []
        steps = []
        for entry in w.matrix_entries(job):
            for step in job.get('steps') or []:
                if w.step_enabled(step, job, context, results, entry):
                    steps.append((entry, step))
        return steps

    def test_every_release_entry_point_runs_the_qualification(self):
        self.require_qualification_job()
        for label, raw in self.contexts.items():
            with self.subTest(entry_point=label):
                context = w.with_env(self.workflow, raw)
                results = w.simulate(self.workflow, raw)
                self.assertEqual(results[QUALIFY], 'success', f'{label}: {QUALIFY} does not run')
                scope = {QUALIFY} | w.upstream(self.jobs, QUALIFY)
                texts = [w.step_text(step) for name in scope if results[name] == 'success'
                         for _, step in self.enabled_steps(name, context, results)]
                self.assertTrue(any('tools/release/qualify.py check' in text for text in texts),
                                f'{label}: no step runs tools/release/qualify.py check')
                for module in INSTALLED:
                    steps = [(name, step) for name in scope if results[name] == 'success'
                             for _, step in self.enabled_steps(name, context, results)
                             if module in w.step_text(step)]
                    self.assertTrue(steps, f'{label}: the release path does not run {module}')
                    for name, step in steps:
                        environment = {**(self.workflow.get('env') or {}), **(self.jobs[name].get('env') or {}),
                                       **(step.get('env') or {})}
                        self.assertIn('ASSOZETA_HANDOFF_P5_IMAGES', environment,
                                      f'{label}: {module} must run against the candidate images, not a rebuild')

    def test_publication_waits_for_a_successful_qualification(self):
        self.require_qualification_job()
        publishers = self.publishers()
        self.assertTrue(publishers, 'fixture: some job publishes release artifacts')
        for label, raw in self.contexts.items():
            clean = w.simulate(self.workflow, raw)
            with self.subTest(entry_point=label, qualification='success'):
                self.assertTrue(any(clean[name] == 'success' for name in publishers),
                                f'{label}: nothing publishes after a successful qualification (unsatisfiable gate)')
            for bad in BAD_RESULTS:
                with self.subTest(entry_point=label, qualification=bad):
                    results = w.simulate(self.workflow, raw, {QUALIFY: bad})
                    ran = sorted(name for name in publishers if results[name] == 'success')
                    self.assertEqual(ran, [], f'{label}: {ran} publish although {QUALIFY} is {bad}')
                    for name in publishers:
                        self.assertIn(QUALIFY, w.upstream(self.jobs, name), f'{name} does not depend on {QUALIFY}')

    def test_release_images_are_promoted_not_rebuilt(self):
        self.require_qualification_job()
        for label, raw in self.contexts.items():
            with self.subTest(entry_point=label):
                context = w.with_env(self.workflow, raw)
                results = w.simulate(self.workflow, raw)
                downstream = [name for name in self.jobs if QUALIFY in w.upstream(self.jobs, name)
                              and results[name] == 'success']
                rebuilt, promoted = [], False
                for name in downstream:
                    for entry, step in self.enabled_steps(name, context, results):
                        if builds_and_pushes(step, push_value(step, self.jobs[name], context, results, entry)):
                            rebuilt.append(f'{name}[{entry.get("name", "")}]')
                        promoted |= 'tools/release/qualify.py promote' in w.step_text(step)
                self.assertEqual(rebuilt, [], f'{label}: release images rebuilt after the qualification')
                self.assertTrue(promoted, f'{label}: no step promotes the qualified digests '
                                          '(tools/release/qualify.py promote)')

    def test_published_digests_are_verified_before_the_bundle(self):
        self.require_qualification_job()
        for label, raw in self.contexts.items():
            with self.subTest(entry_point=label):
                context = w.with_env(self.workflow, raw)
                results = w.simulate(self.workflow, raw)
                bundle = [name for name in self.jobs if results[name] == 'success' and any(
                    'gh release upload' in w.step_text(step) and 'selfhost' in w.step_text(step)
                    for _, step in self.enabled_steps(name, context, results))]
                self.assertTrue(bundle, f'{label}: no job attaches the self-host bundle (fixture)')
                for name in bundle:
                    verifiers = [need for need in w.upstream(self.jobs, name) | {name} if results[need] == 'success'
                                 and any('tools/release/qualify.py verify-published' in w.step_text(step)
                                         for _, step in self.enabled_steps(need, context, results))]
                    self.assertTrue(verifiers, f'{label}: {name} attaches the bundle without '
                                               'tools/release/qualify.py verify-published before it')

    def test_the_qualification_is_attached_to_the_release(self):
        self.require_qualification_job()
        for label, raw in self.contexts.items():
            with self.subTest(entry_point=label):
                context = w.with_env(self.workflow, raw)
                results = w.simulate(self.workflow, raw)
                attached = any('gh release upload' in w.step_text(step)
                               and 'assozeta-release-qualification.json' in w.step_text(step)
                               for name in self.jobs if results[name] == 'success'
                               for _, step in self.enabled_steps(name, context, results))
                self.assertTrue(attached, f'{label}: assozeta-release-qualification.json is not a release asset')


class Simulator(unittest.TestCase):
    """Controls (green today): the simulator sees the gates publish-images already has."""

    def test_existing_gates_block_publication_and_a_removed_gate_is_noticed(self):
        workflow = w.load(PUBLISH)
        for label, raw in w.release_contexts(workflow).items():
            with self.subTest(entry_point=label):
                self.assertEqual(w.simulate(workflow, raw)['publish'], 'success')
                self.assertEqual(w.simulate(workflow, raw, {'production-smoke': 'failure'})['publish'], 'skipped')
                mutated = copy.deepcopy(workflow)
                mutated['jobs']['publish']['if'] = mutated['jobs']['publish']['if'].replace(
                    "needs.production-smoke.result == 'success' &&", '')
                mutated['jobs']['publish']['needs'].remove('production-smoke')
                self.assertEqual(w.simulate(mutated, raw, {'production-smoke': 'failure'})['publish'], 'success',
                                 'the simulator must notice a removed gate')

    def test_expression_semantics(self):
        context = {'github': {'event_name': 'Release'}, 'inputs': {'tag': ''}}
        self.assertTrue(w.evaluate("github.event_name == 'release'", context))
        self.assertEqual(w.evaluate("inputs.tag || 'x'", context), 'x')
        self.assertFalse(w.truthy(w.evaluate('inputs.missing', context)))
        self.assertTrue(w.evaluate("!cancelled() && startsWith('refs/tags/v1', 'refs/tags/')", context))


class OtherWorkflows(unittest.TestCase):
    """Guard (green today): only publish-images publishes release artifacts, so no other entry point
    (including manuale-release's own dispatch) can bypass the gate."""

    def test_no_other_workflow_publishes_release_artifacts(self):
        for path in sorted(w.WORKFLOWS.glob('*.yml')):
            if path.name == PUBLISH:
                continue
            workflow = w.load(path.name)
            with self.subTest(workflow=path.name):
                permissions = json.dumps([workflow.get('permissions')] + [job.get('permissions') for job in
                                                                         (workflow.get('jobs') or {}).values()])
                self.assertNotIn('"packages": "write"', permissions, f'{path.name} can write packages')
                for name, job in (workflow.get('jobs') or {}).items():
                    self.assertEqual(publishing_steps(workflow, job), [], f'{path.name}:{name} publishes')
