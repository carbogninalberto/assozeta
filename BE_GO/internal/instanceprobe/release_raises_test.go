package instanceprobe

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// An item normalize_release raises on is application state (Django's 500 page),
// never a refusal handed off.
func TestNormalizeRaisesIsAppState(t *testing.T) {
	release, err := normalize("x")
	if release != nil {
		t.Fatalf("normalize(\"x\") = %v, want nil", release)
	}
	if !errors.Is(err, ErrReleaseRaises) {
		t.Fatalf("normalize(\"x\") error %v, want ErrReleaseRaises", err)
	}
	if !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("normalize(\"x\") error %v, want refusal.ErrAppState", err)
	}
	if errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("normalize(\"x\") error %v must not match refusal.ErrNotPorted", err)
	}

	valid := func() map[string]any {
		return map[string]any{"tag_name": "v1.0.0", "published_at": "2026-01-01T00:00:00Z", "id": Int("1")}
	}

	nullAssets := valid()
	nullAssets["assets"] = nil
	if _, err = normalize(nullAssets); !errors.Is(err, ErrReleaseRaises) {
		t.Fatalf("assets nil: error %v, want ErrReleaseRaises", err)
	}

	missingID := map[string]any{"tag_name": "v1.0.0", "published_at": "2026-01-01T00:00:00Z"}
	if _, err = normalize(missingID); !errors.Is(err, ErrReleaseRaises) {
		t.Fatalf("missing id: error %v, want ErrReleaseRaises", err)
	}

	stringID := valid()
	stringID["id"] = "abc"
	release, err = normalize(stringID)
	if err != nil || release == nil || !release.IDValueSet || release.IDValue != "abc" {
		t.Fatalf("id \"abc\": %v, %v; want the string id kept", release, err)
	}

	unhashableID := valid()
	unhashableID["id"] = []any{Int("1")}
	release, err = normalize(unhashableID)
	if release != nil || !errors.Is(err, ErrReleaseRaises) || !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("list id: %v, %v; want ErrReleaseRaises app state", release, err)
	}

	release, err = normalize(valid())
	if err != nil || release == nil {
		t.Fatalf("valid release: %v, %v; want a release and nil", release, err)
	}
}
