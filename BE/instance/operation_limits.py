"""Short owner-operation leases independent of Redis, which diagnostics may inspect."""
from django.db import DatabaseError, transaction
from django.utils import timezone
from .models import InstanceConfiguration


def claim_operation(name, seconds):
    if name not in ('diagnostics', 'email_test'):
        raise ValueError('Unknown operation')
    try:
        with transaction.atomic():
            config = InstanceConfiguration.objects.select_for_update(nowait=True).first()
            if not config:
                return False
            results = dict(config.diagnostic_results or {})
            leases = dict(results.get('operation_leases', {}))
            now = timezone.now().timestamp()
            if leases.get(name, 0) > now:
                return False
            leases[name] = now + seconds
            config.diagnostic_results = {**results, 'operation_leases': leases}
            config.save(update_fields=['diagnostic_results'])
        return True
    except DatabaseError:
        return False
