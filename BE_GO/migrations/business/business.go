// Package business applies the PostgreSQL form of the pinned Django migrations.
// The deployment lifecycle calls it through cmd/migrate under its operations lock.
package business

import (
	"context"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

//go:embed *.sql plan.json data/*.sql
var files embed.FS

const dataMarker = "\n-- django-data: "

// Business migrations share Django's history with manage.py. This separate
// advisory lock serializes Go invocations even outside the deployment CLI.
const advisoryLock = 0x415a425553494e45

type migration struct {
	App      string
	Name     string
	SQL      string
	Replaces [][2]string
}

func (m migration) key() [2]string { return [2]string{m.App, m.Name} }

// Apply runs unapplied migrations and their history writes in the same
// transactions, then reconciles Django's post_migrate content types/permissions.
func Apply(ctx context.Context, cfg config.Config) error {
	planSQL, err := files.ReadFile("plan.json")
	if err != nil {
		return errors.New("business migration plan unavailable")
	}
	var plan []migration
	if err = json.Unmarshal(planSQL, &plan); err != nil {
		return errors.New("invalid business migration plan")
	}

	db, err := pgx.ParseConfig(cfg.MigrationDatabaseURL())
	if err != nil {
		return errors.New("invalid business migration database configuration")
	}
	db.ConnectTimeout = 10 * time.Second
	db.RuntimeParams["search_path"] = "public"
	db.RuntimeParams["timezone"] = "UTC"
	db.RuntimeParams["statement_timeout"] = "300000"
	db.RuntimeParams["lock_timeout"] = "30000"
	conn, err := pgx.ConnectConfig(ctx, db)
	if err != nil {
		return publicError("database connection", err)
	}
	defer conn.Close(context.Background())

	if _, err = conn.Exec(ctx, "SELECT pg_advisory_lock($1)", int64(advisoryLock)); err != nil {
		return publicError("advisory lock", err)
	}
	if err = executeSQL(ctx, conn, "000100_django_migrations.up.sql"); err != nil {
		return publicError("Django history initialization", err)
	}
	rows, err := conn.Query(ctx, "SELECT app, name FROM django_migrations")
	if err != nil {
		return publicError("Django history lookup", err)
	}
	applied := make(map[[2]string]bool)
	for rows.Next() {
		var key [2]string
		if err = rows.Scan(&key[0], &key[1]); err != nil {
			rows.Close()
			return publicError("Django history lookup", err)
		}
		applied[key] = true
	}
	err = rows.Err()
	if err != nil {
		return publicError("Django history lookup", err)
	}

	for _, step := range plan {
		var recorded int
		for _, key := range step.Replaces {
			if applied[key] {
				recorded++
			}
		}
		if recorded != 0 && recorded != len(step.Replaces) {
			return fmt.Errorf("business migration %s.%s has partially applied Django replacements", step.App, step.Name)
		}
		if applied[step.key()] && recorded != len(step.Replaces) {
			return fmt.Errorf("business migration %s.%s is missing its Django replacements", step.App, step.Name)
		}
	}
	for _, step := range plan {
		if applied[step.key()] {
			continue
		}
		if err := applyMigration(ctx, conn, cfg.Storage, step, applied); err != nil {
			return publicError(step.App+"."+step.Name, err)
		}
	}

	if err := pgx.BeginFunc(ctx, conn, func(tx pgx.Tx) error {
		return executeSQL(ctx, tx, "post_migrate.sql")
	}); err != nil {
		return publicError("Django content types and permissions", err)
	}
	return nil
}

func applyMigration(ctx context.Context, conn *pgx.Conn, storage config.StorageSettings, step migration, applied map[[2]string]bool) error {
	return pgx.BeginFunc(ctx, conn, func(tx pgx.Tx) error {
		if len(step.Replaces) == 0 || !applied[step.Replaces[0]] {
			if err := runMigrationSQL(ctx, tx, storage, step.SQL); err != nil {
				return err
			}
			for _, key := range step.Replaces {
				if err := record(ctx, tx, key); err != nil {
					return err
				}
			}
		}
		return record(ctx, tx, step.key())
	})
}

func record(ctx context.Context, tx pgx.Tx, key [2]string) error {
	_, err := tx.Exec(ctx, "INSERT INTO django_migrations (app, name, applied) VALUES ($1, $2, statement_timestamp())", key[0], key[1])
	return err
}

func runMigrationSQL(ctx context.Context, tx pgx.Tx, storage config.StorageSettings, path string) error {
	body, err := files.ReadFile(path)
	if err != nil {
		return err
	}
	parts := strings.Split(string(body), dataMarker)
	if _, err = tx.Exec(ctx, parts[0]); err != nil {
		return err
	}
	for _, part := range parts[1:] {
		name, sql, _ := strings.Cut(part, "\n")
		if name == "application.migrate_subscription_signature_compatibility" {
			err = migrateSubscriptionSignatures(ctx, tx, storage)
		} else {
			err = executeSQL(ctx, tx, "data/"+name+".sql")
		}
		if err != nil {
			return err
		}
		if sql != "" {
			if _, err = tx.Exec(ctx, sql); err != nil {
				return err
			}
		}
	}
	return nil
}

type sqlExecutor interface {
	Exec(context.Context, string, ...any) (pgconn.CommandTag, error)
}

func executeSQL(ctx context.Context, db sqlExecutor, path string) error {
	body, err := files.ReadFile(path)
	if err != nil {
		return err
	}
	_, err = db.Exec(ctx, string(body))
	return err
}

func publicError(operation string, err error) error {
	// Database and storage errors may contain private values. Only surface the
	// migration name and PostgreSQL's stable error code.
	var pgError *pgconn.PgError
	if errors.As(err, &pgError) {
		return fmt.Errorf("business schema %s failed (SQLSTATE %s)", operation, pgError.Code)
	}
	return fmt.Errorf("business schema %s failed", operation)
}
