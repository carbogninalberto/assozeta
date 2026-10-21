"""Explicit shared-database resource visibility contracts, not HTTP admission.

Fixtures use bulk inserts to isolate reads from subscription/payment hooks. The
scope assertions call the actual Django managers and scoped_queryset. Expected
IDs below are authored independently; they are never copied from Django output.
"""
import json
from pathlib import Path
from types import SimpleNamespace


def uid(number):
    return f'00000000-0000-4000-8000-{number:012d}'


def prepare(work):
    from django.utils import timezone
    from django.db import connection, transaction
    from django.test.utils import CaptureQueriesContext
    from application import models as m
    from application.models.carnet_models import CarnetSubscription
    from application.models.subscriptions_models import SubscriptionFile
    from application.impersonation_scope import scoped_queryset
    from application.mixin import _local
    from docmanager.models import Document

    # This adapter owns only synthetic rows in the private database created by
    # accounts.py. Bypass business hooks deliberately; this certifies reads only.
    def insert(model, rows):
        model.objects.bulk_create([model(**row) for row in rows])

    insert(m.User, [dict(user_id=uid(n), username=f'scope-{n}', email=f'scope-{n}@example.test',
                        password='!scope-only', role=2) for n in (30, 31)])
    insert(m.Group, [dict(group_id=uid(n), sport_association_id=uid(a), name=f'Scope {n}')
                     for n, a in [(1001, 80), (1002, 80), (1003, 90)]])
    members = [
        (1010, 80, 30, 1001, 'OWN', False), (1011, 80, 31, 1001, 'FAMILY', False),
        (1012, 80, 31, 1001, 'STRANGER', False), (1013, 90, 30, 1003, 'FOREIGN', False),
        (1014, 80, 30, 1001, 'DELETED', True), (1015, 80, 30, 1002, 'OTHER_GROUP', False),
        (1016, 80, 30, None, '', False), (1017, 80, 31, 1001, 'LEGACY', True),
        (1018, 80, 31, 1001, 'LEGACY', False),
        (1019, 80, 31, 1001, 'FOREIGN', False),  # foreign subscription cannot seed local family
    ]
    insert(m.Associate, [dict(associate_id=uid(n), sport_association_id=uid(a), user_id=uid(u),
                             group_id=uid(g) if g else None, tax_code=tax, deleted=deleted)
                        for n, a, u, g, tax, deleted in members])
    insert(m.MedicalCertificate, [dict(medical_id=uid(n)) for n in (1101, 1102, 1103, 1104)])
    subscriptions = [
        (2010, 80, 1010, 30, 1001, False, 1101), (2011, 80, 1011, 30, 1001, False, None),
        (2012, 80, 1011, 31, 1001, False, 1102), (2013, 80, 1012, 31, 1001, False, 1103),
        (2014, 90, 1013, 30, 1003, False, 1104), (2015, 80, 1015, 30, 1002, False, None),
        (2016, 80, 1016, 30, None, False, None), (2017, 80, 1010, 30, 1001, True, None),
        (2018, 80, 1017, 30, 1001, True, None), (2019, 80, 1018, 31, 1001, False, None),
        (2021, 80, 1011, 31, 1002, False, None), (2022, 80, 1015, 31, 1001, False, None),
    ]
    insert(m.Subscription, [dict(subscription_id=uid(n), sport_association_id=uid(a), associate_id=uid(member),
        user_id=uid(u), group_id=uid(g) if g else None, deleted=deleted, medical_id=uid(medical) if medical else None)
        for n, a, member, u, g, deleted, medical in subscriptions])
    invoices = [(4010, 80, 1001, False), (4011, 80, 1001, False), (4012, 80, 1001, False),
                (4013, 90, 1003, False), (4014, 80, 1001, False), (4015, 80, 1002, False),
                (4016, 80, 1001, False), (4017, 80, 1001, True)]
    insert(m.Invoice, [dict(invoice_id=uid(n), sport_association_id=uid(a), group_id=uid(g),
                           membership_fee=0, activity_fee=0, deleted=deleted) for n, a, g, deleted in invoices])
    payments = [(3010, 80, 30, None, 1001, False, 4010), (3011, 80, 31, 1011, 1001, False, 4011),
                (3012, 80, 31, 1012, 1001, False, 4012), (3013, 90, 30, 1013, 1003, False, 4013),
                (3014, 80, 30, None, 1001, True, 4014), (3015, 80, 30, None, 1002, False, 4015),
                (3016, 80, 31, 1011, 1002, False, 4016), (3017, 80, 30, None, 1001, False, 4017)]
    insert(m.Payment, [dict(payment_id=uid(n), sport_association_id=uid(a), user_id=uid(u),
        associate_id=uid(member) if member else None, group_id=uid(g), deleted=deleted, invoice_id=uid(invoice))
        for n, a, u, member, g, deleted, invoice in payments])
    courses = [(5010, 80, 1001, False), (5011, 80, 1002, False), (5012, 80, 1001, True), (5013, 90, 1003, False)]
    insert(m.Course, [dict(course_id=uid(n), sport_association_id=uid(a), group_id=uid(g), deleted=deleted)
                      for n, a, g, deleted in courses])
    course_subscriptions = [(6010, 2010, 5010, False), (6011, 2012, 5010, False), (6012, 2013, 5010, False),
        (6013, 2014, 5013, False), (6014, 2010, 5013, False), (6015, 2014, 5010, False),
        (6016, 2010, 5012, False), (6017, 2010, 5011, False), (6018, 2010, 5010, True), (6019, 2015, 5010, False)]
    insert(m.CourseSubscription, [dict(course_subscription_id=uid(n), subscription_id=uid(s), course_id=uid(c), deleted=d)
                                  for n, s, c, d in course_subscriptions])
    installments = [(7010, 6010, 1001, False), (7011, 6011, 1001, False), (7012, 6012, 1001, False),
        (7013, 6013, 1003, False), (7014, 6014, 1001, False), (7015, 6018, 1001, False),
        (7016, 6010, 1002, False), (7017, 6010, 1001, True), (7018, 6019, 1001, False)]
    insert(m.CourseSubscriptionInstallment, [dict(course_subscription_installment_id=uid(n), course_subscription_id=uid(c),
        group_id=uid(g), deleted=d, payment_date=timezone.now()) for n, c, g, d in installments])
    insert(m.Carnet, [dict(carnet_id=uid(n), sport_association_id=uid(a)) for n, a in [(8010, 80), (8011, 90)]])
    carnets = [(8110, 2010, 8010), (8111, 2012, 8010), (8112, 2013, 8010), (8113, 2014, 8011),
               (8114, 2010, 8011), (8115, 2014, 8010), (8116, 2015, 8010), (8117, 2017, 8010)]
    insert(CarnetSubscription, [dict(carnet_subscription_id=uid(n), subscription_id=uid(s), carnet_id_id=uid(c))
                                for n, s, c in carnets])
    insert(m.AttendanceRegistry, [dict(attendance_registry_id=uid(n), course_id=uid(c))
                                 for n, c in [(9010, 5010), (9011, 5011), (9012, 5012), (9013, 5013)]])
    insert(m.AttendanceDay, [dict(attendance_day_id=uid(n), attendance_registry_id=uid(c), date=timezone.now())
                            for n, c in [(9110, 9010), (9111, 9011), (9112, 9012), (9113, 9013)]])
    insert(m.Tags, [dict(tag_id=uid(n), sport_association_id=uid(a), tag_name=f'Tag {n}')
                    for n, a in [(9210, 80), (9211, 90)] + [(n, 80) for n in range(10000, 11025)]])
    insert(Document, [dict(document_id=uid(9300))])
    files = [(9310, 2010), (9311, 2012), (9312, 2013), (9313, 2014), (9314, 2015), (9315, 2017)]
    insert(SubscriptionFile, [dict(subscription_file_id=uid(n), subscription_id=uid(s), document_id=uid(9300)) for n, s in files])
    association = m.SportAssociation.objects.get(pk=uid(80))
    athlete = m.User.objects.get(pk=uid(30))
    staff = m.User.objects.get(pk=uid(11))
    # kind, model, all candidates, unrestricted staff, athlete, group-1001 athlete,
    # group-1001 staff. Related FK joins do not inherit related model managers.
    specifications = [
        ('sport_association', m.SportAssociation, [80, 90], [80], [80], [80], [80]),
        ('associate', m.Associate, [r[0] for r in members], [1010,1011,1012,1015,1016,1018,1019], [1010,1011,1015,1016,1018], [1010,1011,1018], [1010,1011,1012,1018,1019]),
        ('subscription', m.Subscription, [r[0] for r in subscriptions], [2010,2011,2012,2013,2015,2016,2017,2018,2019,2021,2022], [2010,2011,2012,2015,2016,2017,2018,2019,2021,2022], [2010,2011,2012,2017,2018,2019], [2010,2011,2012,2013,2017,2018,2019,2022]),
        ('payment', m.Payment, [r[0] for r in payments], [3010,3011,3012,3015,3016,3017], [3010,3011,3015,3016,3017], [3010,3011,3017], [3010,3011,3012,3017]),
        ('invoice', m.Invoice, [r[0] for r in invoices], [4010,4011,4012,4014,4015,4016], [4010,4011,4015,4016], [4010,4011], [4010,4011,4012,4014,4016]),
        ('course', m.Course, [r[0] for r in courses], [5010,5011], [5010,5011], [5010], [5010]),
        ('course_subscription', m.CourseSubscription, [r[0] for r in course_subscriptions], [6010,6011,6012,6016,6017,6019], [6010,6011,6016,6017,6019], [6010,6011,6016,6017], [6010,6011,6012,6016,6017,6019]),
        ('course_subscription_installment', m.CourseSubscriptionInstallment, [r[0] for r in installments], [7010,7011,7012,7014,7015,7016,7018], [7010,7011,7014,7015,7016,7018], [7010,7011,7014,7015], [7010,7011,7012,7014,7015,7018]),
        ('carnet', m.Carnet, [8010,8011], [8010], [8010], [8010], [8010]),
        ('carnet_subscription', CarnetSubscription, [r[0] for r in carnets], [8110,8111,8112,8116,8117], [8110,8111,8116,8117], [8110,8111,8117], [8110,8111,8112,8116,8117]),
        ('attendance_registry', m.AttendanceRegistry, [9010,9011,9012,9013], [9010,9011,9012], [9010,9011,9012], [9010,9011,9012], [9010,9011,9012]),
        ('attendance_day', m.AttendanceDay, [9110,9111,9112,9113], [9110,9111,9112], [9110,9111,9112], [9110,9111,9112], [9110,9111,9112]),
        ('tag', m.Tags, [9210,9211], [9210], [9210], [9210], [9210]),
        ('group', m.Group, [1001,1002,1003], [1001,1002], [1001,1002], [1001,1002], [1001,1002]),
        ('subscription_file', SubscriptionFile, [r[0] for r in files], [9310,9311,9312,9314,9315], [9310,9311,9314,9315], [9310,9311,9315], [9310,9311,9312,9314,9315]),
        ('medical', m.Subscription, [1101,1102,1103,1104], [1101,1102,1103], [1101,1102], [1101,1102], [1101,1102,1103]),
    ]
    cases = []
    try:
        for kind, model, candidates, staff_ids, athlete_ids, group_athlete_ids, group_staff_ids in specifications:
            for label, person, group, expected in [('staff', staff, None, staff_ids), ('athlete', athlete, None, athlete_ids),
                    ('athlete-group', athlete, 1001, group_athlete_ids), ('staff-group', staff, 1001, group_staff_ids)]:
                _local.group_id = uid(group) if group else None
                scope = SimpleNamespace(impersonation_association=association, effective_user=person)
                ids = [uid(n) for n in candidates + [999999]]
                with CaptureQueriesContext(connection) as queries:
                    query = scoped_queryset(scope, model.objects)
                    if kind == 'medical':
                        actual = sorted(set(str(n) for n in query.filter(medical_id__in=ids).values_list('medical_id', flat=True)))
                    else:
                        actual = sorted(str(n) for n in query.filter(pk__in=ids).values_list('pk', flat=True))
                want = sorted(uid(n) for n in expected)
                assert actual == want, f'Django {kind}/{label}: {actual!r} != {want!r}'
                assert len(queries) == 1, f'Django scope query unexpectedly expanded: {kind}/{label}'
                cases.append(dict(name=f'{kind}/{label}', kind=kind, ids=ids, want=want,
                    scope=dict(AssociationID=uid(80), EffectiveID=str(person.pk), Role=person.role, GroupID=uid(group) if group else '')))
        # A forged foreign group narrows group-aware reads; it cannot override
        # the tenant predicate. Group-insensitive resources keep their own scope.
        for kind, model in [('subscription', m.Subscription), ('payment', m.Payment), ('course', m.Course)]:
            _local.group_id = uid(1003)
            scope = SimpleNamespace(impersonation_association=association, effective_user=athlete)
            assert not scoped_queryset(scope, model.objects).exists()
            candidates = next(row[2] for row in specifications if row[0] == kind)
            cases.append(dict(name=f'{kind}/foreign-group', kind=kind, ids=[uid(n) for n in candidates], want=[],
                scope=dict(AssociationID=uid(80), EffectiveID=uid(30), Role=2, GroupID=uid(1003))))
        _local.group_id = uid(1001)
        scope = SimpleNamespace(impersonation_association=association, effective_user=athlete)
        with transaction.atomic():
            assert scoped_queryset(scope, m.Subscription.objects).filter(pk=uid(2012)).exists()
            assert scoped_queryset(scope, m.Invoice.objects).filter(pk=uid(4011)).exists()
            assert m.Payment.objects.filter(pk=uid(3011)).update(deleted=True) == 1
            assert not scoped_queryset(scope, m.Invoice.objects).filter(pk=uid(4011)).exists()
            assert m.Subscription.objects.filter(pk=uid(2011)).update(user_id=uid(31)) == 1
            assert not scoped_queryset(scope, m.Subscription.objects).filter(pk=uid(2012)).exists()
            assert not scoped_queryset(scope, m.Associate.objects).filter(pk=uid(1011)).exists()
            transaction.set_rollback(True)
    finally:
        _local.group_id = None
    path = Path(work) / 'scoped-resources.json'
    path.write_text(json.dumps(cases)); path.chmod(0o600)
    print(f'Django: {len(cases)} explicit association/family/group resource visibility contracts passed', flush=True)
    snapshot_models = {model for _, model, *_ in specifications} | {m.MedicalCertificate, Document}
    return path, len(cases), snapshot_models
