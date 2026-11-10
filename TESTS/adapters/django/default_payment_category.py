"""Payment.save()'s default payment category in the HTTP family fixtures.

Payment.save() takes the lowest-key category named 'entrate e proventi da attività
tipiche' (case-insensitive) among the payment's association categories and the global
ones. Families that reach that branch also seed a same-named category owned by the
fixture's other association, keyed below every uuid4 key, so the expected default is
the payment association's own or global row and never that one.
"""
NAME = 'entrate e proventi da attività tipiche'
OTHER_ASSOCIATION_CATEGORY = '00000000-0000-4000-8000-000000000501'
SOURCES = ('TESTS/adapters/django/default_payment_category.py',)


def seed_other_association_category(association, created):
    from application.models import PaymentCategory
    if not association:
        raise RuntimeError('GUARD: other association required for the default category fixture')
    PaymentCategory._base_manager.bulk_create([PaymentCategory(
        payment_category_id=OTHER_ASSOCIATION_CATEGORY, name='Entrate e proventi da attività tipiche',
        sport_association_id=association)])
    created.append((PaymentCategory, OTHER_ASSOCIATION_CATEGORY))


def default_category(association):
    """The row Payment.save() picks for a payment of `association` (None: global only)."""
    from django.db.models import Q
    from application.models import PaymentCategory
    return PaymentCategory.objects.filter(Q(sport_association_id=association) | Q(sport_association__isnull=True),
                                          name__iexact=NAME).order_by('pk').first()
