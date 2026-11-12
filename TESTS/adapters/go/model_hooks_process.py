"""Model-hook parity for Payment.save() and Subscription.save() through every Go writer.

  python -m TESTS.adapters.go.model_hooks_process [--label L] [--families a,b] [--discover]

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
    'camps_periods.go:SavePayment:UPDATE:payment': ('save', ['http:camps-periods']),
    'camps_subscriptions.go:CreatePayment:INSERT:payment': ('save', ['http:camps-subscriptions']),
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
    'orphan_medical_cleanup.go:DeleteOrphanMedicalDocument:UPDATE:table': ('collector', ['process:orphan_medical_worker_process']),
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


def next_label():
    head = subprocess.run(['git', 'rev-parse', '--short=8', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if not head:
        raise SystemExit('GUARD: cannot read HEAD')
    number = 1
    while (REPORTS / f'model-hooks-{head}-r{number}').exists():
        number += 1
    return f'{head}-r{number}'


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--label')
    parser.add_argument('--families', help='comma-separated subset (a partial run never reports success)')
    parser.add_argument('--discover', action='store_true', help='report coverage without enforcing REQUIRED')
    args = parser.parse_args(argv)
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    selected = list(FAMILIES) if not args.families else [f for f in args.families.split(',') if f]
    unknown = [f for f in selected if f not in FAMILIES]
    if not selected or unknown:
        raise SystemExit('GUARD: no families selected or unknown families: ' + ', '.join(unknown))
    label = args.label or next_label()
    output = REPORTS / f'model-hooks-{label}'
    if output.exists():
        raise SystemExit(f'GUARD: report directory exists: {output}')
    output.mkdir(parents=True)
    report = {'success': False, 'label': label, 'partial': selected != list(FAMILIES), 'discover': args.discover,
              'scope': __doc__.strip().splitlines()[0], 'families': {}, 'coverage': {}, 'problems': []}
    found = inventory()
    report['inventory'] = found['writers']
    report['problems'] += found['problems']
    coverage = collections.defaultdict(set)
    observed = collections.defaultdict(set)
    for family in selected:
        result, records = run_family(family, output)
        report['families'][family] = result
        (output / 'results.json').write_text(json.dumps(report, indent=1, sort_keys=True) + '\n')
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
    if not args.discover:
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
        report['required'] = {label: report['coverage'].get(label, []) for label in REQUIRED}
        report['required_raised'] = {label: sorted({c for k, v in observed.items() if k.startswith(label + '@raised') for c in v})
                                     for label in REQUIRED_RAISED}
        report['not_traced'] = NOT_TRACED
        report['unreachable'] = UNREACHABLE
        report['worker_branches'] = WORKER_BRANCHES
    report['success'] = bool(not report['problems'] and not report['partial'] and not args.discover
                             and all(r['success'] for r in report['families'].values())
                             and len(report['families']) == len(FAMILIES))
    (output / 'results.json').write_text(json.dumps(report, indent=1, sort_keys=True) + '\n')
    print(json.dumps({'success': report['success'], 'label': label, 'families': len(report['families']),
                      'passed': sum(1 for r in report['families'].values() if r['success']),
                      'required': len(REQUIRED), 'problems': report['problems'][:5]}))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
