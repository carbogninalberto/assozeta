package manual

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func sourceCorpus(t *testing.T, path string) map[string]any {
	t.Helper()
	value := map[string]any{"format": json.Number("1"), "embedding": "italian-tfidf-lsa-v5", "dependencies": map[string]any{}, "metadata": map[string]any{"application_revision": "revision", "release": "release", "code_state": "committed", "publication_status": "published"}, "chunks": []any{map[string]any{"id": "public", "status": "verified", "audience": "public", "features": []any{}, "evidence": []any{}, "screenshots": []any{map[string]any{"path": "images/a.png", "sha256": hash([]byte("verified bytes"))}}}, map[string]any{"id": "private", "status": "verified", "audience": "maintainer", "features": []any{}, "evidence": []any{}, "screenshots": []any{}}, map[string]any{"id": "host", "status": "verified", "audience": "public", "features": []any{"self_hosted"}, "evidence": []any{}, "screenshots": []any{}}}}
	writeSourceCorpus(t, path, value)
	return value
}
func writeSourceCorpus(t *testing.T, path string, value map[string]any) {
	t.Helper()
	delete(value, "identity")
	raw, err := canonical(value)
	if err != nil {
		t.Fatal(err)
	}
	value["identity"] = hash(raw)
	raw, err = json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
}
func TestManualSourceRuntime(t *testing.T) {
	root := t.TempDir()
	s := Settings{BundlePath: filepath.Join(root, "missing"), IndexPath: filepath.Join(root, "index.json"), ApplicationRevision: "revision", Release: "release", AssetRoot: root}
	sourceCorpus(t, s.IndexPath)
	os.MkdirAll(filepath.Join(root, "images"), 0700)
	os.WriteFile(filepath.Join(root, "images/a.png"), []byte("verified bytes"), 0600)
	l := New(s)
	snapshot, err := l.LoadReader(context.Background(), nil)
	if err != nil || snapshot.IsPreview() || !reflect.DeepEqual(snapshot.SectionIDs(), []string{"public"}) {
		t.Fatal("runtime public selection", err)
	}
	ids := snapshot.SectionIDs()
	ids[0] = "corrupt"
	if snapshot.SectionIDs()[0] != "public" {
		t.Fatal("mutable ID escaped")
	}
	b, err := l.ReadAsset(context.Background(), nil, "images/a.png")
	if err != nil || string(b) != "verified bytes" {
		t.Fatal("hash-valid runtime bytes", err)
	}
	snapshot, err = l.LoadReader(context.Background(), []string{"self_hosted"})
	if err != nil || !reflect.DeepEqual(snapshot.SectionIDs(), []string{"public", "host"}) {
		t.Fatal("live features", err)
	}
	if _, err = l.LoadReader(nil, nil); !errors.Is(err, ErrReaderUnavailable) {
		t.Fatal("nil context")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err = l.LoadReader(ctx, nil); !errors.Is(err, context.Canceled) {
		t.Fatal("cancel", err)
	}
}
func TestManualSourcePreviewDoesNotReplaceOrdinaryAssets(t *testing.T) {
	root := t.TempDir()
	s := Settings{BundlePath: filepath.Join(root, "missing"), IndexPath: filepath.Join(root, "index.json"), Release: "release", DeploymentMode: "development", DevelopmentRoot: filepath.Join(root, "dev"), AssetRoot: root}
	sourceCorpus(t, s.IndexPath)
	raw := []byte(`{"sections":[{"id":"draft","screenshots":[{"path":"draft.png","sha256":"` + hash([]byte("preview")) + `"}]}]}`)
	identity := hash(raw)
	p := filepath.Join(s.DevelopmentRoot, "preview", identity)
	os.MkdirAll(p, 0700)
	os.WriteFile(filepath.Join(p, "index.json"), raw, 0600)
	os.WriteFile(filepath.Join(p, "draft.png"), []byte("preview"), 0600)
	os.WriteFile(filepath.Join(s.DevelopmentRoot, "preview/active.json"), []byte(`{"identity":"`+identity+`"}`), 0600)
	l := New(s)
	snapshot, err := l.LoadReader(context.Background(), nil)
	if err != nil || !snapshot.IsPreview() || !reflect.DeepEqual(snapshot.SectionIDs(), []string{"draft"}) {
		t.Fatal("preview", err)
	}
	b, err := l.ReadAsset(context.Background(), nil, "preview/draft.png")
	if err != nil || string(b) != "preview" {
		t.Fatal("preview asset", err)
	}
	if _, err = l.ReadAsset(context.Background(), nil, "images/a.png"); !errors.Is(err, ErrAssetNotFound) {
		t.Fatal("ordinary asset must use runtime version check", err)
	}
}
