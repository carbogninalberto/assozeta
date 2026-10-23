//go:build manualtoolintegration

package manual

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// This exercises the knowledge library with explicit trusted context. It does
// not establish authentication, database authority, MCP or agent dispatch.
func TestManualToolOracle(t *testing.T) {
	path := os.Getenv("ASSOZETA_MANUAL_TOOL_FIXTURE")
	if !strings.HasPrefix(filepath.Base(filepath.Dir(path)), "az-manual-http-") {
		t.Fatal("private adapter fixture required")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var tc struct {
		Settings  Settings
		Context   ToolContext
		Name      string
		Arguments map[string]any
		Expected  map[string]any
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err = decoder.Decode(&tc); err != nil {
		t.Fatal(err)
	}
	loader := New(tc.Settings)
	actual, err := loader.ExecuteTool(context.Background(), tc.Context, tc.Name, tc.Arguments)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(actual)
	if err != nil {
		t.Fatal(err)
	}
	// JSON numbers compare by numeric value, without changing result structure.
	var expectedJSON, actualJSON any
	want, _ := json.Marshal(tc.Expected)
	json.Unmarshal(want, &expectedJSON)
	json.Unmarshal(encoded, &actualJSON)
	if !reflect.DeepEqual(actualJSON, expectedJSON) {
		t.Fatalf("%s differs\nactual: %s\nexpected: %s", tc.Name, encoded, want)
	}
	if err = os.WriteFile(path+".result.json", encoded, 0600); err != nil {
		t.Fatal(err)
	}
}
