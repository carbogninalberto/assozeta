package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/labstack/echo/v4"
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

type observationReceipt struct {
	Type      string `json:"type"`
	Stream    string `json:"stream"`
	Version   int    `json:"version"`
	RunID     string `json:"run_id"`
	RequestID string `json:"request_id"`
	Sequence  uint64 `json:"sequence"`
	Complete  bool   `json:"complete"`
}

func readObservationReceipts(t *testing.T, path string) []observationReceipt {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var records []observationReceipt
	for _, line := range bytes.Split(bytes.TrimSpace(raw), []byte("\n")) {
		var record observationReceipt
		if err := json.Unmarshal(line, &record); err != nil {
			t.Fatal(err)
		}
		records = append(records, record)
	}
	return records
}

func TestQueryObservationPairedFilesAndGeneratedHTTPIDs(t *testing.T) {
	dir := t.TempDir()
	summary, connection := filepath.Join(dir, "summary"), filepath.Join(dir, "connections")
	o, finalize, err := openQueryObservationFiles(summary, connection)
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{summary, connection} {
		info, err := os.Stat(path)
		if err != nil || info.Mode().Perm() != 0600 {
			t.Fatalf("private sink: %v %v", info, err)
		}
	}
	// Real middleware, in memory: these query-free requests provide no PG evidence.
	e := echo.New()
	e.Pre(middleware.QueryObservation(o))
	e.GET("/", func(c echo.Context) error { return c.NoContent(http.StatusNoContent) })
	var ids []string
	for range 2 {
		r := httptest.NewRequest(http.MethodGet, "/", nil)
		r.Header.Set("X-Query-Observation-ID", "client-selected-secret")
		r.Header.Set("X-Correlation-ID", "client-selected-secret")
		w := httptest.NewRecorder()
		e.ServeHTTP(w, r)
		if w.Code != http.StatusNoContent {
			t.Fatalf("HTTP status: %d", w.Code)
		}
		ids = append(ids, w.Header().Get("X-Query-Observation-ID"))
	}
	if err := finalize(true); err != nil {
		t.Fatal(err)
	}
	summaries, connections := readObservationReceipts(t, summary), readObservationReceipts(t, connection)
	if len(summaries) != 4 || len(connections) != 6 {
		t.Fatalf("record counts: %d %d", len(summaries), len(connections))
	}
	runID := summaries[0].RunID
	if id, err := uuid.Parse(runID); err != nil || id == uuid.Nil || id.String() != runID {
		t.Fatalf("generated run UUID: %q", runID)
	}
	if ids[0] != runID+"-1" || ids[1] != runID+"-2" {
		t.Fatalf("generated response IDs: %v", ids)
	}
	for i, r := range summaries {
		if r.RunID != runID || r.Version != 1 || r.Stream != "" {
			t.Fatalf("summary schema changed: %+v", r)
		}
		if i == 1 || i == 2 {
			if r.Type != "request" || r.RequestID != ids[i-1] {
				t.Fatalf("summary request binding: %+v", r)
			}
		}
	}
	for i, r := range connections {
		if r.RunID != runID || r.Stream != "query_connection" || r.Version != 1 || r.Sequence != uint64(i+1) {
			t.Fatalf("connection schema/sequence: %+v", r)
		}
		if i > 0 && i < 5 {
			wantType := "request_begin"
			if i%2 == 0 {
				wantType = "request_finish"
			}
			if r.Type != wantType || r.RequestID != ids[(i-1)/2] {
				t.Fatalf("connection request binding: %+v", r)
			}
		}
	}
	for _, records := range [][]observationReceipt{summaries, connections} {
		if records[0].Type != "header" || records[0].Complete {
			t.Fatalf("header: %+v", records[0])
		}
		if last := records[len(records)-1]; last.Type != "footer" || !last.Complete {
			t.Fatalf("footer: %+v", last)
		}
	}
}

func TestQueryObservationPairedDisabledAndConnectionOnly(t *testing.T) {
	o, finalize, err := openQueryObservationFiles("", "")
	if err != nil || o != nil || finalize(false) != nil {
		t.Fatalf("disabled: %v %v", o, err)
	}
	path := filepath.Join(t.TempDir(), "must-not-create")
	o, _, err = openQueryObservationFiles("", path)
	if o != nil || err == nil || strings.Contains(err.Error(), path) {
		t.Fatalf("connection-only diagnostics: %v %v", o, err)
	}
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		t.Fatal("connection-only mode created a file")
	}
}

