package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
)

func TestQueryObservationFileDisabled(t *testing.T) {
	o, closeFile, err := openQueryObservation("")
	if err != nil || o != nil {
		t.Fatalf("disabled: %v %v", o, err)
	}
	if err := closeFile(true); err != nil {
		t.Fatal(err)
	}
}

func TestQueryObservationFilePrivateAndFinalized(t *testing.T) {
	path := filepath.Join(t.TempDir(), "queries.jsonl")
	o, closeFile, err := openQueryObservation(path)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0600 {
		t.Fatalf("permissions: %v", info.Mode())
	}
	ctx, _, finish := o.Begin(context.Background())
	ctx = o.TraceQueryStart(ctx, nil, pgx.TraceQueryStartData{SQL: "private query"})
	o.TraceQueryEnd(ctx, nil, pgx.TraceQueryEndData{})
	finish()
	if err := closeFile(true); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "private query") {
		t.Fatal("SQL disclosure")
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 3 {
		t.Fatalf("records: %d", len(lines))
	}
	var footer struct {
		Type     string
		Complete bool
	}
	if err := json.Unmarshal([]byte(lines[2]), &footer); err != nil {
		t.Fatal(err)
	}
	if footer.Type != "footer" || !footer.Complete {
		t.Fatalf("footer: %+v", footer)
	}
}

func TestQueryObservationFileRefusesExistingAndSymlink(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "keep")
	if err := os.WriteFile(target, []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{target, link, dir, filepath.Join(dir, "absent", "file")} {
		o, _, err := openQueryObservation(path)
		if err == nil || o != nil {
			t.Fatalf("accepted unsafe path %s", path)
		}
	}
	raw, err := os.ReadFile(target)
	if err != nil || string(raw) != "keep" {
		t.Fatal("existing file changed")
	}
}

func TestQueryObservationFileRejectsIncompleteFinalization(t *testing.T) {
	o, closeFile, err := openQueryObservation(filepath.Join(t.TempDir(), "queries.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	o.Begin(context.Background())
	if err := closeFile(true); err == nil {
		t.Fatal("incomplete diagnostic accepted")
	}
}

// Failed drains must not serialize a footer while callbacks can still arrive.
func TestQueryObservationFileAbortsWithoutFooterDuringActiveWork(t *testing.T) {
	path := filepath.Join(t.TempDir(), "aborted.jsonl")
	o, closeFile, err := openQueryObservation(path)
	if err != nil {
		t.Fatal(err)
	}
	ctx, _, finish := o.Begin(context.Background())
	ctx = o.TraceQueryStart(ctx, nil, pgx.TraceQueryStartData{})
	if err := closeFile(false); err == nil {
		t.Fatal("aborted diagnostics accepted")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), `"type":"footer"`) {
		t.Fatal("aborted drain wrote footer")
	}
	// Late callbacks cannot turn the partial output into accepted evidence.
	o.TraceQueryEnd(ctx, nil, pgx.TraceQueryEndData{})
	finish()
	if err := o.Close(); err == nil {
		t.Fatal("late callbacks repaired aborted output")
	}
	after, err := os.ReadFile(path)
	if err != nil || string(after) != string(raw) {
		t.Fatal("aborted output changed")
	}
}
