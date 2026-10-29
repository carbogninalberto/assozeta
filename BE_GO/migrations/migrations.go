// Package migrations owns only explicitly additive Go operational tables.
// It is never called by the HTTP server or a request handler.
package migrations

import (
	"embed"
	"errors"
	"time"

	"github.com/golang-migrate/migrate/v4"
	migratepgx "github.com/golang-migrate/migrate/v4/database/pgx/v5"
	"github.com/golang-migrate/migrate/v4/source/iofs"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
)

//go:embed *.sql
var files embed.FS

// CurrentVersion is the operational schema understood by this binary. Worker
// startup may check it without running Apply or changing migration history.
const CurrentVersion = 4

// Apply has no force/down operation: rolling back a binary retains job intent.
// The dedicated connection and migration history cannot affect Django's history.
// Callers must hold the deployment lifecycle lock before applying in an install.
func Apply(dsn string) error {
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		return errors.New("invalid operational migration database configuration")
	}
	cfg.ConnectTimeout = 10 * time.Second
	cfg.RuntimeParams["statement_timeout"] = "30000"
	db := stdlib.OpenDB(*cfg)
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	defer db.Close()
	source, err := iofs.New(files, ".")
	if err != nil {
		return err
	}
	driver, err := migratepgx.WithInstance(db, &migratepgx.Config{SchemaName: "public", MigrationsTable: "assozeta_go_schema_migrations", StatementTimeout: 30 * time.Second})
	if err != nil {
		_ = source.Close()
		return err
	}
	runner, err := migrate.NewWithInstance("iofs", source, "pgx5", driver)
	if err != nil {
		_ = source.Close()
		_ = driver.Close()
		return err
	}
	defer runner.Close()
	runner.LockTimeout = 10 * time.Second
	err = runner.Up()
	if errors.Is(err, migrate.ErrNoChange) {
		return nil
	}
	return err
}