func TestQueryObservationPairedRefusesAliasesAndExistingDestinations(t *testing.T) {
	for _, kind := range []string{"equal", "clean-alias", "directory-symlink-alias", "summary-exists", "connection-exists", "symlink", "dangling-symlink", "hardlink", "missing-directory"} {
		t.Run(kind, func(t *testing.T) {
			dir := t.TempDir()
			summary, connection := filepath.Join(dir, "summary"), filepath.Join(dir, "connection")
			target := filepath.Join(dir, "keep")
			if err := os.WriteFile(target, []byte("private-existing-target"), 0600); err != nil {
				t.Fatal(err)
			}
			var setupErr error
			switch kind {
			case "equal":
				connection = summary
			case "clean-alias":
				connection = dir + "/./summary"
			case "directory-symlink-alias":
				setupErr = os.Symlink(dir, filepath.Join(dir, "alias"))
				connection = filepath.Join(dir, "alias", "summary")
			case "summary-exists":
				summary = target
			case "connection-exists":
				connection = target
			case "symlink":
				setupErr = os.Symlink(target, connection)
			case "dangling-symlink":
				setupErr = os.Symlink(filepath.Join(dir, "absent"), connection)
			case "hardlink":
				setupErr = os.Link(target, connection)
			case "missing-directory":
				connection = filepath.Join(dir, "absent", "connection")
			}
			if setupErr != nil {
				t.Fatal(setupErr)
			}
			o, _, err := openQueryObservationFiles(summary, connection)
			if err == nil || o != nil || strings.Contains(err.Error(), dir) {
				t.Fatalf("unsafe destination accepted or disclosed: %v %v", o, err)
			}
			if raw, err := os.ReadFile(target); err != nil || string(raw) != "private-existing-target" {
				t.Fatal("existing target changed")
			}
			if kind != "summary-exists" {
				if raw, err := os.ReadFile(summary); err != nil || len(raw) != 0 {
					t.Fatal("failed second acquisition wrote a header or removed the first file")
				}
			}
			if kind == "symlink" || kind == "dangling-symlink" {
				if info, err := os.Lstat(connection); err != nil || info.Mode()&os.ModeSymlink == 0 {
					t.Fatal("symlink modified")
				}
			}
		})
	}
}

func TestQueryObservationPairedRejectsIncompleteInvalidAndAbortedDrain(t *testing.T) {
	for _, kind := range []string{"incomplete", "invalid-connection", "aborted"} {
		t.Run(kind, func(t *testing.T) {
			dir := t.TempDir()
			summary, connection := filepath.Join(dir, "summary"), filepath.Join(dir, "connection")
			o, finalize, err := openQueryObservationFiles(summary, connection)
			if err != nil {
				t.Fatal(err)
			}
			ctx, _, finish := o.Begin(context.Background())
			if kind == "invalid-connection" {
				ctx = o.TraceQueryStart(ctx, nil, pgx.TraceQueryStartData{SQL: "private query password"})
				o.TraceQueryEnd(ctx, nil, pgx.TraceQueryEndData{})
				finish()
			}
			if err := finalize(kind != "aborted"); err == nil || strings.Contains(err.Error(), dir) || strings.Contains(err.Error(), "password") {
				t.Fatalf("invalid evidence accepted/disclosed: %v", err)
			}
			for _, path := range []string{summary, connection} {
				records := readObservationReceipts(t, path)
				last := records[len(records)-1]
				if kind == "aborted" {
					for _, r := range records {
						if r.Type == "footer" {
							t.Fatal("aborted drain serialized footer")
						}
					}
				} else if last.Type != "footer" || last.Complete {
					t.Fatalf("invalid/incomplete footer: %+v", last)
				}
			}
			if kind == "aborted" {
				beforeSummary, _ := os.ReadFile(summary)
				beforeConnection, _ := os.ReadFile(connection)
				finish()
				if err := o.Close(); err == nil {
					t.Fatal("late callbacks repaired aborted evidence")
				}
				afterSummary, _ := os.ReadFile(summary)
				afterConnection, _ := os.ReadFile(connection)
				if !bytes.Equal(beforeSummary, afterSummary) || !bytes.Equal(beforeConnection, afterConnection) {
					t.Fatal("late callbacks changed closed output")
				}
			}
		})
	}
}

