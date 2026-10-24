//go:build interop

package service

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

// Structural drift detection supplements the explicit behavior contracts. It is
// not a claim that every mapped endpoint or nested resource has been authorized.
func TestPermissionRegistryMatchesDjangoDeclarations(t *testing.T) {
	path := os.Getenv("ASSOZETA_PERMISSION_REGISTRY")
	if path == "" {
		t.Fatal("run through TESTS/adapters/django/permissions.py")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var manifest struct {
		Rules    []permissionRule
		Excluded []string
	}
	if err := json.Unmarshal(data, &manifest); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(manifest.Rules, collaboratorRules) {
		t.Fatal("permission rules differ from Django; review mappings and behavior contracts")
	}
	if !reflect.DeepEqual(manifest.Excluded, collaboratorExclusions) {
		t.Fatal("permission exemptions differ from Django")
	}
}
