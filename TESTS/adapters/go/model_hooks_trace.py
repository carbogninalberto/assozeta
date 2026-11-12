"""Run one HTTP family through the acceptance driver while recording which branches
Payment.save() and Subscription.save() take on the Django side.

  python -m TESTS.adapters.go.model_hooks_trace --trace TRACE.jsonl -- --family F --output DIR [--versioned]

The tracer wraps the two save() overrides after Django is initialized. It records
only saves made while serving a request (not the main thread, which seeds fixtures
and checks effects) and never changes what save() does: the branch labels are
computed from the instance before the original save() runs, with read-only
queries inside a savepoint, and the original method then runs unchanged. Each
line names the workload label and the authored case that was being checked.
"""
import argparse
import functools
import json
from pathlib import Path
import sys
import threading
from datetime import datetime

DEFAULT_CATEGORY = 'entrate e proventi da attività tipiche'
STATE = {'label': None, 'case': None, 'request': None}
LOCK = threading.Lock()


def _creation_label(value):
    if isinstance(value, str):
        return 'subscription.creation.str'
    if value is None:
        return 'subscription.creation.none'
    if getattr(value, 'tzinfo', None) is not None:
        return 'subscription.creation.aware'
    return 'subscription.creation.naive' if hasattr(value, 'hour') else 'subscription.creation.date'


def _write(path, record):
    with LOCK, open(path, 'a') as stream:
        stream.write(json.dumps(record, sort_keys=True, default=str) + '\n')


def _related_meta(row):
    payment = row.payment
    return bool(payment and payment.meta and 'description' in payment.meta)


def course_description_label(payment):
    """The path get_course_carnet_name takes for a course payment (read-only replica of its lookups)."""
    from application.models.courses_models import CourseSubscription, CourseSubscriptionInstallment
    from application.models.carnet_models import CarnetSubscription
    row = CourseSubscription.objects.all_objects().filter(payment=payment).first()
    if row:
        return 'payment.description.course.subscription-' + ('meta' if _related_meta(row) else 'title')
    row = CourseSubscriptionInstallment.objects.all_objects().filter(payment=payment).first()
    if row:
        return 'payment.description.course.installment-' + ('meta' if _related_meta(row) else 'title')
    row = CarnetSubscription.objects.filter(payment=payment).first()
    if row:
        return 'payment.description.course.carnet-' + ('meta' if _related_meta(row) else 'title')
    if payment.course and 'label' in payment.course:
        return 'payment.description.course.label'
    return 'payment.description.course.quota'


def payment_labels(payment, kwargs):
    from application.models import PaymentCategory
    payment._model_hooks_description = payment.description
    labels = ['payment.adding' if payment._state.adding else 'payment.update']
    if kwargs.get('update_fields') is not None:
        labels.append('payment.update-fields')
    if payment.payment_category_id is not None:
        labels.append('payment.category.given')
    else:
        from django.db.models import Q
        named = PaymentCategory.objects.filter(name__iexact=DEFAULT_CATEGORY).order_by('pk')
        chosen = named.filter(Q(sport_association_id=payment.sport_association_id) | Q(sport_association__isnull=True)).first()
        labels.append('payment.category.default' if chosen else 'payment.category.default-missing')
        # A same-named category of another association sorts before the one save() takes.
        first = named.first()
        if first is not None and (chosen is None or first.pk != chosen.pk):
            labels.append('payment.category.default-other-association-first')
    subject = payment.subject
    if payment.description is not None:
        labels.append('payment.description.given' + ('' if payment.description else '-empty'))
    elif subject == payment.COURSE:
        labels.append(course_description_label(payment))
    elif subject == payment.SUBSCRIPTION:
        labels.append('payment.description.subscription')
    elif subject == payment.OTHER:
        has_category = payment.payment_category_id is not None or 'payment.category.default' in labels
        labels.append('payment.description.other-' + ('category' if has_category else 'none'))
    elif subject == payment.ACCOUNT_TRANSFER:
        labels.append('payment.description.transfer')
    else:
        labels.append('payment.description.unknown-subject')
    if payment.pk is None and payment.user_id is not None and payment.custom_accounts_id is None:
        labels.append('payment.default-account')
    return labels


