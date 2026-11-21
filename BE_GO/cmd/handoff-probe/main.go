//go:build handoff

// Command handoff-probe is a Phase 3 test helper (TESTS/handoff/p3_stack.py),
// never built into an image: only -tags handoff builds contain it. It is a Go
// durable-job producer that records one job through the production
// repository.Jobs.Enqueue in its own transaction, exactly as the scheduler's
// occurrence publisher does, and prints one JSON line:
//
//	{"outcome":"enqueued","job":{...}}  the transaction committed
//	{"outcome":"error","error":"..."}   Enqueue or the commit failed (rolled back)
//
// The handoff contract requires Enqueue to apply durable admission inside the
// caller's transaction, so a stale producer is refused with an error whose text
// contains "handoff admission rejected".
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type jobView struct {
	ID                  string `json:"id"`
	State               string `json:"state"`
	Kind                string `json:"kind"`
	Attempts            int    `json:"attempts"`
	OwnershipGeneration int64  `json:"ownership_generation"`
	AvailableAt         string `json:"available_at"`
	CreatedAt           string `json:"created_at"`
}

func main() {
	result := map[string]any{}
	if err := run(os.Args[1:], result); err != nil {
		result["outcome"], result["error"] = "error", err.Error()
	}
	_ = json.NewEncoder(os.Stdout).Encode(result)
}

func run(arguments []string, result map[string]any) error {
	if len(arguments) == 0 || arguments[0] != "enqueue" {
		return errors.New("usage: handoff-probe enqueue --dsn DSN --id UUID --kind KIND --key KEY [--available-at RFC3339]")
	}
	flags := flag.NewFlagSet("handoff-probe", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	dsn := flags.String("dsn", "", "private test database")
	id := flags.String("id", "", "job id")
	kind := flags.String("kind", "", "job kind")
	key := flags.String("key", "", "idempotency key")
	availableAt := flags.String("available-at", "", "RFC 3339 due time")
	association := flags.String("association", uuid.Nil.String(), "association id (nil: instance-wide)")
	maxAttempts := flags.Int("max-attempts", 3, "attempt budget")
	payload := flags.String("payload", "{}", "JSON payload")
	if err := flags.Parse(arguments[1:]); err != nil {
		return err
	}
	jobID, err := uuid.Parse(*id)
	if err != nil {
		return errors.New("invalid --id")
	}
	associationID, err := uuid.Parse(*association)
	if err != nil {
		return errors.New("invalid --association")
	}
	intent := domain.JobIntent{ID: jobID, AssociationID: associationID, Kind: *kind, Version: 1, Key: *key,
		Payload: json.RawMessage(*payload), MaxAttempts: *maxAttempts}
	if *availableAt != "" {
		if intent.AvailableAt, err = time.Parse(time.RFC3339Nano, *availableAt); err != nil {
			return errors.New("invalid --available-at")
		}
	}
	config, err := pgx.ParseConfig(*dsn)
	if err != nil {
		return errors.New("invalid --dsn")
	}
	config.DefaultQueryExecMode = pgx.QueryExecModeExec
	config.RuntimeParams["application_name"] = "handoff-go-producer"
	db := sqlx.NewDb(ownedpgx.OpenDB(*config), "pgx")
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	tx, err := db.BeginTxx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin: %w", err)
	}
	defer tx.Rollback()
	job, err := repository.NewJobs(db).Enqueue(ctx, tx, intent)
	if err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return fmt.Errorf("commit: %w", err)
	}
	result["outcome"] = "enqueued"
	result["job"] = jobView{ID: job.ID.String(), State: job.State, Kind: job.Kind, Attempts: job.Attempts,
		OwnershipGeneration: job.OwnershipGeneration, AvailableAt: job.AvailableAt.UTC().Format(time.RFC3339Nano),
		CreatedAt: job.CreatedAt.UTC().Format(time.RFC3339Nano)}
	return nil
}
