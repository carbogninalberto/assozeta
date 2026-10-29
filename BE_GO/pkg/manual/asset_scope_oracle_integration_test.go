//go:build manualassetscopeintegration

package manual

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

type assetScopeExpected struct {
	Access   string `json:"access"`
	Artifact string `json:"artifact,omitempty"`
}

type assetScopeCase struct {
	Name      string
	Settings  Settings
	Features  []string
	AssetPath string
	Expected  assetScopeExpected
}

type assetScopeCheck struct {
	AssetPath string
	Expected  assetScopeExpected
}

type assetScopeStage struct {
	Name   string
	Writes map[string]string
	Checks []assetScopeCheck
}

type assetScopeSequence struct {
	Name     string
	Settings Settings
	Features []string
	Expected assetScopeExpected
	Stages   []assetScopeStage
}

type assetScopeReceipt struct {
	Format               int               `json:"format"`
	FixtureSpecSHA256    string            `json:"fixture_spec_sha256"`
	CaseDefinitionSHA256 string            `json:"case_definition_sha256"`
	FixtureFiles         map[string]string `json:"fixture_files"`
	SourceFiles          map[string]string `json:"source_files"`
	ArtifactSHA256       map[string]string `json:"artifact_sha256"`
	RepositoryRoot       string            `json:"repository_root"`
	RepositoryFiles      map[string]string `json:"repository_files"`
	GoModuleRoot         string            `json:"go_module_root"`
	GoFiles              map[string]string `json:"go_files"`
}

type assetScopeManifest struct {
	FixtureRoot           string
	SourceRoot            string
	SourceBytes           map[string]string
	Artifacts             map[string]string
	InitialFiles          map[string]string
	InputReceipt          json.RawMessage
	InputReceiptCanonical string
	InputReceiptSHA256    string
	Cases                 []assetScopeCase
	Sequences             []assetScopeSequence
}

