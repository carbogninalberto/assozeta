//go:build manualretrievalintegration

package manual

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestManualRetrievalOracle(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_MANUAL_RETRIEVAL_FIXTURE"))
	if err != nil {
		t.Fatal("required oracle fixture", err)
	}
	decoded, err := decodeJSON(raw)
	if err != nil {
		t.Fatal(err)
	}
	fixture := decoded.(map[string]any)
	cases := fixture["cases"].([]any)
	if len(cases) != 33 {
		t.Fatalf("unexpected case count %d", len(cases))
	}
	for _, rawCase := range cases {
		tc := rawCase.(map[string]any)
		t.Run(tc["name"].(string), func(t *testing.T) {
			chunks := []map[string]any{}
			for _, v := range tc["applicable"].([]any) {
				chunks = append(chunks, v.(map[string]any))
			}
			before, _ := json.Marshal(tc)
			got, err := readerSections(context.Background(), tc["index"].(map[string]any), chunks, tc["asset_urls"].(bool), tc["query"].(string))
			if tc["expected_error"] == true {
				if err == nil {
					t.Fatal("malformed input accepted")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			a, _ := json.Marshal(got)
			b, _ := json.Marshal(tc["expected"])
			var x, y any
			_ = json.Unmarshal(a, &x)
			_ = json.Unmarshal(b, &y)
			if !reflect.DeepEqual(x, y) {
				t.Fatalf("projection mismatch\ngot %s\nwant %s", a, b)
			}
			after, _ := json.Marshal(tc)
			if string(before) != string(after) {
				t.Fatal("mutated immutable source")
			}
		})
	}
	for _, rawCase := range fixture["tokens"].([]any) {
		tc := rawCase.(map[string]any)
		t.Run("tokens/"+tc["text"].(string), func(t *testing.T) {
			got := manualTokens(tc["text"].(string))
			want := []string{}
			for _, v := range tc["expected"].([]any) {
				want = append(want, v.(string))
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("tokens %v want %v", got, want)
			}
		})
	}
}
