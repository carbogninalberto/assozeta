"""A minimal source tree in the formats the handoff inventory discovers.

`build(root)` writes one baseline item of every gated kind, in the same syntax
as the real sources (Django URLconf/routing, Celery tasks and Beat, echo routes,
the Go periodic registry, ticker loops, producers, Compose, Caddy, Go commands).
`ADDITIONS` introduces exactly one new item each; its distinctive name must
appear in the gate's `unclassified` line.
"""
import json
from pathlib import Path
import textwrap

FILES = {
    'BE/core/__init__.py': '',
    'BE/core/settings.py': 'INSTALLED_APPS = ["probe"]\n',
    'BE/core/celery.py': '''\
        from celery import Celery
        from celery.schedules import crontab

        app = Celery('core')
        app.conf.beat_schedule = {
            'handoff-base-beat': {'task': 'handoff_base_task', 'schedule': 60.0},
            'handoff-base-cron': {
                'task': 'probe.handoff_base_named',
                'schedule': crontab(hour='1', minute='5'),
                'args': ()
            },
        }
        ''',
    'BE/core/urls.py': '''\
        from django.urls import include, path
        from probe import views

        urlpatterns = [
            path('handoff-base/', views.base),
            path('probe/', include('probe.urls')),
        ]
        ''',
    'BE/probe/__init__.py': '',
    'BE/probe/urls.py': '''\
        from django.urls import path
        from probe import views

        urlpatterns = [
            path('handoff-base-nested/', views.base),
        ]
        ''',
    'BE/probe/routing.py': '''\
        from django.urls import re_path
        from probe import consumers

        websocket_urlpatterns = [
            re_path(r'ws/handoff-base-socket/$', consumers.Base.as_asgi()),
        ]
        ''',
    'BE/probe/consumers.py': '''\
        class Base:
            pass
        ''',
    'BE/probe/tasks.py': '''\
        from celery import shared_task


        @shared_task
        def handoff_base_task():
            return None


        @shared_task(bind=True, name='probe.handoff_base_named')
        def handoff_base_retrying(self):
            raise self.retry(countdown=5)
        ''',
    'BE/probe/views.py': '''\
        from probe.tasks import handoff_base_task


        def base(request):
            handoff_base_task.delay()
        ''',
    'BE_GO/cmd/server/main.go': '''\
        package main

        func main() {
        	e := echo.New()
        	e.GET("/handoff-base-go", baseHandler)
        	group := e.Group("/handoff-base-group")
        	group.POST("/item", baseHandler)
        	e.Any("/*", unmatched)
        }
        ''',
    'BE_GO/cmd/worker/main.go': '''\
        package main

        func main() {}
        ''',
    'BE_GO/internal/domain/names.go': '''\
        package domain

        const JobHandoffBase = "handoff_base_task"
        ''',
    'BE_GO/internal/domain/schedule.go': '''\
        package domain

        var periodicJobKinds = map[string]string{
        	JobHandoffBase: JobHandoffBase,
        }
        ''',
    'BE_GO/internal/service/probe.go': '''\
        package service

        func (s *Probe) handoffBasePublisher(ctx context.Context) error {
        	_, err := s.publisher.Publish(ctx, celery.Task{Name: "handoff_base_task"})
        	return err
        }
        ''',
    'BE_GO/internal/probe/loop.go': '''\
        package probe

        func handoffBaseLoop(ctx context.Context) {
        	ticker := time.NewTicker(10 * time.Second)
        	defer ticker.Stop()
        }
        ''',
    'BE_GO/internal/probe/loop_test.go': '''\
        package probe

        func TestIgnored(t *testing.T) {
        	ticker := time.NewTicker(time.Second)
        	defer ticker.Stop()
        }
        ''',
    'selfhost/compose.yml': '''\
        name: handoff-synthetic
        services:
          api:
            image: ghcr.io/carbogninalberto/assozeta-backend:test
          worker:
            image: ghcr.io/carbogninalberto/assozeta-backend:test
            command: ["celery", "-A", "core", "worker", "-Q", "celery"]
          api-go:
            image: ghcr.io/carbogninalberto/assozeta-backend-go:test
          migrate:
            image: ghcr.io/carbogninalberto/assozeta-backend:test
            profiles: [tools]
            command: ["python", "manage.py", "migrate"]
          web:
            image: ghcr.io/carbogninalberto/assozeta-web:test
            depends_on:
              api:
                condition: service_healthy
        ''',
    'selfhost/compose.updater.yml': '''\
        name: handoff-synthetic-updater
        services:
          updater:
            image: ghcr.io/carbogninalberto/assozeta-updater:test
        ''',
    'selfhost/caddy/Caddyfile': '''\
        {$SITE_ADDRESS} {
            route {
                handle /healthz {
                    respond 200
                }

                handle /handoff-base-caddy/* {
                    reverse_proxy api:8000
                }

                handle_path /api/v2/* {
                    reverse_proxy api-go:8000
                }

                handle_path /api/* {
                    reverse_proxy api:8000
                }
            }
        }
        ''',
    'selfhost/bin/assozeta': '''\
        #!/bin/sh
        set -eu
        prod_compose() { docker compose "$@"; }
        migrate_prod_schemas() {
            prod_compose --profile tools run --rm --no-deps migrate || return $?
        }
        ''',
    'docs/architecture/go-migration/ledger.json': json.dumps({'format': 1, 'scope': 'synthetic', 'entries': []}),
    'docs/architecture/go-migration/go-tasks-inventory.json': json.dumps([
        {'name': 'handoff_base_task', 'django_site': 'BE/probe/tasks.py:4', 'scheduled': True,
         'consumer': True, 'job': True, 'routed': False, 'harness': []},
        {'name': 'probe.handoff_base_named', 'django_site': 'BE/probe/tasks.py:9', 'scheduled': True,
         'consumer': False, 'job': False, 'routed': False, 'harness': []},
    ], indent=2),
}