def payment_after(payment, labels):
    """Labels known only once save() resolved the description: whether the description reached the
    'quota corso' check (then replaced by meta['description'] or kept)."""
    before = getattr(payment, '_model_hooks_description', None)
    text = payment.description
    replaced = bool(payment.meta and 'description' in payment.meta) and text == payment.meta.get('description')
    reached = (before and before.lower() == 'quota corso') or 'payment.description.course.quota' in labels \
        or (text and text.lower() == 'quota corso')
    if reached:
        labels.append('payment.quota.' + ('meta' if replaced else 'kept'))
    return labels


def subscription_labels(subscription, kwargs):
    labels = ['subscription.adding' if subscription._state.adding else 'subscription.update',
              _creation_label(subscription.creation_date)]
    if kwargs.get('update_fields') is not None:
        labels.append('subscription.update-fields')
    owner = subscription.sport_association.user
    labels.append(f'subscription.duration.{owner.subscription_duration}')
    if isinstance(subscription.creation_date, datetime):
        from application.utils.api_utils import BalanceSheetData
        try:  # the same pure call save() makes first
            BalanceSheetData.get_range_from_year_and_starting_date(
                date=subscription.creation_date, starting_day=owner.subscription_start_day,
                starting_month=owner.subscription_start_month, user=owner)
            labels.append('subscription.season.valid')
        except ValueError:
            labels.append('subscription.season.invalid')
    if owner.custom_end_date:
        labels.append('subscription.custom-end')
    labels.append('subscription.start.' + ('given' if subscription.start_date else 'missing'))
    labels.append('subscription.end.' + ('given' if subscription.end_date else 'missing'))
    return labels


def subscription_after(subscription, labels):
    for field in ('start', 'end'):
        if f'subscription.{field}.missing' in labels:
            filled = getattr(subscription, field + '_date') is not None
            labels.append(f'subscription.{field}.' + ('filled' if filled else 'left-null'))
    return labels


def install(trace):
    from django.db import transaction
    from application.models import Payment, Subscription

    def wrap(model, before, after):
        original = model.save

        @functools.wraps(original)
        def save(self, *args, **kwargs):
            if threading.current_thread() is threading.main_thread():
                return original(self, *args, **kwargs)
            record = {'model': model.__name__, 'label': STATE['label'], 'case': STATE['case'], 'request': STATE['request']}
            try:
                with transaction.atomic(using=kwargs.get('using') or self._state.db or 'default'):
                    labels = before(self, kwargs)
            except Exception as error:  # the labels are observational; save() still runs
                labels = ['trace.label-error:' + type(error).__name__]
            try:
                result = original(self, *args, **kwargs)
            except BaseException as error:
                record.update(labels=labels, outcome='raised:' + type(error).__name__)
                _write(trace, record)
                raise
            record.update(labels=after(self, labels), outcome='saved')
            _write(trace, record)
            return result

        model.save = save

    wrap(Payment, payment_labels, payment_after)
    wrap(Subscription, subscription_labels, subscription_after)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('rest', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    rest = args.rest[1:] if args.rest[:1] == ['--'] else args.rest
    if not rest or '--family' not in rest or '--output' not in rest:
        sys.exit('GUARD: driver arguments (--family, --output) are required')
    trace = args.trace.resolve()
    if trace.exists():
        sys.exit(f'GUARD: trace file already exists: {trace}')
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.touch()

    from TESTS.adapters.django import message_reads_runtime as reuse
    from TESTS.adapters.django import staff_board_http_acceptance as driver
    from TESTS.contracts import http_family
    from TESTS import http_client

    initialize = reuse.initialize_django_apps

    def initialize_and_trace(*a, **k):
        result = initialize(*a, **k)
        install(trace)
        return result

    reuse.initialize_django_apps = initialize_and_trace

    family_runner = driver.family_runner

    def traced_runner(family):
        runner = family_runner(family)
        run_workload = runner.run_workload
        if getattr(run_workload, '_model_hooks', False):
            return runner

        def run(label, *a, **k):
            STATE.update(label=label, case=None, request=None)
            return run_workload(label, *a, **k)

        run._model_hooks = True
        runner.run_workload = run
        return runner

    driver.family_runner = traced_runner

    assert_case = http_family.assert_case

    def traced_assert_case(case, *a, **k):
        STATE.update(case=case.get('name'), request=None)
        return assert_case(case, *a, **k)

    http_family.assert_case = traced_assert_case

    request = http_client.Client.request

    def traced_request(self, method, path, *a, **k):
        STATE['request'] = f'{method} {path}'
        return request(self, method, path, *a, **k)

    http_client.Client.request = traced_request
    return driver.main(rest)


if __name__ == '__main__':
    raise SystemExit(main())
