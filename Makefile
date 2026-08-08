SHELL := /bin/sh

ASSOZETA := ./selfhost/bin/assozeta

.PHONY: help dev-config dev dev-up dev-down dev-logs dev-shell dev-ui-shell dev-db-shell \
	dev-migrate dev-makemigrations dev-test dev-rebuild dev-reset \
	selfhost-configure selfhost-install selfhost-validate selfhost-start \
	selfhost-stop selfhost-status selfhost-logs selfhost-backup selfhost-restore \
	selfhost-upgrade selfhost-test selfhost-smoke selfhost-production-smoke

help:
	@printf '%s\n' \
		'make dev-config               Generate selfhost/.env.dev without starting Docker' \
		'make dev                      Run the complete development stack' \
		'make dev-up                   Start development detached' \
		'make dev-down                 Stop development and keep data' \
		'make dev-logs SERVICE=api     Follow development logs' \
		'make dev-shell                Open a backend shell' \
		'make dev-ui-shell             Open a UI shell' \
		'make dev-db-shell             Open psql' \
		'make dev-migrate              Run Django migrations' \
		'make dev-makemigrations       Create Django migrations' \
		'make dev-test                 Run backend tests then UI CSS verify' \
		'make dev-rebuild              Rebuild development images' \
		'make dev-reset CONFIRM=1      Delete all development data' \
		'make selfhost-install ARGS="--domain example.org --email admin@example.org"' \
		'make selfhost-status          Show production status' \
		'make selfhost-test            Validate scripts, Compose, and Python syntax' \
		'make selfhost-smoke           Start and smoke-test the development stack' \
		'make selfhost-production-smoke Build and test the production stack'

dev-config:
	@$(ASSOZETA) dev-config

dev:
	@$(ASSOZETA) dev

dev-up:
	@$(ASSOZETA) dev-up

dev-down:
	@$(ASSOZETA) dev-down

dev-logs:
	@$(ASSOZETA) dev-logs $(SERVICE)

dev-shell:
	@$(ASSOZETA) dev-compose exec api sh

dev-ui-shell:
	@$(ASSOZETA) dev-compose exec ui sh

dev-db-shell:
	@$(ASSOZETA) dev-compose exec postgres sh -lc 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

dev-migrate:
	@$(ASSOZETA) dev-compose run --rm api python manage.py migrate --noinput

dev-makemigrations:
	@$(ASSOZETA) dev-compose run --rm api python manage.py makemigrations

dev-test:
	@./run_tests.sh
	@$(ASSOZETA) dev-compose run --rm ui npm run css:verify

dev-rebuild:
	@$(ASSOZETA) dev-compose build --no-cache api ui renderer

dev-reset:
	@test "$(CONFIRM)" = "1" || { printf 'Run make dev-reset CONFIRM=1 to delete development data.\n' >&2; exit 1; }
	@$(ASSOZETA) dev-reset --yes

selfhost-configure:
	@$(ASSOZETA) configure $(ARGS)

selfhost-install:
	@$(ASSOZETA) install $(ARGS)

selfhost-validate:
	@$(ASSOZETA) validate

selfhost-start:
	@$(ASSOZETA) start

selfhost-stop:
	@$(ASSOZETA) stop

selfhost-status:
	@$(ASSOZETA) status

selfhost-logs:
	@$(ASSOZETA) logs $(SERVICE)

selfhost-backup:
	@$(ASSOZETA) backup $(DESTINATION)

selfhost-restore:
	@test -n "$(ARCHIVE)" || { printf 'ARCHIVE=/path/to/backup.tar.gz is required.\n' >&2; exit 1; }
	@$(ASSOZETA) restore "$(ARCHIVE)"

selfhost-upgrade:
	@test -n "$(VERSION)" || { printf 'VERSION is required.\n' >&2; exit 1; }
	@$(ASSOZETA) upgrade "$(VERSION)"

selfhost-test:
	@./selfhost/tests/validate.sh

selfhost-smoke:
	@./selfhost/tests/smoke.sh

selfhost-production-smoke:
	@docker build --target production --build-arg VERSION=test -t assozeta-backend:test ./BE
	@docker build --target production -t assozeta-web:test ./UI
	@docker build -t assozeta-renderer:test ./selfhost/renderer
	@./selfhost/tests/production-smoke.sh
