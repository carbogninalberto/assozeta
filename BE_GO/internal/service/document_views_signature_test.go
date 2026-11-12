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

// TestDocumentViewsSubscriptionSignature is Subscription.get_signature_base64:
// a readable stored object is a PNG data URI, a failed read without the
// legacy URL is None, and every case that would fetch the legacy URL (or
// read storage without a configured reader) stays refused.
func TestDocumentViewsSubscriptionSignature(t *testing.T) {
	const uid = "a1000000-0000-4000-8000-000000000046"
	const storedName = "signatures/stored.png"
	const legacyURL = "https://legacy.example.test/signature.png"
	body := []byte("signature fixture bytes")
	objects := signatureDocumentViewObjects{content: map[string][]byte{storedName: body}}
	subscription := func(key, url *string) *domain.ViewSubscription {
		return &domain.ViewSubscription{ID: uid, Association: "a1000000-0000-4000-8000-000000000001",
			CreationDate: time.Date(2026, 3, 4, 10, 0, 0, 0, time.UTC), Type: 2, Role: 5,
			SignatureStorageKey: key, SignatureURL: url}
	}
	cases := []struct {
		name         string
		subscription *domain.ViewSubscription
		objects      domain.DocumentViewObjects
		want         string
		refused      bool
	}{
		{name: "stored object", subscription: subscription(signatureText(storedName), nil), objects: objects,
			want: `src="data:image/png;base64,` + base64.StdEncoding.EncodeToString(body) + `"`},
		{name: "missing object without url", subscription: subscription(signatureText("signatures/missing.png"), nil), objects: objects,
			want: `src="None"`},
		{name: "missing object with url", subscription: subscription(signatureText("signatures/missing.png"), signatureText(legacyURL)),
			objects: objects, refused: true},
		{name: "url without key", subscription: subscription(nil, signatureText(legacyURL)), objects: objects, refused: true},
		{name: "stored object without reader", subscription: subscription(signatureText(storedName), nil), refused: true},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			views := NewDocumentViews(signatureDocumentViewStore{subscription: test.subscription}, nil, "", time.Now)
			if test.objects != nil {
				views = views.WithSignatureObjects(test.objects)
			}
			reply, err := views.Render(context.Background(), domain.ResolvedIdentity{},
				DocumentViewRequest{View: ViewSubscription, UID: uid, Language: "en"})
			if test.refused {
				if !errors.Is(err, ErrDocumentViewNotPorted) {
					t.Fatalf("error %v, want the signature refusal", err)
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
