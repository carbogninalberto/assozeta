package manual

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func fixtureIndex(t *testing.T, path, state string) {
	t.Helper()
	value := map[string]any{"format": json.Number("1"), "embedding": "italian-tfidf-lsa-v5", "dependencies": map[string]any{}, "chunks": []any{}, "metadata": map[string]any{"application_revision": "revision", "release": "release", "code_state": state}}
	sealed, err := canonical(value)
	if err != nil {
		t.Fatal(err)
	}
	value["identity"] = hash(sealed)
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
}
func TestAvailabilityCacheReplacementAndConcurrentReaders(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "index.json")
	loader := New(Settings{BundlePath: filepath.Join(root, "missing"), IndexPath: path, ApplicationRevision: "revision", Release: "release"})
	fixtureIndex(t, path, "committed")
	if ok, err := loader.Available(context.Background()); !ok || err != nil {
		t.Fatal(ok, err)
	}
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	replacement := filepath.Join(root, "next.json")
	fixtureIndex(t, replacement, "untrusted") // same byte length
	if err = os.Chtimes(replacement, before.ModTime(), before.ModTime()); err != nil {
		t.Fatal(err)
	}
	if err = os.Rename(replacement, path); err != nil {
		t.Fatal(err)
	}
	if ok, err := loader.Available(context.Background()); ok || err != nil {
		t.Fatal("replacement reused old cache", ok, err)
	}
	fixtureIndex(t, path, "committed")
	var wait sync.WaitGroup
	for range 20 {
		wait.Add(1)
		go func() {
			defer wait.Done()
			for range 25 {
				if ok, err := loader.Available(context.Background()); !ok || err != nil {
					t.Error(ok, err)
				}
			}
		}()
	}
	wait.Wait()
	if len(loader.indexes) > 2 {
		t.Fatal("cache exceeds two entries")
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if ok, err := loader.Available(context.Background()); ok || err != nil {
		t.Fatal("removed file stayed available", ok, err)
	}
}
func TestAvailabilityCancellationAndRemoteNeedsNoArtifact(t *testing.T) {
	loader := New(Settings{BundlePath: filepath.Join(t.TempDir(), "missing"), CorpusBaseURL: "not even a URL"})
	if ok, err := loader.Available(context.Background()); !ok || err != nil {
		t.Fatal(ok, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := loader.Available(ctx); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if _, err := loader.Available(nil); err == nil {
		t.Fatal("nil context accepted")
	}
}
func TestOwnedPathsResolveSymlinkBeforeParent(t *testing.T) {
	root := t.TempDir()
	base := filepath.Join(root, "base")
	outside := filepath.Join(root, "outside")
	for _, path := range []string{base, filepath.Join(outside, "child")} {
		if err := os.MkdirAll(path, 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(filepath.Join(outside, "child"), filepath.Join(base, "link")); err != nil {
		t.Fatal(err)
	}
	if _, err := owned(base, "link/../missing"); err == nil {
		t.Fatal("symlink-parent escape accepted")
	}
	resolvedBase, err := filepath.EvalSymlinks(base)
	if err != nil {
		t.Fatal(err)
	}
	got, err := owned(filepath.Join(base, "missing-root"), "missing-file")
	if err != nil || got != filepath.Join(resolvedBase, "missing-root/missing-file") {
		t.Fatal(got, err)
	}
	for _, path := range []string{".", "..", "../outside", "link"} {
		if _, err := owned(base, path); err == nil {
			t.Fatalf("escape accepted %s", path)
		}
	}
}
func TestCanonicalRejectsUnpairedUnicodeSurrogates(t *testing.T) {
	for _, raw := range []string{`{"text":"\ud800"}`, `{"text":"\udfff"}`, `{"text":"\ud800x"}`} {
		if _, err := decodeSealedJSON([]byte(raw)); err == nil {
			t.Fatal("lossy surrogate accepted")
		}
	}
	value, err := decodeSealedJSON([]byte(`{"text":"\ud83d\ude00","literal":"\\ud800"}`))
	if err != nil {
		t.Fatal(err)
	}
	got, err := canonical(value)
	if err != nil || string(got) != `{"literal":"\\ud800","text":"😀"}` {
		t.Fatal(string(got), err)
	}
}

func TestRequiredBundleNeverUsesAvailabilityFallback(t *testing.T) {
	settings := Settings{BundlePath: filepath.Join(t.TempDir(), "absent"), BundleRequired: true, CorpusBaseURL: "configured-private-publisher"}
	if err := CheckBundle(context.Background(), settings); err == nil {
		t.Fatal("required package bypassed")
	}
	if err := CheckBundle(nil, settings); err == nil {
		t.Fatal("nil context accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := CheckBundle(ctx, settings); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
