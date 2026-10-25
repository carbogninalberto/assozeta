package manual

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

type assetScopeFixture struct {
	root   string
	source string
	index  map[string]any
}

func newAssetScopeFixture(t *testing.T) *assetScopeFixture {
	t.Helper()
	root := t.TempDir()
	f := &assetScopeFixture{root: root, source: filepath.Join(root, "source")}
	sources := map[string]string{
		"BE/course.py":              "course_source()\n",
		"BE/member.py":              "member_source()\n",
		"BE/feature.py":             "feature_source()\n",
		"BE/core/authentication.py": "authentication_source()\n",
	}
	for relative, contents := range sources {
		path := filepath.Join(f.source, relative)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(contents), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.MkdirAll(filepath.Join(root, "images"), 0700); err != nil {
		t.Fatal(err)
	}
	for name, contents := range map[string]string{
		"course.png": "course bytes",
		"shared.png": "shared bytes",
	} {
		if err := os.WriteFile(filepath.Join(root, "images", name), []byte(contents), 0600); err != nil {
			t.Fatal(err)
		}
	}
	f.index = sourceCorpus(t, filepath.Join(root, "runtime.json"))
	f.index["dependencies"] = map[string]any{
		"BE/core/authentication.py": hash([]byte(sources["BE/core/authentication.py"])),
	}
	f.index["chunks"] = []any{
		sourceScopeChunk("course", "public", nil, "BE/course.py", hash([]byte(sources["BE/course.py"])), "images/course.png"),
		sourceScopeChunk("member", "public", nil, "BE/member.py", hash([]byte(sources["BE/member.py"])), "images/shared.png"),
		sourceScopeChunk("feature", "public", []any{"self_hosted"}, "BE/feature.py", hash([]byte(sources["BE/feature.py"])), "images/shared.png"),
	}
	f.writeIndex(t)
	return f
}

func sourceScopeChunk(id, audience string, features []any, source, digest, asset string) map[string]any {
	imageDigest := hash([]byte("shared bytes"))
	if asset == "images/course.png" {
		imageDigest = hash([]byte("course bytes"))
	}
	if features == nil {
		features = []any{}
	}
	return map[string]any{
		"id":       id,
		"status":   "verified",
		"audience": audience,
		"features": features,
		"evidence": []any{map[string]any{"path": source, "sha256": digest}},
		"screenshots": []any{
			map[string]any{"path": asset, "sha256": imageDigest},
		},
	}
}

func (f *assetScopeFixture) writeIndex(t *testing.T) {
	t.Helper()
	writeSourceCorpus(t, filepath.Join(f.root, "runtime.json"), f.index)
}

func (f *assetScopeFixture) loader() *Loader {
	return New(Settings{
		BundlePath:          filepath.Join(f.root, "missing-bundle"),
		IndexPath:           filepath.Join(f.root, "runtime.json"),
		AssetRoot:           f.root,
		SourceRoot:          f.source,
		ApplicationRevision: "revision",
		Release:             "release",
	})
}

func (f *assetScopeFixture) chunk(id string) map[string]any {
	for _, raw := range f.index["chunks"].([]any) {
		chunk := raw.(map[string]any)
		if chunk["id"] == id {
			return chunk
		}
	}
	panic("missing chunk " + id)
}

func TestManualAssetSourceReadsAreScopedToAccessibleReferringSections(t *testing.T) {
	f := newAssetScopeFixture(t)
	private := sourceScopeChunk("private", "maintainer", nil, "../outside-source", hash([]byte("private")), "images/private.png")
	member := f.chunk("member")
	member["evidence"] = []any{map[string]any{"path": "../outside-member", "sha256": hash([]byte("member"))}}
	f.index["chunks"] = append(f.index["chunks"].([]any), private)
	f.writeIndex(t)

	loader := f.loader()
	if data, err := loader.ReadAsset(context.Background(), nil, "images/course.png"); err != nil || string(data) != "course bytes" {
		t.Fatal("asset request read a non-referring or inaccessible section source", data, err)
	}
	member["evidence"] = []any{map[string]any{"path": "BE/member.py", "sha256": hash([]byte("member_source()\n"))}}
	feature := f.chunk("feature")
	feature["evidence"] = []any{map[string]any{"path": "../outside-feature", "sha256": hash([]byte("feature"))}}
	f.writeIndex(t)
	snapshot, err := loader.LoadReader(context.Background(), nil)
	if err != nil || !reflect.DeepEqual(snapshot.SectionIDs(), []string{"course", "member"}) {
		t.Fatal("section request evaluated an inaccessible or feature-inapplicable source", snapshot, err)
	}
}

func TestManualAssetRechecksOnlyReferringSourcesAndSharedSecurityEachRequest(t *testing.T) {
	f := newAssetScopeFixture(t)
	loader := f.loader()
	ctx := context.Background()
	read := func() error {
		data, err := loader.ReadAsset(ctx, nil, "images/course.png")
		if err == nil && string(data) != "course bytes" {
			t.Fatal("asset digest changed")
		}
		return err
	}
	writeSource := func(relative, contents string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(f.source, relative), []byte(contents), 0600); err != nil {
			t.Fatal(err)
		}
	}

	if err := read(); err != nil {
		t.Fatal("baseline asset", err)
	}
	writeSource("BE/member.py", "changed_member()\n")
	if err := read(); err != nil {
		t.Fatal("unrelated source invalidated asset", err)
	}
	writeSource("BE/course.py", "changed_course()\n")
	if err := read(); !errors.Is(err, ErrAssetNotFound) {
		t.Fatal("referring source change was reused", err)
	}
	writeSource("BE/course.py", "course_source()\n")
	if err := read(); err != nil {
		t.Fatal("restored referring source stayed stale", err)
	}
	writeSource("BE/core/authentication.py", "changed_authentication()\n")
	if err := read(); !errors.Is(err, ErrAssetNotFound) {
		t.Fatal("shared security dependency change was reused", err)
	}
}