func scopeHash(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func scopeFingerprint(t *testing.T, root string) map[string]string {
	t.Helper()
	result := map[string]string{}
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		relative, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		if entry.Type()&os.ModeSymlink != 0 {
			target, err := os.Readlink(path)
			if err != nil {
				return err
			}
			result[filepath.ToSlash(relative)] = "symlink:" + target
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		result[filepath.ToSlash(relative)] = scopeHash(data)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func scopeLoadManifest(t *testing.T) assetScopeManifest {
	t.Helper()
	path := os.Getenv("ASSOZETA_MANUAL_ASSET_SCOPE_FIXTURE")
	if !strings.HasPrefix(filepath.Base(filepath.Dir(path)), "az-manual-asset-scope-") {
		t.Fatal("private offline fixture required")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var manifest assetScopeManifest
	if err = json.Unmarshal(raw, &manifest); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(manifest.FixtureRoot, filepath.Dir(path)+string(filepath.Separator)) {
		t.Fatal("fixture root is outside the private harness")
	}
	return manifest
}

func scopeVerifyHashFiles(t *testing.T, root string, expected map[string]string) {
	t.Helper()
	for relative, digest := range expected {
		path := filepath.Join(root, filepath.FromSlash(relative))
		inside, err := filepath.Rel(root, path)
		if err != nil || inside == ".." || strings.HasPrefix(inside, ".."+string(filepath.Separator)) {
			t.Fatal("receipt path escapes its root")
		}
		data, err := os.ReadFile(path)
		if err != nil || scopeHash(data) != digest {
			t.Fatalf("receipt does not bind %s", relative)
		}
	}
}

func scopeVerifyReceipt(t *testing.T, manifest assetScopeManifest) {
	t.Helper()
	if manifest.InputReceiptSHA256 != scopeHash([]byte(manifest.InputReceiptCanonical)) {
		t.Fatal("canonical input receipt hash differs")
	}
	var receipt assetScopeReceipt
	if err := json.Unmarshal(manifest.InputReceipt, &receipt); err != nil {
		t.Fatal(err)
	}
	if receipt.Format != 1 || receipt.FixtureSpecSHA256 == "" || receipt.CaseDefinitionSHA256 == "" {
		t.Fatal("incomplete input receipt")
	}
	if !reflect.DeepEqual(receipt.FixtureFiles, manifest.InitialFiles) {
		t.Fatal("initial fixture and receipt differ")
	}
	scopeVerifyHashFiles(t, manifest.FixtureRoot, receipt.FixtureFiles)
	scopeVerifyHashFiles(t, receipt.RepositoryRoot, receipt.RepositoryFiles)
	scopeVerifyHashFiles(t, receipt.GoModuleRoot, receipt.GoFiles)
	for relative, encoded := range manifest.SourceBytes {
		data, err := base64.StdEncoding.DecodeString(encoded)
		if err != nil || receipt.SourceFiles[relative] != scopeHash(data) {
			t.Fatalf("source receipt differs for %s", relative)
		}
	}
	for name, encoded := range manifest.Artifacts {
		data, err := base64.StdEncoding.DecodeString(encoded)
		if err != nil || receipt.ArtifactSHA256[name] != scopeHash(data) {
			t.Fatalf("artifact receipt differs for %s", name)
		}
	}
}

func scopeObserve(t *testing.T, loader *Loader, features []string, assetPath string, expected assetScopeExpected, artifacts map[string]string) assetScopeExpected {
	t.Helper()
	data, err := loader.ReadAsset(context.Background(), features, assetPath)
	var observed assetScopeExpected
	if errors.Is(err, ErrAssetNotFound) {
		observed = assetScopeExpected{Access: "denied"}
	} else if err != nil {
		t.Fatal("unexpected asset error", err)
	} else {
		expectedBytes, decodeErr := base64.StdEncoding.DecodeString(artifacts[expected.Artifact])
		if decodeErr != nil || !bytes.Equal(data, expectedBytes) {
			t.Fatal("verified asset bytes differ", decodeErr)
		}
		observed = assetScopeExpected{Access: "bytes", Artifact: expected.Artifact}
	}
	if !reflect.DeepEqual(observed, expected) {
		t.Fatal("asset outcome differs", observed, expected)
	}
	return observed
}

func scopeChanged(before, after map[string]string) []string {
	changed := []string{}
	for path := range before {
		if before[path] != after[path] {
			changed = append(changed, path)
		}
	}
	for path := range after {
		if _, present := before[path]; !present {
			changed = append(changed, path)
		}
	}
	return changed
}

func scopeSafeSourcePath(t *testing.T, sourceRoot, relative string) string {
	t.Helper()
	path := filepath.Join(sourceRoot, filepath.FromSlash(relative))
	inside, err := filepath.Rel(sourceRoot, path)
	if err != nil || inside == ".." || strings.HasPrefix(inside, ".."+string(filepath.Separator)) {
		t.Fatal("declared source write escapes source root")
	}
	return path
}

func scopeRunSequence(t *testing.T, manifest assetScopeManifest, sequence assetScopeSequence) map[string]assetScopeExpected {
	t.Helper()
	initial := scopeFingerprint(t, manifest.FixtureRoot)
	loader := New(sequence.Settings)
	outcomes := map[string]assetScopeExpected{}
	for _, stage := range sequence.Stages {
		beforeWrite := scopeFingerprint(t, manifest.FixtureRoot)
		expectedChanges := []string{}
		for relative, encoded := range stage.Writes {
			data, err := base64.StdEncoding.DecodeString(encoded)
			if err != nil {
				t.Fatal(err)
			}
			path := scopeSafeSourcePath(t, sequence.Settings.SourceRoot, relative)
			if err = os.WriteFile(path, data, 0600); err != nil {
				t.Fatal(err)
			}
			sourceRelative, _ := filepath.Rel(manifest.FixtureRoot, path)
			expectedChanges = append(expectedChanges, filepath.ToSlash(sourceRelative))
		}
		afterWrite := scopeFingerprint(t, manifest.FixtureRoot)
		actualChanges := scopeChanged(beforeWrite, afterWrite)
		if len(actualChanges) != len(expectedChanges) {
			t.Fatalf("mutation file effects differ: %v, %v", actualChanges, expectedChanges)
		}
		want := map[string]bool{}
		for _, path := range expectedChanges {
			want[path] = true
		}
		for _, path := range actualChanges {
			if !want[path] {
				t.Fatalf("undeclared mutation at %s", path)
			}
		}
		for _, check := range stage.Checks {
			observed := scopeObserve(t, loader, sequence.Features, check.AssetPath, check.Expected, manifest.Artifacts)
			outcomes["sequence/"+sequence.Name+"/"+stage.Name+"/"+check.AssetPath] = observed
		}
		if afterRead := scopeFingerprint(t, manifest.FixtureRoot); !reflect.DeepEqual(afterRead, afterWrite) {
			t.Fatal("asset read changed fixture files")
		}
	}
	if final := scopeFingerprint(t, manifest.FixtureRoot); !reflect.DeepEqual(final, initial) {
		t.Fatal("mutation sequence did not restore exact fixture")
	}
	return outcomes
}

func scopeWriteResults(t *testing.T, results map[string]assetScopeExpected) {
	t.Helper()
	path := os.Getenv("ASSOZETA_MANUAL_ASSET_SCOPE_RESULT")
	if path == "" {
		t.Fatal("result path required")
	}
	data, err := json.Marshal(results)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestManualAssetScopeDifferential(t *testing.T) {
	manifest := scopeLoadManifest(t)
	scopeVerifyReceipt(t, manifest)
	if initial := scopeFingerprint(t, manifest.FixtureRoot); !reflect.DeepEqual(initial, manifest.InitialFiles) {
		t.Fatal("fixture initial fingerprint differs")
	}
	results := map[string]assetScopeExpected{}
	passed := true
	passed = t.Run("case", func(t *testing.T) {
		for _, tc := range manifest.Cases {
			tc := tc
			if !t.Run(tc.Name, func(t *testing.T) {
				before := scopeFingerprint(t, manifest.FixtureRoot)
				loader := New(tc.Settings)
				results["case/"+tc.Name] = scopeObserve(t, loader, tc.Features, tc.AssetPath, tc.Expected, manifest.Artifacts)
				if after := scopeFingerprint(t, manifest.FixtureRoot); !reflect.DeepEqual(after, before) {
					t.Fatal("case changed fixture files")
				}
			}) {
				passed = false
			}
		}
	}) && passed
	passed = t.Run("sequence", func(t *testing.T) {
		for _, sequence := range manifest.Sequences {
			sequence := sequence
			if !t.Run(sequence.Name, func(t *testing.T) {
				for name, observed := range scopeRunSequence(t, manifest, sequence) {
					results[name] = observed
				}
			}) {
				passed = false
			}
		}
	}) && passed
	if final := scopeFingerprint(t, manifest.FixtureRoot); !reflect.DeepEqual(final, manifest.InitialFiles) {
		t.Fatal("differential changed fixture files")
	}
	scopeWriteResults(t, results)
	if !passed {
		t.FailNow()
	}
}