// Inject only filesystem operations to exercise deterministic I/O failures
// without listeners, PostgreSQL, process-wide hooks, or changing the observer.
type observationTestSink struct {
	bytes.Buffer
	name                        string
	events                      *[]string
	writeCalls, failWriteAt     int
	syncCalls, closeCalls       int
	writeErr, syncErr, closeErr error
}

func (s *observationTestSink) Write(p []byte) (int, error) {
	s.writeCalls++
	var record observationReceipt
	_ = json.Unmarshal(p, &record)
	*s.events = append(*s.events, s.name+":"+record.Type)
	if s.closeCalls != 0 || s.writeCalls == s.failWriteAt {
		return 0, s.writeErr
	}
	return s.Buffer.Write(p)
}

func (s *observationTestSink) Sync() error {
	s.syncCalls++
	*s.events = append(*s.events, s.name+":sync")
	return s.syncErr
}

func (s *observationTestSink) Close() error {
	s.closeCalls++
	*s.events = append(*s.events, s.name+":close")
	return s.closeErr
}

func TestQueryObservationPairedCleansUpEveryOwnedHandle(t *testing.T) {
	privateErr := errors.New("private SQL password /private/destination")
	for _, failure := range []string{"second-open", "summary-header", "connection-header", "summary-footer", "connection-footer", "summary-sync", "connection-sync", "summary-close", "connection-close", "aborted", "none"} {
		t.Run(failure, func(t *testing.T) {
			var events []string
			summary := &observationTestSink{name: "summary", events: &events, writeErr: privateErr}
			connection := &observationTestSink{name: "connection", events: &events, writeErr: privateErr}
			switch failure {
			case "summary-header":
				summary.failWriteAt = 1
			case "connection-header":
				connection.failWriteAt = 1
			case "summary-footer":
				summary.failWriteAt = 2
			case "connection-footer":
				connection.failWriteAt = 2
			case "summary-sync":
				summary.syncErr = privateErr
			case "connection-sync":
				connection.syncErr = privateErr
			case "summary-close":
				summary.closeErr = privateErr
			case "connection-close":
				connection.closeErr = privateErr
			}
			o, finalize, err := openQueryObservationFilesWith("summary", "connection", func(path string) (queryObservationSink, error) {
				if path == "summary" {
					return summary, nil
				}
				if failure == "second-open" {
					return nil, privateErr
				}
				return connection, nil
			})
			initializationFailed := failure == "second-open" || failure == "summary-header" || failure == "connection-header"
			if initializationFailed {
				if o != nil || err == nil {
					t.Fatal("initialization failure accepted")
				}
			} else {
				if err != nil {
					t.Fatal(err)
				}
				err = finalize(failure != "aborted")
				if (err == nil) != (failure == "none") {
					t.Fatalf("finalization error: %v", err)
				}
			}
			if err != nil && (strings.Contains(err.Error(), "private") || strings.Contains(err.Error(), "password")) {
				t.Fatal("raw diagnostic I/O error disclosed")
			}
			wantConnectionCloses := 1
			if failure == "second-open" {
				wantConnectionCloses = 0
			}
			if summary.closeCalls != 1 || connection.closeCalls != wantConnectionCloses {
				t.Fatalf("independent closes: %d %d", summary.closeCalls, connection.closeCalls)
			}
			wantSyncs := 1
			if initializationFailed || failure == "aborted" {
				wantSyncs = 0
			}
			if summary.syncCalls != wantSyncs || connection.syncCalls != wantSyncs {
				t.Fatalf("independent syncs: %d %d", summary.syncCalls, connection.syncCalls)
			}
			if wantSyncs == 1 {
				want := "summary:header,connection:header,summary:footer,connection:footer,summary:sync,summary:close,connection:sync,connection:close"
				if strings.Join(events, ",") != want {
					t.Fatalf("finalization order: %v", events)
				}
			}
		})
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
