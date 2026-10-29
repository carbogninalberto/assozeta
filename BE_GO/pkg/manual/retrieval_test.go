package manual

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func retrievalFixture(t *testing.T) (map[string]any, []map[string]any) {
	t.Helper()
	raw, err := os.ReadFile("../../../TESTS/fixtures/manual-reader-retrieval.json")
	if err != nil {
		t.Fatal(err)
	}
	v, err := decodeJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	index := v.(map[string]any)["index"].(map[string]any)
	chunks := []map[string]any{}
	for _, raw := range index["chunks"].([]any) {
		c := raw.(map[string]any)
		if c["status"] == "verified" && c["audience"] == "public" && len(c["features"].([]any)) == 0 {
			chunks = append(chunks, c)
		}
	}
	return index, chunks
}
func TestManualRetrievalCopiesAllNestedOutput(t *testing.T) {
	index, chunks := retrievalFixture(t)
	before, _ := json.Marshal(index)
	response, err := readerSections(context.Background(), index, chunks, true, "")
	if err != nil {
		t.Fatal(err)
	}
	first := response["results"].([]any)[0].(map[string]any)
	first["screenshots"].([]any)[0].(map[string]any)["caption"] = "mutated"
	first["reader"].([]any)[0].(map[string]any)["content"].([]any)[0].(map[string]any)["markdown"] = "mutated"
	first["features"] = []any{"changed"}
	after, _ := json.Marshal(index)
	if string(before) != string(after) {
		t.Fatal("returned output changed cached input")
	}
	again, err := readerSections(context.Background(), index, chunks, true, "")
	if err != nil {
		t.Fatal(err)
	}
	item := again["results"].([]any)[0].(map[string]any)
	if item["screenshots"].([]any)[0].(map[string]any)["caption"] == "mutated" {
		t.Fatal("request leaked into next projection")
	}
}
func TestManualRetrievalConcurrentCallsKeepScoreRequestLocal(t *testing.T) {
	index, chunks := retrievalFixture(t)
	before, _ := json.Marshal(index)
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			query := ""
			if i%2 == 1 {
				query = "generare codice fiscale"
			}
			result, err := readerSections(context.Background(), index, chunks, true, query)
			if err != nil {
				t.Error(err)
				return
			}
			for _, raw := range result["results"].([]any) {
				c := raw.(map[string]any)
				if query == "" && c["score"] != nil {
					t.Error("cached search score leaked")
				}
				c["reader"] = "local"
			}
		}(i)
	}
	wg.Wait()
	after, _ := json.Marshal(index)
	if string(before) != string(after) {
		t.Fatal("concurrent requests changed input")
	}
}
func TestManualRetrievalCancellationAndNilContext(t *testing.T) {
	index, chunks := retrievalFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	for _, query := range []string{"", "tag"} {
		got, err := readerSections(ctx, index, chunks, true, query)
		if got != nil || !errors.Is(err, context.Canceled) {
			t.Fatal(got, err)
		}
	}
	got, err := readerSections(nil, index, chunks, true, "tag")
	if got != nil || !errors.Is(err, ErrReaderUnavailable) {
		t.Fatal(got, err)
	}
}

type cancelAfterChecks struct {
	checks atomic.Int64
	after  int64
}

func (c *cancelAfterChecks) Deadline() (time.Time, bool) { return time.Time{}, false }
func (c *cancelAfterChecks) Done() <-chan struct{}       { return nil }
func (c *cancelAfterChecks) Value(any) any               { return nil }
func (c *cancelAfterChecks) Err() error {
	if c.checks.Add(1) >= c.after {
		return context.Canceled
	}
	return nil
}
func TestManualRetrievalCancellationDuringProjectionAndSearch(t *testing.T) {
	index, chunks := retrievalFixture(t)
	for _, query := range []string{"", "tag"} {
		ctx := &cancelAfterChecks{after: 4}
		got, err := readerSections(ctx, index, chunks, true, query)
		if got != nil || !errors.Is(err, context.Canceled) {
			t.Fatalf("query%q got%v error%v", query, got, err)
		}
	}
}
func TestManualRetrievalDoesNotModifyModelOnSearch(t *testing.T) {
	index, chunks := retrievalFixture(t)
	before, _ := json.Marshal(index["semantics"])
	for _, query := range []string{"tag", "codice fiscale", "caffè città", "OAuth"} {
		if _, err := readerSections(context.Background(), index, chunks, false, query); err != nil {
			t.Fatal(err)
		}
	}
	after, _ := json.Marshal(index["semantics"])
	if string(before) != string(after) {
		t.Fatal("sealed model changed")
	}
}
