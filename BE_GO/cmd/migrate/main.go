// This explicit command applies additive Go operational migrations only. The
// installing lifecycle must hold the existing exclusive operations lock before
// invoking it and before resuming application/background services. Managed
// updates take their backup first. HTTP/worker startup
// never invokes migrations. Binary rollback retains durable operational intent.
package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		slog.Error(err.Error())
		os.Exit(1)
	}
}

func run(arguments []string) error {
	flags := flag.NewFlagSet("migrate", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	envFile := flags.String("env-file", "", "optional existing deployment .env")
	if err := flags.Parse(arguments); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			fmt.Println("Go operational migrations: --env-file PATH. Forward only; caller must hold the exclusive deployment operations lock throughout migration and before application/background admission. Managed updates take their backup first. Does not migrate Django, change runtime ownership, or start workers.")
			return nil
		}
		return errors.New("invalid migration arguments")
	}
	if flags.NArg() != 0 {
		return errors.New("unexpected migration arguments")
	}
	cfg, err := config.Load(*envFile)
	if err != nil {
		return errors.New("invalid migration environment configuration")
	}
	// Migration session advisory locks require a direct PostgreSQL connection;
	// the ordinary DatabaseURL may select a transaction-mode PgBouncer endpoint.
	if err = migrations.Apply(cfg.MigrationDatabaseURL()); err != nil {
		// PostgreSQL/migration errors may contain SQL, credentials or private data.
		return errors.New("Go operational migration failed")
	}
	slog.Info("Go operational migrations applied")
	return nil
}
