package service

import (
	"context"
	"errors"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
)

type releaseCacheStub struct {
	releases []instanceprobe.Release
	cached   bool
	stored   []instanceprobe.Release
}

func (s *releaseCacheStub) Catalog(context.Context) ([]instanceprobe.Release, bool, error) {
	return s.releases, s.cached, nil
}

func (s *releaseCacheStub) SetCatalog(_ context.Context, releases []instanceprobe.Release) error {
	s.stored = releases
	return nil
}

func (s *releaseCacheStub) AddRefresh(context.Context) (bool, error) { return false, nil }

type releaseProbeStub struct {
	releases []instanceprobe.Release
	fetched  bool
}

func (p *releaseProbeStub) FetchReleases(context.Context) ([]instanceprobe.Release, error) {
	p.fetched = true
	return p.releases, nil
}

func (p *releaseProbeStub) VerifyIntegration(context.Context, string, map[string]any) (*instanceprobe.Result, error) {
	return nil, nil
}

func (p *releaseProbeStub) SMTP(string) smtpsend.Server { return smtpsend.Server{} }

func releaseOutbound(cache *releaseCacheStub, probes *releaseProbeStub) *InstanceOutbound {
	admin := NewInstanceAdmin(nil, nil, InstanceAdminEnvironment{Version: "v0.0.0"}, nil)
	return NewInstanceOutbound(admin, nil, cache, probes, time.Now)
}

// A cached catalog with values normalize_release copies is served as DRF's
// JSONRenderer body, without fetching the feed.
func TestReleasesRendersCachedCatalog(t *testing.T) {
	release := instanceprobe.Release{ID: 1, Tag: "v2.0.0", Version: "2.0.0", Name: "R",
		PublishedAt: "2026-01-01T00:00:00Z", URL: "https://example.test/v2.0.0", ArtifactsReady: true}
	cache := &releaseCacheStub{releases: []instanceprobe.Release{release}, cached: true}
	probes := &releaseProbeStub{}
	body, err := releaseOutbound(cache, probes).Releases(context.Background(), "1", true, "")
	if err != nil {
		t.Fatal(err)
	}
	if probes.fetched {
		t.Fatal("a cached catalog fetched the feed")
	}
	want := `{"repository":"carbogninalberto/assozeta","latest":{"id":1,"tag":"v2.0.0","version":"2.0.0",` +
		`"name":"R","published_at":"2026-01-01T00:00:00Z","notes":"","url":"https://example.test/v2.0.0",` +
		`"artifacts_ready":true,"unavailable_reason":null},"relation":"behind","pending":[{"id":1,` +
		`"tag":"v2.0.0","version":"2.0.0","name":"R","published_at":"2026-01-01T00:00:00Z","notes":"",` +
		`"url":"https://example.test/v2.0.0","artifacts_ready":true,"unavailable_reason":null}],"history":` +
		`[{"id":1,"tag":"v2.0.0","version":"2.0.0","name":"R","published_at":"2026-01-01T00:00:00Z",` +
		`"notes":"","url":"https://example.test/v2.0.0","artifacts_ready":true,"unavailable_reason":null}],` +
		`"page":1,"next_page":null,"total":1}`
	if string(body) != want {
		t.Fatalf("body = %s, want %s", body, want)
	}
}

// A cached catalog with a value DRF cannot render is the exception Django lets
// escape: its 500 page (application state), not a handoff.
func TestReleasesUnrenderableValueIsAppState(t *testing.T) {
	release := instanceprobe.Release{ID: 1, Tag: "v2.0.0", Version: "2.0.0", NameValue: math.NaN(),
		PublishedAt: "2026-01-01T00:00:00Z", URL: "https://example.test/v2.0.0", ArtifactsReady: true}
	cache := &releaseCacheStub{releases: []instanceprobe.Release{release}, cached: true}
	body, err := releaseOutbound(cache, &releaseProbeStub{}).Releases(context.Background(), "1", true, "")
	if body != nil || !errors.Is(err, instanceprobe.ErrReleaseRender) || !errors.Is(err, domain.ErrInstanceAdminUnexpected) {
		t.Fatalf("Releases = %q, %v; want the render app state", body, err)
	}
	if reason, ok := refusal.AppStateReason(err); !ok || !strings.Contains(reason, "instance administration") {
		t.Fatalf("app state reason = %q, %v", reason, ok)
	}
}
