//go:build queryobservationintegration

package queryobservation

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/google/uuid"
)

// Exercise the same database/sql per-query acquisitions used by HTTP repositories,
// including context attribution when eight requests share three physical connections.
func TestObservedDriverPostgreSQLRequests(t *testing.T) {
	cfg := pgFixture(t)
	var output bytes.Buffer
	observer, err := New(&output, uuid.NewString())
	if err != nil {
		t.Fatal(err)
	}
	cfg.Tracer = observer
	db := database.OpenObservedDB(*cfg, observer)
	db.SetMaxOpenConns(3)
	db.SetMaxIdleConns(3)
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	start := make(chan struct{})
	failures := make(chan error, 8)
	var wg sync.WaitGroup
	for range 8 {
		request, _, finish := observer.Begin(ctx)
		wg.Add(1)
		go func() {
			defer wg.Done()
			defer finish()
			<-start
			for i := 0; i < 3; i++ {
				var got int
				if err := db.QueryRowContext(request, "SELECT $1::int", i).Scan(&got); err != nil || got != i {
					failures <- fmt.Errorf("real SELECT failed or returned incorrect result")
					return
				}
			}
		}()
	}
	close(start)
	wg.Wait()
	close(failures)
	for err := range failures {
		t.Error(err)
	}
	if db.Stats().InUse != 0 {
		t.Fatal("driver work not drained")
	}
	if err := db.Close(); err != nil {
		t.Fatal("database close failed")
	}
	if err := observer.Close(); err != nil {
		t.Fatal("driver observation incomplete", err)
	}
	// Publish only generated IDs/counters inside the already validated private
	// fixture directory, for the independent Python consumer to reconcile.
	file, err := os.OpenFile(filepath.Join(cfg.Host, "driver-observations.jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		t.Fatal("cannot create private diagnostic receipt")
	}
	_, writeErr := file.Write(output.Bytes())
	closeErr := file.Close()
	if writeErr != nil || closeErr != nil {
		t.Fatal("cannot finalize private diagnostic receipt")
	}
	decoder := json.NewDecoder(bytes.NewReader(output.Bytes()))
	requests := 0
	for decoder.More() {
		var row record
		if err := decoder.Decode(&row); err != nil {
			t.Fatal(err)
		}
		switch row.Type {
		case "header":
		case "request":
			requests++
			if !row.Complete || row.QueryStarts != 3 || row.QueryEnds != 3 || row.QueryErrors != 0 ||
				row.ConnectStarts+row.ResetStarts != 3 || row.ConnectStarts != row.ConnectEnds ||
				row.ResetStarts != row.ResetEnds || row.ResetProbes > row.ResetStarts ||
				row.DriverStarts != 3 || row.DriverEnds != 3 || row.DriverErrors != 0 ||
				row.PingStarts != 0 || row.PingEnds != 0 || row.ActiveDriverOperations != 0 {
				t.Fatalf("incorrect request accounting: %+v", row)
			}
		case "footer":
			if !row.Complete || row.QueryStarts != 24 || row.QueryEnds != 24 || row.DriverStarts != 24 ||
				row.DriverEnds != 24 || row.ConnectStarts+row.ResetStarts != 24 || row.DriverErrors != 0 ||
				row.ResetProbes > row.ResetStarts || row.PingStarts != 0 || row.ActiveDriverOperations != 0 {
				t.Fatalf("incorrect footer accounting: %+v", row)
			}
		default:
			t.Fatal("unexpected diagnostic record")
		}
	}
	if requests != 8 {
		t.Fatalf("request summaries: %d", requests)
	}
}
