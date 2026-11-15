package service

import (
	"context"
	"encoding/base64"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// signatureDocumentViewStore serves the one subscription and association of
// the signature tests; every other store method is unused.
type signatureDocumentViewStore struct {
	domain.DocumentViewStore
	subscription *domain.ViewSubscription
}

func (s signatureDocumentViewStore) Subscription(context.Context, string, domain.RenderScope) (*domain.ViewSubscription, error) {
	return s.subscription, nil
}

func (s signatureDocumentViewStore) Association(context.Context, string) (*domain.ViewAssociation, error) {
	sections := "[]"
	return &domain.ViewAssociation{Denomination: "Associazione", TaxCode: "01234567890", AdditionalSections: &sections}, nil
}

// signatureDocumentViewObjects is default_storage's read of the seeded names.
type signatureDocumentViewObjects struct {
	content map[string][]byte
}

func (o signatureDocumentViewObjects) Read(_ context.Context, name string) ([]byte, error) {
	body, ok := o.content[name]
	if !ok {
		return nil, errors.New("no such object")
	}
	return body, nil
}

func signatureText(value string) *string { return &value }

// signatureFetcherFunc is LegacySignatureFetcher with a fixed reply.
type signatureFetcherFunc func(context.Context, string) ([]byte, error)

func (f signatureFetcherFunc) Fetch(ctx context.Context, url string) ([]byte, error) {
	return f(ctx, url)
}

// TestDocumentViewsSubscriptionSignature is Subscription.get_signature_base64:
// a readable stored object is a PNG data URI, a failed read falls back to the
// legacy URL, a failed legacy fetch or no URL is None, and reading storage
// without a configured reader is app state.
func TestDocumentViewsSubscriptionSignature(t *testing.T) {
	const uid = "a1000000-0000-4000-8000-000000000046"
	const storedName = "signatures/stored.png"
	const legacyURL = "https://legacy.example.test/signature.png"
	body := []byte("signature fixture bytes")
	objects := signatureDocumentViewObjects{content: map[string][]byte{storedName: body}}
	fetched := signatureFetcherFunc(func(context.Context, string) ([]byte, error) { return body, nil })
	failing := signatureFetcherFunc(func(context.Context, string) ([]byte, error) { return nil, errors.New("connection refused") })
	subscription := func(key, url *string) *domain.ViewSubscription {
		return &domain.ViewSubscription{ID: uid, Association: "a1000000-0000-4000-8000-000000000001",
			CreationDate: time.Date(2026, 3, 4, 10, 0, 0, 0, time.UTC), Type: 2, Role: 5,
			SignatureStorageKey: key, SignatureURL: url}
	}
	cases := []struct {
		name         string
		subscription *domain.ViewSubscription
		objects      domain.DocumentViewObjects
		fetcher      LegacySignatureFetcher
		want         string
		refused      error
	}{
		{name: "stored object", subscription: subscription(signatureText(storedName), nil), objects: objects,
			want: `src="data:image/png;base64,` + base64.StdEncoding.EncodeToString(body) + `"`},
		{name: "missing object without url", subscription: subscription(signatureText("signatures/missing.png"), nil), objects: objects,
			want: `src="None"`},
		{name: "missing object with url", subscription: subscription(signatureText("signatures/missing.png"), signatureText(legacyURL)),
			objects: objects, fetcher: fetched, want: `src="data:image/png;base64,` + base64.StdEncoding.EncodeToString(body) + `"`},
		{name: "url without key", subscription: subscription(nil, signatureText(legacyURL)), objects: objects, fetcher: fetched,
			want: `src="data:image/png;base64,` + base64.StdEncoding.EncodeToString(body) + `"`},
		{name: "url failing", subscription: subscription(nil, signatureText(legacyURL)), objects: objects, fetcher: failing,
			want: `src="None"`},
		{name: "stored object without reader", subscription: subscription(signatureText(storedName), nil), refused: errDocumentViewSignature},
		{name: "url without fetcher", subscription: subscription(nil, signatureText(legacyURL)), refused: domain.ErrServerComposition},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			views := NewDocumentViews(signatureDocumentViewStore{subscription: test.subscription}, nil, "", time.Now)
			if test.objects != nil {
				views = views.WithSignatureObjects(test.objects)
			}
			if test.fetcher != nil {
				views = views.WithLegacySignatureFetcher(test.fetcher)
			}
			reply, err := views.Render(context.Background(), domain.ResolvedIdentity{},
				DocumentViewRequest{View: ViewSubscription, UID: uid, Language: "en"})
			if test.refused != nil {
				if !errors.Is(err, test.refused) {
					t.Fatalf("error %v, want %v", err, test.refused)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if reply.Status != 200 || reply.HTML == nil {
				t.Fatalf("reply %+v, want the rendered page", reply)
			}
			if !strings.Contains(*reply.HTML, test.want) {
				t.Errorf("page misses %q:\n%s", test.want, *reply.HTML)
			}
		})
	}
}
