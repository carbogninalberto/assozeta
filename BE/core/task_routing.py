"""Opt-in routing of selected Celery task names to the Go consumer's queue.

GO_TASK_QUEUE_TASKS is a comma-separated list of registered task names
(for example ``core.tasks.send_mail_async``). Empty (the default) returns
None: no ``task_routes`` setting is defined and every task keeps going to
the default ``celery`` queue exactly as before. The Go producer reads the
same variable (``BE_GO/internal/celery/routes.go``), so both producers send a
routed name to the same queue. See docs/architecture/go-migration/celery-consumer.md.
"""

GO_TASK_QUEUE = 'assozeta-go'


def go_task_names(value):
    return [name.strip() for name in (value or '').split(',') if name.strip()]


def go_task_routes(value):
    names = go_task_names(value)
    if not names:
        return None
    return {name: {'queue': GO_TASK_QUEUE} for name in names}
