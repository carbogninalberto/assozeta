package service

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
)

type manualSourceFixture struct {
	preview  map[string]any
	err      error
	calls    []string
	features []string
	query    string
}

func (f *manualSourceFixture) PreviewSections(ctx context.Context, query string) (map[string]any, error) {
	f.calls = append(f.calls, "preview")
	f.query = query
	return f.preview, f.err
}
func (f *manualSourceFixture) Sections(ctx context.Context, features []string, query string) (map[string]any, error) {
	f.calls = append(f.calls, "sections")
	f.features = features
	f.query = query
	return map[string]any{"status": "verified", "results": []any{}, "message": ""}, f.err
}
func (f *manualSourceFixture) ReadAsset(ctx context.Context, features []string, path string) ([]byte, error) {
	f.calls = append(f.calls, "asset")
	f.features = features
	return []byte("verified"), f.err
}

type manualFeatureFixture struct {
	calls    int
	features []string
	err      error
}

func (f *manualFeatureFixture) ManualFeatures(context.Context) ([]string, error) {
	f.calls++
	return f.features, f.err
}
func manualIdentity() domain.ResolvedIdentity {
	a := &domain.Account{ID: "actor", Active: true}
	return domain.ResolvedIdentity{Actor: a, Effective: a, ScopeUser: a}
}
func TestManualReaderTrustedIdentityAndQueryAdmission(t *testing.T) {
	ctx := context.Background()
	source := &manualSourceFixture{}
	features := &manualFeatureFixture{}
	s := NewManualReader(features, source)
	if _, err := s.Sections(ctx, domain.ResolvedIdentity{}, ""); !errors.Is(err, domain.ErrInvalidScopedRequest) {
		t.Fatal("anonymous identity admitted", err)
	}
	if _, err := s.Sections(ctx, manualIdentity(), strings.Repeat("à", 2001)); !errors.Is(err, ErrManualQueryTooLong) {
		t.Fatal("query rune limit", err)
	}
	if len(source.calls) != 0 || features.calls != 0 {
		t.Fatal("rejected request accessed sources")
	}
	if _, err := s.Sections(ctx, manualIdentity(), "\x1c\u00a0caffè\u2003\x1f"); err != nil || source.query != "caffè" {
		t.Fatal("Python whitespace", source.query, err)
	}
}
func TestManualReaderPreviewAvoidsInstanceLookup(t *testing.T) {
	source := &manualSourceFixture{preview: map[string]any{"status": "development_preview", "results": []any{}, "message": ""}}
	features := &manualFeatureFixture{err: errors.New("must not query")}
	response, err := NewManualReader(features, source).Sections(context.Background(), manualIdentity(), "")
	if err != nil || response.Status != "development_preview" || features.calls != 0 || !reflect.DeepEqual(source.calls, []string{"preview"}) {
		t.Fatal("preview precedence", response, err)
	}
}
func TestManualReaderLivePublicFeaturesAndSafeUnavailable(t *testing.T) {
	source := &manualSourceFixture{}
	features := &manualFeatureFixture{features: []string{"self_hosted"}}
	s := NewManualReader(features, source)
	if _, err := s.Sections(context.Background(), manualIdentity(), ""); err != nil || features.calls != 1 || !reflect.DeepEqual(source.features, features.features) {
		t.Fatal("ordinary features", err)
	}
	features.features = nil
	if _, err := s.Sections(context.Background(), manualIdentity(), ""); err != nil || features.calls != 2 || len(source.features) != 0 {
		t.Fatal("cached features", err)
	}
	source.err = manual.ErrReaderUnavailable
	response, err := s.Sections(context.Background(), manualIdentity(), "")
	if err != nil || response.Status != "no_evidence" || response.Message != "Il manuale verificato non è disponibile per questa versione o deve essere aggiornato." {
		t.Fatal("safe unavailable", response, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if response, err = s.Sections(ctx, manualIdentity(), ""); response != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation hidden", response, err)
	}
}
func TestManualReaderAssetScopeAndPreviewNamespace(t *testing.T) {
	source := &manualSourceFixture{}
	features := &manualFeatureFixture{features: []string{"self_hosted"}}
	s := NewManualReader(features, source)
	if _, err := s.Asset(context.Background(), domain.ResolvedIdentity{}, "preview/a.png"); !errors.Is(err, domain.ErrInvalidScopedRequest) {
		t.Fatal("asset missing identity", err)
	}
	if _, err := s.Asset(context.Background(), manualIdentity(), "preview/a.png"); err != nil || features.calls != 0 {
		t.Fatal("preview asset loaded ordinary context", err)
	}
	if _, err := s.Asset(context.Background(), manualIdentity(), "images/a.png"); err != nil || features.calls != 1 || !reflect.DeepEqual(source.features, features.features) {
		t.Fatal("ordinary asset scope", err)
	}
}
