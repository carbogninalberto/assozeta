"""Model-hook parity for Payment.save() and Subscription.save() through every Go writer.

  python -m TESTS.adapters.go.model_hooks_process [--label L] [--families a,b] [--discover]
  MODEL_HOOKS_SHARD=I/N python -m TESTS.adapters.go.model_hooks_process [--label L]
  python -m TESTS.adapters.go.model_hooks_process [--label L] --merge-shards PATH [PATH ...]

Split FAMILIES in order into blocks of up to N names and reverse every second
block, including the final block if it falls on a reverse turn. Shard I takes
the Ith name (1-based) from each block with at least I names.
A passing shard reports shard_success. Merge every shard's results.json to certify
full coverage at the current HEAD.

1. Writer inventory: every SQL statement in BE_GO that inserts, updates or deletes
   application_payment / application_subscription rows must be listed in WRITERS with
   what Django does at that point (a save() that runs the override, a queryset or
   collector write that does not) and the parity harnesses that exercise it. A writer
   that is not listed, or a listed writer that no longer exists, fails the run.
2. Branch trace: each HTTP family in FAMILIES runs natively on Django and Go
   (TESTS.adapters.go.model_hooks_trace) with a tracer on the two save() overrides.
   Every family must pass on both transports (status, body, complete database state,
   audit entries and index rows after every case), and its Django side must save the
   models the inventory says it saves, and no others.
3. Coverage: every branch label in REQUIRED must be taken by at least one Django save
   inside a passing family, so the Go writers reproduced that branch's result.
   Labels in UNREACHABLE must never be observed (their reason is recorded).

Worker and consumer writers (tasks, archive, import, signature migration) are covered
by their own process harnesses, listed per writer; Django runs those in separate
processes, so their branches are recorded in WORKER_BRANCHES from their scenarios.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
REPORTS = ROOT / 'quality-reports/go-migration'
GO_BIN = os.environ.get('GO_MIGRATION_GO_BIN', str(Path.home() / 'go/pkg/mod/golang.org/toolchain@v0.0.1-go1.25.12.darwin-arm64/bin'))
WRITE = re.compile(r'(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?application_(payment|subscription)"?(?=[\s(]|$)')
GENERIC = re.compile(r'(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+`\s*\+\s*(\w+(?:\.\w+)*)')

# file:function:op:table -> (what Django does there, the harnesses exercising it)
WRITERS = {
    'camps_periods.go:savePayment:UPDATE:payment': ('save', ['http:camps-periods']),
    'camps_subscriptions.go:createPayment:INSERT:payment': ('save', ['http:camps-subscriptions']),
    'carnet_writes.go:CreateTopup:INSERT:payment': ('save', ['http:carnet-subscription-topup', 'http:carnet-assign', 'http:carnet-add', 'http:carnet-replace']),
    'carnet_writes.go:deleteReplacePayment:DELETE:payment': ('collector', ['http:carnet-replace']),
    'course_enrolment.go:CreatePayment:INSERT:payment': ('save', ['http:course-enrolment', 'http:router-course-subscriptions']),
    'course_subscriptions.go:SavePayment:UPDATE:payment': ('save', ['http:course-subscriptions', 'http:router-course-subscriptions']),
    'course_subscriptions.go:deletePayments:DELETE:payment': ('collector', ['http:course-subscriptions']),
    # signals.update_metadata_courses: bulk_update(['meta', 'description']), no save().
    'course_writes.go:RewritePayment:UPDATE:payment': ('queryset', ['http:course-writes']),
    'course_writes.go:CreatePayment:INSERT:payment': ('save', ['http:course-writes']),
    'documents_writes.go:AttachCertificate:UPDATE:subscription': ('save', ['http:documents-writes']),
    'instructor_writes.go:DeleteInstructor:UPDATE:payment': ('collector', ['http:instructor-writes']),
    'instructor_writes.go:DeleteInstructor:UPDATE:subscription': ('collector', ['http:instructor-writes']),
    'instructor_writes.go:InsertPayment:INSERT:payment': ('save', ['http:instructor-writes']),
    'instructor_writes.go:SetPaymentNotes:UPDATE:payment': ('save', ['http:instructor-writes']),
    'invoice_core.go:UnlinkPayment:UPDATE:payment': ('save', ['http:invoice-core']),
    'invoice_core.go:DeleteInvoices:UPDATE:payment': ('collector', ['http:invoice-core']),
    'invoice_generate.go:SaveGeneratedPayment:UPDATE:payment': ('save', ['http:invoice-generate']),
    'invoice_parties.go:CreateSupplierPayment:INSERT:payment': ('save', ['http:invoice-parties']),
    'invoice_parties.go:CreateSupplierPayment:UPDATE:payment': ('save', ['http:invoice-parties']),
    'invoice_parties.go:UpdatePaymentPaid:UPDATE:payment': ('save', ['http:invoice-parties']),
    'invoice_sync_print.go:SaveInvoicePayment:UPDATE:payment': ('save', ['http:invoice-sync-print']),
    'payment_archive.go:SavePaymentArchive:UPDATE:payment': ('save', ['http:payment-archive', 'http:subscription-lifecycle']),
    'payment_misc.go:SetPaymentSignature:UPDATE:payment': ('save', ['http:payment-misc']),
    'payment_paid_flow.go:SavePaidFlowPayment:UPDATE:payment': ('save', ['http:payment-state', 'http:stripe-routes']),
    'payment_paid_flow.go:MarkStripePaymentPaid:UPDATE:payment': ('save', ['http:stripe-routes']),
    'payment_state.go:SavePaymentState:UPDATE:payment': ('save', ['http:payment-state']),
    'payment_state.go:MarkPayments:UPDATE:payment': ('queryset', ['http:payment-state']),
    'payment_writes.go:InsertPaymentRow:INSERT:payment': ('save', ['http:payment-writes', 'http:payment-misc',
                                                                   'process:quote_assign_worker_process']),
    'payment_writes.go:SavePaymentRow:UPDATE:payment': ('save', ['http:payment-writes']),
    'payment_writes.go:InsertBulkPayments:INSERT:payment': ('queryset', ['http:payment-writes']),
    'persona_subs_writes.go:SaveSubscription:UPDATE:subscription': ('save', ['http:router-persona-subscriptions']),
    'stripe_paid_check.go:SaveStripePaidCheckType:UPDATE:payment': ('save', ['process:stripe_paid_check_worker_process']),
    'stripe_routes.go:SavePaymentIntent:UPDATE:payment': ('save', ['http:stripe-routes']),
    'subscription_approve.go:SaveSignature:UPDATE:subscription': ('save', ['http:subscription-approve']),
    'subscription_create.go:InsertPayment:INSERT:payment': ('save', ['http:subscription-create', 'http:subscription-renew']),
    'subscription_create.go:SaveSubscriptionSignature:UPDATE:subscription': ('save', ['http:subscription-create']),
    'subscription_create.go:InsertSubscription:INSERT:subscription': ('save', ['http:subscription-create', 'http:subscription-renew']),
    'subscription_lifecycle.go:SaveSubscription:UPDATE:subscription': ('save', ['http:subscription-lifecycle', 'http:subscription-approve',
                                                                                'http:celery-routes-a']),
    'subscription_medical.go:AttachNewCertificate:UPDATE:subscription': ('save', ['http:subscription-medical']),
    'subscription_medical.go:SetSubscriptionMedical:UPDATE:subscription': ('save', ['http:subscription-medical']),
    'subscription_medical.go:DetachSubscription:UPDATE:subscription': ('save', ['http:subscription-medical']),
    'subscription_medical.go:DeleteCertificate:UPDATE:subscription': ('collector', ['http:subscription-medical']),
    'subscription_sign.go:Save:UPDATE:subscription': ('save', ['http:subscription-sign']),
    'subscription_signature_migration.go:Save:UPDATE:subscription': ('save', ['component:subscription-signature-migration']),
    'subscription_tags.go:SaveSubscriptionDates:UPDATE:subscription': ('save', ['http:subscription-tags']),
    'subscription_update.go:SaveSubscription:UPDATE:subscription': ('save', ['http:celery-routes-a']),
    'tasks_db_d.go:MarkStripeType:UPDATE:payment': ('queryset', ['process:tasks_db_d_worker_process']),
    'tasks_db_d.go:CreateRenewalPayment:INSERT:payment': ('save', ['process:tasks_db_d_worker_process']),
    # Generic writers (the table is chosen at run time) that reach the two tables.
    'subscription_auto_archive.go:archiveRow:UPDATE:m.table': ('save', ['process:nightly_subscriptions_worker_process']),
    'subscription_transfer.go:saveUser:UPDATE:table': ('save', ['http:subscription-transfer']),
    'document_render.go:Link:UPDATE:link.Table': ('save', ['http:document-render']),
    'carnet_writes.go:clearPaymentReferences:UPDATE:pq': ('collector', ['http:carnet-replace']),
    # Bulk archive delete and the other document / stock / subscription deletes run the shared collector
    # (Collector.delete(): SET_NULL updates and cascade deletes, no save()).
    'collaborator_delete_collector.go:delete:UPDATE:update.table': ('collector', ['http:documents-bulk-delete', 'http:documents-delete',
                                                                                  'http:subscription-lifecycle']),
    'collaborator_delete_collector.go:delete:DELETE:bucket.model.Table': ('collector', ['http:documents-bulk-delete', 'http:documents-delete',
                                                                                        'http:subscription-lifecycle']),
    'collaborator_delete_collector.go:delete:DELETE:fast.model.Table': ('collector', ['http:documents-bulk-delete', 'http:documents-delete',
                                                                                      'http:subscription-lifecycle']),
    'instructor_hours.go:DeleteDocument:UPDATE:ref.Table': ('collector', ['http:instructor-hours']),
    'supplier_writes.go:DeleteSupplier:UPDATE:pq': ('collector', ['http:supplier-deletes']),
}
# The shared deletion collector (Django's Collector.delete(): SET_NULL updates and cascade deletes by model graph)
# writes payment and subscription rows through run-time table names and never calls save(). Its SQL carries no table
# literal, so the scan reads the file anyway.
COLLECTOR_FILES = {'collaborator_delete_collector.go'}
# Generic writers in the same files whose tables are never application_payment / application_subscription.
OTHER_TABLES = {
    'camps_periods.go:DeletePeriod:DELETE:campsPeriodLinks', 'camps_periods.go:DeletePeriod:DELETE:campsSubscriptionLinks',
    'camps_periods.go:DeleteService:DELETE:campsPeriodLinks', 'camps_periods.go:DeleteService:DELETE:campsSubscriptionLinks',
    'camps_periods.go:LinkPeriodServices:DELETE:campsPeriodLinks', 'camps_periods.go:LinkPeriodServices:INSERT:campsPeriodLinks',
    'camps_subscriptions.go:DeleteSubscriptionPeriod:DELETE:campsSubscriptionLinks',
    'camps_subscriptions.go:LinkSubscriptionPeriod:INSERT:campsSubscriptionLinks',
    'carnet_writes.go:clearPaymentReferences:DELETE:pq', 'instructor_writes.go:DeleteInstructor:DELETE:table',
}
# Import effects write every imported model through the plan (internal/importeffects), with the
# Payment and Subscription save() overrides in overrides.go; checked by name below.
IMPORT_EFFECTS = ('BE_GO/internal/importeffects/overrides.go', ('paymentSave', 'subscriptionSave'),
                  'process:celery_consumer_import_process')

# HTTP families whose Go routes reach the writers above, and the models their Django side saves.
FAMILIES = {
    'camps-periods': {'Payment'}, 'camps-subscriptions': {'Payment'}, 'carnet-subscription-topup': {'Payment'},
    # carnet-replace saves the replaced payment before deleting it; router-course-subscriptions reaches the
    # course writers only on paths Django refuses before save() (its writes are covered by the two families above).
    'carnet-replace': {'Payment'}, 'carnet-assign': {'Payment'}, 'carnet-add': {'Payment'},
    'course-enrolment': {'Payment'}, 'course-writes': {'Payment'},
    'course-subscriptions': {'Payment'}, 'router-course-subscriptions': set(),
    'instructor-writes': {'Payment'}, 'invoice-core': {'Payment'}, 'invoice-generate': {'Payment'},
    'invoice-parties': {'Payment'}, 'invoice-sync-print': {'Payment'}, 'payment-archive': {'Payment'},
    'payment-state': {'Payment'}, 'payment-writes': {'Payment'}, 'payment-misc': {'Payment'},
    'stripe-routes': {'Payment'}, 'subscription-create': {'Payment', 'Subscription'},
    'subscription-renew': {'Payment', 'Subscription'}, 'subscription-lifecycle': {'Payment', 'Subscription'},
    'subscription-approve': {'Subscription'}, 'celery-routes-a': {'Subscription'},
    'documents-writes': {'Subscription'}, 'router-persona-subscriptions': {'Subscription'},
    'subscription-medical': {'Subscription'}, 'subscription-sign': {'Subscription'},
    'subscription-tags': {'Subscription'}, 'subscription-transfer': {'Subscription'},
    'document-render': {'Subscription'},
}

# Branches of the two overrides (labels written by model_hooks_trace) that a Django save() inside a passing
# family must take, so a Go writer reproduced the branch's stored result, audit entries and index rows.
REQUIRED = [
    'payment.adding', 'payment.update', 'payment.update-fields',
    'payment.category.given', 'payment.category.default', 'payment.category.default-other-association-first',
    'payment.description.given', 'payment.description.subscription', 'payment.description.other-category',
    'payment.description.course.subscription-title', 'payment.description.course.installment-meta',
    'payment.description.course.carnet-meta', 'payment.description.course.carnet-title',
    'payment.description.course.label', 'payment.description.course.quota',
    'payment.quota.meta', 'payment.quota.kept',
    'subscription.adding', 'subscription.update', 'subscription.update-fields', 'subscription.creation.aware',
    'subscription.season.valid', 'subscription.duration.1', 'subscription.duration.2', 'subscription.duration.3',
    'subscription.start.given', 'subscription.start.filled', 'subscription.end.given', 'subscription.end.filled',
]
# Branches whose save() raises: the family's case (and Go) must answer and leave state as Django does.
REQUIRED_RAISED = ['payment.description.transfer', 'subscription.season.invalid']
# Branches no Go-served path can take, with the reason; observing one fails the run.
UNREACHABLE = {
    'payment.default-account': 'payment_id has a uuid4 default, so self.pk is never None inside save()',
}
# Branches not taken by the traced families; where their result is established instead.
NOT_TRACED = {
    'payment.category.default-missing / payment.description.other-none': 'needs the global default category to be absent; '
        'the fixtures keep the migration-seeded row. Calculator: TESTS/adapters/django/payment_description.py.',
    'payment.description.course.subscription-meta / installment-title': 'same lookup (PaymentLinkedName) as the traced '
        'subscription-title and installment-meta branches; calculator oracle: payment_description.py.',
    'payment.description.given-empty / unknown-subject': 'calculator oracle: payment_description.py.',
    'subscription.creation.str / naive': 'no Go-served writer passes a str or naive creation_date (DRF parses inputs); '
        'the import consumer refuses such values; calculator oracle: TESTS/adapters/django/subscription_dates.py.',
    'subscription.duration.0/4/5, custom-end, start/end left-null': 'shared ResolveSubscriptionDates; oracle: '
        'subscription_dates.py (293 ranges, 36 real saves).',
}
# Writers Django runs in other processes (workers, consumers, commands): their own harnesses compare the
# complete state after Django and Go; the branches their scenarios take.
WORKER_BRANCHES = {
    'process:tasks_db_d_worker_process': 'renewal Payment.objects.create: default category, explicit description',
    'process:stripe_paid_check_worker_process': "type save(update_fields) then the paid flow's save(); "
        'a save() that raises (account transfer without description) rolls the payment back and the run goes on',
    'process:quote_assign_worker_process': 'Payment.objects.create(subject=1): default category, Iscrizione',
    'process:nightly_subscriptions_worker_process': "save(update_fields=['archived']) of subscription, payment, invoice",
    'process:celery_consumer_import_process': 'importeffects paymentSave/subscriptionSave: five payments over the '
        'description rules and the default category; dated subscriptions',
    'component:subscription-signature-migration': "save(update_fields=['signature_url', 'signature_storage_key'])",
}


def inventory():
    """The writer statements in BE_GO, keyed like WRITERS."""
    found, problems = collections.defaultdict(list), []
    for path in sorted((ROOT / 'BE_GO').rglob('*.go')):
        if path.name.endswith('_test.go'):
            continue
        text = path.read_text()
        for pattern in (WRITE, GENERIC):
            if (pattern is GENERIC and path.name not in COLLECTOR_FILES
                    and 'application_payment' not in text and 'application_subscription' not in text):
                continue
            for match in pattern.finditer(text):
                functions = re.findall(r'^func (?:\([^)]*\)\s*)?(\w+)', text[:match.start()], re.M)
                op = match.group(1).split()[0].upper()
                table = match.group(2)
                key = f'{path.name}:{functions[-1] if functions else "-"}:{op}:{table}'
                found[key].append(f'{path.relative_to(ROOT)}:{text.count(chr(10), 0, match.start()) + 1}')
    unlisted = sorted(set(found) - set(WRITERS) - OTHER_TABLES)
    if unlisted:
        problems.append('writers missing from the inventory: ' + ', '.join(unlisted))
    stale = sorted((set(WRITERS) | OTHER_TABLES) - set(found))
    if stale:
        problems.append('inventory lists writers that no longer exist: ' + ', '.join(stale))
    source = (ROOT / IMPORT_EFFECTS[0]).read_text()
    for name in IMPORT_EFFECTS[1]:
        if not re.search(rf'^func \(e \*Effects\) {name}\(', source, re.M):
            problems.append(f'{IMPORT_EFFECTS[0]} no longer defines {name}')
    for key, (kind, harnesses) in WRITERS.items():
        if kind not in ('save', 'queryset', 'collector') or not harnesses:
            problems.append(f'{key}: invalid inventory row')
        for harness in harnesses:
            if kind == 'save' and harness.startswith('http:') and harness[5:] not in FAMILIES:
                problems.append(f'{key}: {harness} is not traced')
    return {'writers': {k: v for k, v in sorted(found.items())}, 'problems': problems}


def git_head(*options):
    head = subprocess.check_output(['git', 'rev-parse', *options, 'HEAD'], cwd=ROOT, text=True).strip()
    if not head:
        raise SystemExit('GUARD: cannot read HEAD')
    return head


def next_label(shard=None):
    head = git_head('--short=8')
    if shard is not None:
        head += f'-shard-{shard[0]}-of-{shard[1]}'
    number = 1
    while (REPORTS / f'model-hooks-{head}-r{number}').exists():
        number += 1
    return f'{head}-r{number}'


def shard_families(index, count):
    if not 1 <= index <= count <= len(FAMILIES):
        raise ValueError(f'shard must satisfy 1 <= I <= N <= {len(FAMILIES)}')
    names = list(FAMILIES)
    selected = []
    for block_number, start in enumerate(range(0, len(names), count)):
        block = names[start:start + count]
        if block_number % 2:
            block.reverse()
        if index <= len(block):
            selected.append(block[index - 1])
    return selected


def run_family(family, output):
    trace, report = output / f'{family}.trace.jsonl', output / f'{family}-http'
    log = output / f'{family}.log'
    environment = {**os.environ, 'PATH': GO_BIN + os.pathsep + os.environ.get('PATH', '')}
    command = [sys.executable, '-m', 'TESTS.adapters.go.model_hooks_trace', '--trace', str(trace), '--',
               '--family', family, '--output', str(report)]
    with log.open('w') as stream:
        code = subprocess.run(command, cwd=ROOT, env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=3600).returncode
    result = {'returncode': code, 'success': False, 'cases': {}, 'saves': {}, 'labels': {}}
    path = report / 'results.json'
    if not path.is_file() or not path.stat().st_size:
        result['error'] = 'no results.json'
        return result, []
    data = json.loads(path.read_text())
    workloads = data.get('workloads') or []
    counts = {w.get('label'): len(w.get('cases') or []) for w in workloads}
    result['cases'] = counts
    result['errors'] = data.get('errors')
    result['success'] = bool(code == 0 and data.get('success') is True and list(counts) == ['django-native', 'go-native']
                             and counts['django-native'] and counts['django-native'] == counts['go-native'])
    records = [json.loads(line) for line in trace.read_text().splitlines()] if trace.is_file() else []
    saves = collections.Counter(r['model'] for r in records if r['label'] == 'django-native')
    stray = [r for r in records if r['label'] != 'django-native']
    if stray:
        result['success'] = False
        result['error'] = f'{len(stray)} Django saves outside the Django workload'
    result['saves'] = dict(sorted(saves.items()))
    return result, records


def write_report(output, report):
    (output / 'results.json').write_text(json.dumps(report, indent=1, sort_keys=True) + '\n')


def run_families(selected, output, report):
    found = inventory()
    report['inventory'] = found['writers']
    report['problems'] += found['problems']
    coverage = collections.defaultdict(set)
    observed = collections.defaultdict(set)
    for family in selected:
        result, records = run_family(family, output)
        report['families'][family] = result
        write_report(output, report)
        if not result['success']:
            report['problems'].append(f'{family}: family did not pass on native Django and Go ({result.get("error") or result.get("errors")})')
            continue
        models = set(result['saves'])
        if models != FAMILIES[family]:
            report['problems'].append(f'{family}: Django saved {sorted(models)}, inventory says {sorted(FAMILIES[family])}')
        labels = collections.Counter()
        for record in records:
            for item in record['labels']:
                tag = f'{item}@{record["outcome"]}'
                labels[tag] += 1
                observed[tag].add(f'{family}:{record["case"]}')
                if record['outcome'] == 'saved' or item.startswith('trace.'):
                    coverage[item].add(f'{family}:{record["case"]}')
        result['labels'] = dict(sorted(labels.items()))
        if any(k.startswith('trace.') for k in labels):
            report['problems'].append(f'{family}: tracer could not label a save')
    report['coverage'] = {k: sorted(v) for k, v in sorted(coverage.items())}
    report['observed'] = {k: sorted(v) for k, v in sorted(observed.items())}


def check_coverage(report):
    coverage, observed = report['coverage'], report['observed']
    if not REQUIRED:
        report['problems'].append('REQUIRED is empty')
    missing = [label for label in REQUIRED if label not in coverage]
    missing += [label + '@raised' for label in REQUIRED_RAISED
                if not any(k.startswith(label + '@raised') for k in observed)]
    if missing and not report['partial']:
        report['problems'].append('required branches not taken by a passing family: ' + ', '.join(missing))
    seen = [label for label in UNREACHABLE if label in coverage or any(k.startswith(label + '@') for k in observed)]
    if seen:
        report['problems'].append('branches recorded as unreachable were taken: ' + ', '.join(seen))
    report['required'] = {label: coverage.get(label, []) for label in REQUIRED}
    report['required_raised'] = {label: sorted({c for k, v in observed.items() if k.startswith(label + '@raised') for c in v})
                                 for label in REQUIRED_RAISED}
    report['not_traced'] = NOT_TRACED
    report['unreachable'] = UNREACHABLE
    report['worker_branches'] = WORKER_BRANCHES


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key {key!r}')
        result[key] = value
    return result


def read_shard(path, revision):
    part = json.loads(path.read_text(), object_pairs_hook=unique_json_object)
    if not isinstance(part, dict) or part.get('source_revision') != revision:
        raise ValueError(f'shard source revision must match HEAD ({revision})')
    shard = part.get('shard')
    if not isinstance(shard, list) or len(shard) != 2 or any(type(value) is not int for value in shard):
        raise ValueError('invalid shard identity, expected [I, N]')
    expected = shard_families(*shard)
    if (part.get('shard_success') is not True or part.get('problems') != [] or part.get('success') is not False
            or part.get('partial') is not True or part.get('discover') is not False):
        raise ValueError(f'shard did not pass (problems: {part.get("problems")})')
    families = part.get('families')
    if not isinstance(families, dict) or families.keys() != set(expected):
        raise ValueError('shard family membership must be exactly: ' + ', '.join(expected))
    for name, result in families.items():
        if not isinstance(result, dict) or result.get('success') is not True:
            raise ValueError(f'{name}: family did not pass')
    if not isinstance(part.get('inventory'), dict):
        raise ValueError('missing writer inventory')
    prefixes = tuple(f'{name}:' for name in expected)
    for field in ('coverage', 'observed'):
        evidence = part.get(field)
        if not isinstance(evidence, dict):
            raise ValueError(f'invalid {field} evidence')
        for tag, cases in evidence.items():
            if (not tag or not isinstance(cases, list) or not cases
                    or any(not isinstance(case, str) or not case.startswith(prefixes) for case in cases)):
                raise ValueError(f'invalid {field} evidence for {tag!r}')
    return part


def merge_shards(paths, report):
    count, seen = None, set()
    evidence = {field: collections.defaultdict(set) for field in ('coverage', 'observed')}
    for path in paths:
        try:
            part = read_shard(path, report['source_revision'])
            index, total = part['shard']
            if count is not None and total != count:
                raise ValueError(f'shard count {total} does not match {count}')
            if index in seen:
                raise ValueError(f'duplicate shard {index}/{total}')
            if 'inventory' in report and part['inventory'] != report['inventory']:
                raise ValueError('writer inventory differs between shards')
        except (OSError, ValueError) as error:
            report['problems'].append(f'{path}: {error}')
            continue
        count = total
        seen.add(index)
        report['inventory'] = part['inventory']
        report['families'].update(part['families'])
        for field, joined in evidence.items():
            for tag, cases in part[field].items():
                joined[tag].update(cases)
    if count is not None:
        missing = [f'{index}/{count}' for index in range(1, count + 1) if index not in seen]
        if missing:
            report['problems'].append('missing shards: ' + ', '.join(missing))
    report.setdefault('inventory', {})
    report['partial'] = report['families'].keys() != FAMILIES.keys()
    for field, joined in evidence.items():
        report[field] = {tag: sorted(cases) for tag, cases in sorted(joined.items())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--label')
    parser.add_argument('--families', help='comma-separated subset (a partial run never reports success)')
    parser.add_argument('--discover', action='store_true', help='report coverage without enforcing REQUIRED')
    parser.add_argument('--merge-shards', metavar='PATH', nargs='+', type=Path, action='extend',
                        help='join shard results.json files')
    args = parser.parse_args(argv)
    shard = None
    selected = list(FAMILIES)
    if args.merge_shards:
        if args.families is not None or args.discover:
            parser.error('--merge-shards cannot be combined with --families or --discover')
    else:
        shard_spec = os.environ.get('MODEL_HOOKS_SHARD')
        if shard_spec is not None:
            if args.families is not None or args.discover:
                parser.error('MODEL_HOOKS_SHARD cannot be combined with --families or --discover')
            match = re.fullmatch(r'([0-9]+)/([0-9]+)', shard_spec)
            if not match:
                parser.error('MODEL_HOOKS_SHARD must be I/N')
            try:
                shard = (int(match[1]), int(match[2]))
                selected = shard_families(*shard)
            except ValueError as error:
                parser.error(f'MODEL_HOOKS_SHARD: {error}')
        elif args.families:
            selected = [family for family in args.families.split(',') if family]
        from TESTS.adapters.django.production_runtime import require_production_python
        require_production_python()
        unknown = [family for family in selected if family not in FAMILIES]
        if not selected or unknown:
            raise SystemExit('GUARD: no families selected or unknown families: ' + ', '.join(unknown))
    revision = git_head() if shard is not None or args.merge_shards else None
    label = args.label or next_label(shard)
    output = REPORTS / f'model-hooks-{label}'
    if output.exists():
        raise SystemExit(f'GUARD: report directory exists: {output}')
    output.mkdir(parents=True)
    report = {'success': False, 'label': label, 'partial': shard is not None or selected != list(FAMILIES),
              'discover': args.discover, 'scope': __doc__.strip().splitlines()[0], 'families': {}, 'coverage': {},
              'problems': []}
    if revision is not None:
        report['source_revision'] = revision
    if shard is not None:
        report.update(shard=shard, shard_success=False)
    if args.merge_shards:
        merge_shards(args.merge_shards, report)
    else:
        run_families(selected, output, report)
    if shard is None and not args.discover:
        check_coverage(report)
    passed = sum(1 for r in report['families'].values() if r['success'])
    if shard is not None:
        report['shard_success'] = not report['problems'] and passed == len(selected)
    else:
        report['success'] = not report['problems'] and not report['partial'] and not args.discover and passed == len(FAMILIES)
    write_report(output, report)
    summary = {'success': report['success'], 'label': label, 'families': len(report['families']), 'passed': passed,
               'required': len(REQUIRED), 'problems': report['problems'][:5]}
    if shard is not None:
        summary.update(shard=shard, shard_success=report['shard_success'])
    print(json.dumps(summary))
    return 0 if report.get('shard_success', report['success']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