func TestManualSharedImageRequiresACurrentAccessibleReferringSection(t *testing.T) {
	f := newAssetScopeFixture(t)
	loader := f.loader()
	ctx := context.Background()
	if err := os.WriteFile(filepath.Join(f.source, "BE/course.py"), []byte("changed_course()\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if data, err := loader.ReadAsset(ctx, nil, "images/shared.png"); err != nil || string(data) != "shared bytes" {
		t.Fatal("current referring section did not retain shared image", data, err)
	}

	member := f.chunk("member")
	member["audience"] = "maintainer"
	f.writeIndex(t)
	if _, err := loader.ReadAsset(ctx, nil, "images/shared.png"); !errors.Is(err, ErrAssetNotFound) {
		t.Fatal("inaccessible referring section exposed shared image", err)
	}

	feature := f.chunk("feature")
	if data, err := loader.ReadAsset(ctx, []string{"self_hosted"}, "images/shared.png"); err != nil || string(data) != "shared bytes" {
		t.Fatal("feature-current referring section did not retain shared image", data, err)
	}
	feature["features"] = []any{"other_feature"}
	f.writeIndex(t)
	if _, err := loader.ReadAsset(ctx, []string{"self_hosted"}, "images/shared.png"); !errors.Is(err, ErrAssetNotFound) {
		t.Fatal("feature-inapplicable section exposed shared image", err)
	}
}

func TestManualAssetPreservesVersionAndAccessChecks(t *testing.T) {
	f := newAssetScopeFixture(t)
	for _, mutate := range []func(map[string]any){
		func(chunk map[string]any) { chunk["status"] = "pending" },
		func(chunk map[string]any) { chunk["audience"] = "maintainer" },
		func(chunk map[string]any) { chunk["features"] = []any{"private_feature"} },
	} {
		course := f.chunk("course")
		course["status"] = "verified"
		course["audience"] = "public"
		course["features"] = []any{}
		mutate(course)
		f.writeIndex(t)
		if _, err := f.loader().ReadAsset(context.Background(), nil, "images/course.png"); !errors.Is(err, ErrAssetNotFound) {
			t.Fatal("inapplicable section exposed asset", err)
		}
	}

	f = newAssetScopeFixture(t)
	loader := f.loader()
	loader.settings.ApplicationRevision = "other"
	if _, err := loader.ReadAsset(context.Background(), nil, "images/course.png"); !errors.Is(err, ErrAssetNotFound) {
		t.Fatal("asset request bypassed application revision", err)
	}
	loader.settings.ApplicationRevision = "revision"
	loader.settings.Release = "other"
	if _, err := loader.ReadAsset(context.Background(), nil, "images/course.png"); !errors.Is(err, ErrAssetNotFound) {
		t.Fatal("asset request bypassed release", err)
	}
}
