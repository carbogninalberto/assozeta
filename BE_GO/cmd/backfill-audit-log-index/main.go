// backfill-audit-log-index is the Go port of Django's backfill_audit_log_index
// management command (BE/application/management/commands/backfill_audit_log_index.py):
// it creates the AuditLogIndex rows missing for existing LogEntry records,
// resolving each entry like the post_save index signal without request
// context. Options: --batch-size N, --dry-run, --content-type app_label.model,
// --limit N (each value as "--name value" or "--name=value") and --env-file
// PATH. Django's global management options and argparse prefix abbreviations
// are not accepted.
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"regexp"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

func main() { os.Exit(run(os.Args[1:])) }

const usage = "usage: backfill-audit-log-index [--batch-size BATCH_SIZE] [--dry-run] [--content-type CONTENT_TYPE] [--limit LIMIT] [--env-file PATH]"

// argparse treats a value that looks like a negative number as a value.
var negativeNumber = regexp.MustCompile(`^-\.?\d`)

func parse(args []string) (service.AuditIndexBackfillOptions, string, error) {
	options := service.AuditIndexBackfillOptions{BatchSize: 500}
	envFile := ""
	for index := 0; index < len(args); index++ {
		name, value, inline := strings.Cut(args[index], "=")
		switch name {
		case "--dry-run":
			if inline {
				return options, "", fmt.Errorf("argument --dry-run: ignored explicit argument '%s'", value)
			}
			options.DryRun = true
			continue
		case "--batch-size", "--limit", "--content-type", "--env-file":
		default:
			return options, "", fmt.Errorf("unrecognized arguments: %s", args[index])
		}
		if !inline {
			if index+1 >= len(args) || strings.HasPrefix(args[index+1], "-") && !negativeNumber.MatchString(args[index+1]) {
				return options, "", fmt.Errorf("argument %s: expected one argument", name)
			}
			index++
			value = args[index]
		}
		switch name {
		case "--content-type":
			options.ContentType = value
		case "--env-file":
			envFile = value
		default:
			number, ok := dto.PythonIntText(value)
			if !ok || !number.IsInt64() {
				return options, "", fmt.Errorf("argument %s: invalid int value: '%s'", name, value)
			}
			if name == "--batch-size" {
				options.BatchSize = number.Int64()
			} else {
				options.Limit = number.Int64()
			}
		}
	}
	return options, envFile, nil
}

func run(args []string) int {
	options, envFile, err := parse(args)
	if err != nil {
		fmt.Fprintln(os.Stderr, usage)
		fmt.Fprintln(os.Stderr, "backfill-audit-log-index: error: "+err.Error())
		return 2
	}
	cfg, err := config.Load(envFile)
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid environment configuration")
		return 1
	}
	database, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		fmt.Fprintln(os.Stderr, "invalid database configuration")
		return 1
	}
	database.DefaultQueryExecMode = pgx.QueryExecModeExec
	database.ConnectTimeout = 5 * time.Second
	database.RuntimeParams["application_name"] = "assozeta-go-backfill-audit-log-index"
	db := sqlx.NewDb(ownedpgx.OpenDB(*database), "pgx")
	defer db.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	err = service.RunAuditIndexBackfill(ctx, repository.NewAuditIndexBackfill(db), options, time.Now, os.Stdout)
	if err != nil {
		// Django ends with a traceback; database errors may contain private values.
		if err == service.ErrAuditIndexBackfillNegativeLimit || err == service.ErrAuditIndexBackfillZeroBatch {
			fmt.Fprintln(os.Stderr, "backfill-audit-log-index failed: "+err.Error())
		} else {
			fmt.Fprintln(os.Stderr, "backfill-audit-log-index failed")
		}
		return 1
	}
	return 0
}
