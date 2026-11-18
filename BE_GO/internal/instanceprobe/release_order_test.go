package instanceprobe

import (
	"errors"
	"math"
	"reflect"
	"strings"
	"testing"
)

// loadOrderedJSON is json.loads for the feed: an object keeps its source key
// order, a repeated key keeping its first position and taking its last value.
// LoadJSON's maps stay as they are for its other callers.
func TestLoadOrderedJSONKeepsSourceOrder(t *testing.T) {
	text := []byte(`{"b":1,"a":2,"b":3}`)
	value, err := loadOrderedJSON(text)
	if err != nil {
		t.Fatal(err)
	}
	object, ok := value.(Dict)
	if !ok {
		t.Fatalf("loadOrderedJSON = %T, want Dict", value)
	}
	if want := (Dict{{Name: "b", Value: Int("3")}, {Name: "a", Value: Int("2")}}); !reflect.DeepEqual(object, want) {
		t.Fatalf("loadOrderedJSON = %+v, want %+v", object, want)
	}

	list, err := loadOrderedJSON([]byte(`[{"b":1,"a":{"d":4,"c":5}}]`))
	if err != nil {
		t.Fatal(err)
	}
	items, ok := list.([]any)
	if !ok || len(items) != 1 {
		t.Fatalf("loadOrderedJSON list = %+v", list)
	}
	item, ok := items[0].(Dict)
	if !ok || len(item) != 2 || item[0].Name != "b" || item[1].Name != "a" {
		t.Fatalf("nested object = %+v, want the keys b, a", items[0])
	}
	nested, ok := item[1].Value.(Dict)
	if !ok || len(nested) != 2 || nested[0].Name != "d" || nested[1].Name != "c" {
		t.Fatalf("object in a list = %+v, want the keys d, c", item[1].Value)
	}

	plain, err := LoadJSON(text)
	if err != nil {
		t.Fatal(err)
	}
	if _, isMap := plain.(map[string]any); !isMap {
		t.Fatalf("LoadJSON = %T, want map[string]any", plain)
	}
}

// The release feed keeps each object's source key order through normalize
// and the summary render.
func TestFeedKeepsObjectKeyOrder(t *testing.T) {
	page := []byte(`[{"id":1,"tag_name":"v1.0.0","published_at":"2024-01-01",` +
		`"name":{"b":1,"a":2},"body":[{"z":1,"y":2}],"assets":[]}]`)
	value, err := loadOrderedJSON(page)
	if err != nil {
		t.Fatal(err)
	}
	items, ok := value.([]any)
	if !ok {
		t.Fatalf("feed page = %T, want []any", value)
	}
	var set ReleaseSet
	if err = set.Add(items); err != nil {
		t.Fatal(err)
	}
	releases := set.Releases()
	if len(releases) != 1 {
		t.Fatalf("%d releases, want 1", len(releases))
	}
	rendered, err := Render(releases[0].Map())
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{`"name":{"b":1,"a":2}`, `"notes":[{"z":1,"y":2}]`} {
		if !strings.Contains(string(rendered), want) {
			t.Fatalf("render %s does not contain %s", rendered, want)
		}
	}
}

// Every NaN id is the one json.decoder.NaN key, on every page: the first
// position keeps the last value.
func TestFeedNaNIDsFoldAcrossPages(t *testing.T) {
	page := func(text string) []any {
		t.Helper()
		value, err := loadOrderedJSON([]byte(text))
		if err != nil {
			t.Fatal(err)
		}
		items, ok := value.([]any)
		if !ok {
			t.Fatalf("feed page = %T, want []any", value)
		}
		return items
	}
	var set ReleaseSet
	if err := set.Add(page(`[{"id":1,"tag_name":"v1.0.0","published_at":"2024-01-01"},` +
		`{"id":NaN,"tag_name":"v1.0.5","published_at":"2024-01-02"}]`)); err != nil {
		t.Fatal(err)
	}
	if err := set.Add(page(`[{"id":NaN,"tag_name":"v1.0.6","published_at":"2024-01-03"},` +
		`{"id":2,"tag_name":"v1.0.1","published_at":"2024-01-04"}]`)); err != nil {
		t.Fatal(err)
	}
	releases := set.Releases()
	if len(releases) != 3 {
		t.Fatalf("%d releases, want 3 (both NaN ids are one key)", len(releases))
	}
	if releases[0].Tag != "v1.0.6" {
		t.Fatalf("first release = %s, want the last NaN value v1.0.6", releases[0].Tag)
	}
	if id, ok := releases[0].IDValue.(float64); !ok || !math.IsNaN(id) {
		t.Fatalf("NaN release id = %+v, want NaN", releases[0].IDValue)
	}
	for _, release := range releases {
		if release.Tag == "v1.0.5" {
			t.Fatalf("the NaN key kept the first value: %+v", releases)
		}
	}
}

// An unhashable feed value is normalize_release's TypeError: Django's 500
// page (application state).
func TestOrderedFeedUnhashableIsAppState(t *testing.T) {
	for name, page := range map[string]string{
		"id object": `[{"id":{"a":1},"tag_name":"v1.0.0","published_at":"2024-01-01"}]`,
		"asset name object": `[{"id":1,"tag_name":"v1.0.0","published_at":"2024-01-01",` +
			`"assets":[{"name":{"a":1},"state":"uploaded","size":1}]}]`,
	} {
		t.Run(name, func(t *testing.T) {
			value, err := loadOrderedJSON([]byte(page))
			if err != nil {
				t.Fatal(err)
			}
			items := value.([]any)
			var set ReleaseSet
			if err = set.Add(items); !errors.Is(err, ErrReleaseRaises) {
				t.Fatalf("Add error = %v, want ErrReleaseRaises", err)
			}
		})
	}
}