# Distinctive baseline names discovery must report, per kind.
BASELINE = {
    'route': ('handoff-base/', 'handoff-base-nested', 'handoff-base-socket', 'handoff-base-go',
              'handoff-base-group', 'handoff-base-caddy'),
    'task': ('handoff_base_task', 'probe.handoff_base_named'),
    'schedule': ('handoff-base-beat', 'handoff-base-cron', 'handoff_base_task', 'handoffBaseLoop'),
    'service': ('api-go', 'worker', 'migrate', 'updater'),
    'publisher': ('handoff_base_task.delay', 'self.retry', 'handoffBasePublisher'),
}
# Must never be discovered: test code is not a production source.
EXCLUDED = {'schedule': ('TestIgnored',)}


def _append(path, text, dedent=True):
    def apply(root):
        target = root / path
        target.write_text(target.read_text() + (textwrap.dedent(text) if dedent else text))
    return apply


def _replace(path, old, new):
    def apply(root):
        target = root / path
        content = target.read_text()
        assert old in content, (path, old)
        target.write_text(content.replace(old, new, 1))
    return apply


def _create(path, text):
    def apply(root):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(text))
    return apply


# (kind, source) -> (mutation, names any one of which identifies the new item)
ADDITIONS = {
    ('route', 'django-urlconf'): (_replace('BE/core/urls.py', "    path('probe/'",
                                           "    path('handoff-new-route/', views.base),\n    path('probe/'"),
                                  ('handoff-new-route',)),
    ('route', 'django-websocket'): (_replace('BE/probe/routing.py', '    re_path(',
                                             "    re_path(r'ws/handoff-new-socket/$', consumers.Base.as_asgi()),\n    re_path("),
                                    ('handoff-new-socket',)),
    ('route', 'go-router'): (_replace('BE_GO/cmd/server/main.go', '\te.Any(',
                                      '\te.POST("/handoff-new-go-route", baseHandler)\n\te.Any('),
                             ('handoff-new-go-route',)),
    ('route', 'caddy'): (_replace('selfhost/caddy/Caddyfile', '        handle_path /api/v2/* {',
                                  '        handle /handoff-new-caddy-route/* {\n'
                                  '            reverse_proxy api-go:8000\n        }\n\n'
                                  '        handle_path /api/v2/* {'),
                         ('handoff-new-caddy-route',)),
    ('task', 'django-registry'): (_append('BE/probe/tasks.py', '''

        @shared_task
        def handoff_new_task():
            return None
        '''), ('handoff_new_task',)),
    ('schedule', 'beat'): (_replace('BE/core/celery.py', "    'handoff-base-cron': {",
                                    "    'handoff-new-beat': {'task': 'handoff_base_task', 'schedule': 30.0},\n"
                                    "    'handoff-base-cron': {"),
                           ('handoff-new-beat',)),
    ('schedule', 'go-periodic'): (lambda root: (
        _append('BE_GO/internal/domain/names.go', 'const JobHandoffNewPeriodic = "handoff_new_periodic"\n')(root),
        _replace('BE_GO/internal/domain/schedule.go', '\tJobHandoffBase: JobHandoffBase,',
                 '\tJobHandoffBase: JobHandoffBase,\n\tJobHandoffNewPeriodic: JobHandoffNewPeriodic,')(root)),
        ('handoff_new_periodic', 'JobHandoffNewPeriodic')),
    ('schedule', 'go-loop'): (_append('BE_GO/internal/probe/loop.go', '''
        func handoffNewLoop(ctx context.Context) {
        \tfor range time.Tick(time.Minute) {
        \t}
        }
        '''), ('handoffNewLoop',)),
    ('service', 'compose'): (_append('selfhost/compose.yml',
                                     '  handoff-new-service:\n'
                                     '    image: ghcr.io/carbogninalberto/assozeta-backend-go:test\n'
                                     '    entrypoint: ["/app/scheduler"]\n', dedent=False),
                             ('handoff-new-service',)),
    ('service', 'go-command'): (_create('BE_GO/cmd/handoff-new-command/main.go', '''\
        package main

        func main() {}
        '''), ('handoff-new-command',)),
    ('publisher', 'python-delay'): (_append('BE/probe/views.py', '''

        def handoff_new_publisher(request):
            handoff_base_task.apply_async(countdown=5)
        '''), ('handoff_new_publisher',)),
    ('publisher', 'python-retry'): (_append('BE/probe/tasks.py', '''

        @shared_task(bind=True, name='probe.handoff_new_retrying')
        def handoff_new_retry(self):
            raise self.retry(countdown=60)
        '''), ('handoff_new_retry',)),
    ('publisher', 'go-broker'): (_append('BE_GO/internal/service/probe.go', '''
        func (s *Probe) handoffNewGoPublisher(ctx context.Context) error {
        \t_, err := s.tasks.Publish(ctx, celery.Task{Name: "handoff_base_task"})
        \treturn err
        }
        '''), ('handoffNewGoPublisher',)),
    ('publisher', 'go-durable-job'): (_append('BE_GO/internal/service/probe.go', '''
        func (s *Probe) handoffNewEnqueue(ctx context.Context, tx *sqlx.Tx) error {
        \t_, err := s.jobs.Enqueue(ctx, tx, domain.JobIntent{Kind: "handoff_base_task"})
        \treturn err
        }
        '''), ('handoffNewEnqueue',)),
}


def build(root):
    root = Path(root)
    for name, content in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content))
    (root / 'selfhost/bin/assozeta').chmod(0o755)
    return root
