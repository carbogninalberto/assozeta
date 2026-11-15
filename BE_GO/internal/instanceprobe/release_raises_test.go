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

	notIntID := valid()
	notIntID["id"] = "abc"
	if _, err = normalize(notIntID); !errors.Is(err, ErrReleaseUnread) {
		t.Fatalf("id \"abc\": error %v, want ErrReleaseUnread", err)
	}
	if errors.Is(err, refusal.ErrNotPorted) || errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("id \"abc\": error %v must be neither a refusal nor app state", err)
	}

	release, err = normalize(valid())
	if err != nil || release == nil {
		t.Fatalf("valid release: %v, %v; want a release and nil", release, err)
	}
}
