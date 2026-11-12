package manual

import (
	"context"
	"errors"
	"reflect"
	"testing"
)

func TestManualPreviewQueryFoldingRankingAndCopies(t *testing.T) {
	rows := []any{
		map[string]any{"id": "first", "page_title": "Caffè", "title": "Straße", "text": "red blue", "reader": []any{map[string]any{"text": "original"}}},
		map[string]any{"id": "second", "page_title": "Caffè", "title": "Red blue", "text": "Straße"},
		map[string]any{"id": "third", "page_title": "Caffè", "title": "Blue", "text": "red Straße"},
		map[string]any{"id": "greek", "page_title": "Greek", "title": "Σς", "text": "none"},
	}
	s := &ReaderSnapshot{preview: map[string]any{"sections": rows}}
	for _, tc := range []struct {
		query string
		ids   []string
	}{
		{"", []string{"first", "second", "third", "greek"}},
		{"STRASSE", []string{"first", "second", "third"}},
		{"red\x1cblue", []string{"second", "third", "first"}},
		{"σσ", []string{"greek"}},
		{"caffe", []string{}},
	} {
		result, err := s.Sections(context.Background(), tc.query)
		if err != nil {
			t.Fatal(err)
		}
		ids := []string{}
		for _, raw := range result["results"].([]any) {
			ids = append(ids, raw.(map[string]any)["id"].(string))
		}
		if !reflect.DeepEqual(ids, tc.ids) || result["status"] != "development_preview" {
			t.Fatalf("%q: %v", tc.query, result)
		}
	}
	result, _ := s.Sections(context.Background(), "")
	result["results"].([]any)[0].(map[string]any)["reader"].([]any)[0].(map[string]any)["text"] = "changed"
	if rows[0].(map[string]any)["reader"].([]any)[0].(map[string]any)["text"] != "original" {
		t.Fatal("preview cache escaped")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if result, err := s.Sections(ctx, ""); result != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("cancel", result, err)
	}
}

func TestManualSnapshotComposesOrdinaryProjection(t *testing.T) {
	index, chunks := retrievalFixture(t)
	s := &ReaderSnapshot{index: index, applicable: chunks, assetRoot: "fixture"}
	for _, query := range []string{"", "generare codice fiscale"} {
		want, err := readerSections(context.Background(), index, chunks, true, query)
		if err != nil {
			t.Fatal(err)
		}
		got, err := s.Sections(context.Background(), query)
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Fatal("ordinary composition", err)
		}
	}
}
