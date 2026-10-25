package manual

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
)

func toolFixture(t *testing.T) (*Loader, string) {
	t.Helper()
	index, _ := retrievalFixture(t)
	index["gaps"] = []any{map[string]any{"page": "docs/test.mdx", "section": "pending", "status": "pending"}}
	for _, raw := range index["chunks"].([]any) {
		chunk := raw.(map[string]any)
		chunk["evidence"] = []any{map[string]any{"path": "BE/private.py", "sha256": strings.Repeat("a", 64), "excerpt": "original"}}
		chunk["capture_provenance"] = []any{map[string]any{"scenario": "captured"}}
	}
	root := t.TempDir()
	path := filepath.Join(root, "runtime.json")
	writeSourceCorpus(t, path, index)
	return New(Settings{BundlePath: filepath.Join(root, "absent"), IndexPath: path,
		ApplicationRevision: "fixture-r1", Release: "fixture-v1"}), path
}

func TestManualToolCopiesAndConcurrentAuthority(t *testing.T) {
	l, path := toolFixture(t)
	if _, err := l.loadKnowledge(context.Background(), nil, true, true, ""); err != nil {
		t.Fatal("fixture source", err)
	}
	before, _ := os.ReadFile(path)
	ctx := context.Background()
	owner := ToolContext{Maintainer: true}
	args := map[string]any{"section_id": "private"}
	result, err := l.ExecuteTool(ctx, owner, "get_manual_evidence", args)
	if err != nil || result["status"] != "verified" {
		t.Fatal(result, err)
	}
	result["evidence"].([]any)[0].(map[string]any)["excerpt"] = "changed"
	result["provenance"].(map[string]any)["release"] = "changed"
	result["capture_provenance"].([]any)[0].(map[string]any)["scenario"] = "changed"
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			caller := ToolContext{Maintainer: i%2 == 0}
			result, err := l.ExecuteTool(ctx, caller, "get_manual_evidence", args)
			if err != nil {
				t.Error(err)
				return
			}
			if !caller.Maintainer {
				if result["status"] != "forbidden" {
					t.Error("owner context escaped", result)
				}
				return
			}
			if result["status"] != "verified" || result["provenance"].(map[string]any)["release"] != "fixture-v1" {
				t.Error("provenance mutated", result)
				return
			}
			evidence := result["evidence"].([]any)[0].(map[string]any)
			if evidence["excerpt"] != "original" {
				t.Error("evidence mutated", evidence)
			}
			evidence["excerpt"] = "concurrent mutation"
		}(i)
	}
	wg.Wait()
	gaps, err := l.ExecuteTool(ctx, owner, "get_manual_gaps", nil)
	if err != nil || gaps["status"] != "diagnostic" {
		t.Fatal(gaps, err)
	}
	gaps["gaps"].([]any)[0].(map[string]any)["section"] = "changed"
	again, err := l.ExecuteTool(ctx, owner, "get_manual_gaps", nil)
	if err != nil || again["gaps"].([]any)[0].(map[string]any)["section"] != "pending" {
		t.Fatal("gap mutation", again, err)
	}
	public, err := l.LoadReader(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range public.SectionIDs() {
		if id == "private" {
			t.Fatal("tool authority leaked into reader")
		}
	}
	after, _ := os.ReadFile(path)
	if string(before) != string(after) {
		t.Fatal("tools wrote the corpus")
	}
}

func TestManualToolCancellationAndAdmission(t *testing.T) {
	l := New(Settings{})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	for _, name := range []string{"search_manual", "get_manual_section", "get_manual_evidence", "get_manual_gaps"} {
		if result, err := l.ExecuteTool(ctx, ToolContext{}, name, nil); result != nil || !errors.Is(err, context.Canceled) {
			t.Fatal(result, err)
		}
		if result, err := l.ExecuteTool(nil, ToolContext{}, name, nil); result != nil || !errors.Is(err, ErrReaderUnavailable) {
			t.Fatal(result, err)
		}
	}
	if result, err := l.ExecuteTool(context.Background(), ToolContext{}, "unknown", nil); result != nil || !errors.Is(err, ErrUnknownTool) {
		t.Fatal(result, err)
	}
	result, err := l.ExecuteTool(context.Background(), ToolContext{}, "get_manual_gaps", map[string]any{"page": []any{}, "maintainer": true})
	if err != nil || result["status"] != "forbidden" {
		t.Fatal("argument escalated authority or loaded invalid source", result, err)
	}
}

func TestManualToolLimitJSONCompatibility(t *testing.T) {
	for _, tc := range []struct {
		value any
		want  int
	}{
		{true, 1}, {false, 1}, {-2, 1}, {2.8, 2}, {json.Number("2.8"), 2}, {json.Number("1e4"), 10},
		{"  +٢_٣ ", 10}, {"𝟚", 2}, {"-9", 1}, {strings.Repeat("9", 4300), 10},
	} {
		got, err := toolLimit(tc.value)
		if err != nil || got != tc.want {
			t.Fatalf("%v: %d %v", tc.value, got, err)
		}
	}
	for _, v := range []any{nil, []any{}, map[string]any{}, "", "2.8", "_2", "2_", "2__3", "2e3", "0x10", "²", "\x1c2", strings.Repeat("9", 4301), math.NaN(), math.Inf(1)} {
		if _, err := toolLimit(v); err == nil {
			t.Fatalf("accepted invalid limit %v", v)
		}
	}
}

func TestManualToolEmptyQueryDoesNotEvaluateLimit(t *testing.T) {
	l, _ := toolFixture(t)
	result, err := l.ExecuteTool(context.Background(), ToolContext{}, "search_manual", map[string]any{"query": "come fare", "limit": nil})
	want := map[string]any{"status": "no_evidence", "results": []any{}, "message": "Non ho trovato istruzioni verificate per questa domanda."}
	if err != nil || !reflect.DeepEqual(result, want) {
		t.Fatal(result, err)
	}
	// A regular query must still evaluate a bad limit even when nothing ranks.
	result, err = l.ExecuteTool(context.Background(), ToolContext{}, "search_manual", map[string]any{"query": "zzzzzzzzz", "limit": nil})
	if err != nil || !reflect.DeepEqual(result, toolUnavailable()) {
		t.Fatal(result, err)
	}
	encoded, _ := json.Marshal(result)
	if strings.Contains(string(encoded), l.settings.IndexPath) {
		t.Fatal("local source disclosed")
	}
}

func TestManualToolStopwordsDoNotReadMissingModel(t *testing.T) {
	l, path := toolFixture(t)
	index, err := readObject(path)
	if err != nil {
		t.Fatal(err)
	}
	delete(index, "semantics")
	writeSourceCorpus(t, path, index)
	result, err := l.ExecuteTool(context.Background(), ToolContext{}, "search_manual", map[string]any{"query": "come fare", "limit": nil})
	want := map[string]any{"status": "no_evidence", "results": []any{}, "message": "Non ho trovato istruzioni verificate per questa domanda."}
	if err != nil || !reflect.DeepEqual(result, want) {
		t.Fatal(result, err)
	}
}
