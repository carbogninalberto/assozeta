package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestSubscriptionCreateFormQuickOwnerImmutableQueryDict covers
// _prepare_subscription_request's owner quick branch on a form body: DRF hands
// the view an immutable QueryDict unless a multipart body carried a file part,
// so data['sport_association'] = ... raises before the view reads any key. The
// answer is the 500 page and nothing is written (no store call is made).
func TestSubscriptionCreateFormQuickOwnerImmutableQueryDict(t *testing.T) {
	creates := &SubscriptionCreates{now: func() time.Time { return time.Date(2026, 11, 12, 0, 0, 0, 0, time.UTC) }}
	identity := domain.ResolvedIdentity{
		Actor:     &domain.Account{ID: "actor", Role: domain.RoleAssociation},
		Effective: &domain.Account{ID: "actor", Role: domain.RoleAssociation},
		ScopeUser: &domain.Account{ID: "owner", Role: domain.RoleAssociation},
	}
	mode := "only_associate"
	for _, data := range []domain.RequestObject{
		{{Name: "associate", Value: "associate-id"}},
		{},
	} {
		_, err := creates.Add(context.Background(), identity, SubscriptionCreateRequest{Body: data, Form: true, Mode: &mode})
		if !errors.Is(err, domain.ErrSubscriptionCreateServer) {
			t.Fatalf("body %v: error %v is not the 500 page", data, err)
		}
	}
	// A multipart body with a file part is the mutable copy DRF builds: it does
	// not take this branch (the quick flow's store work is exercised natively).
	data := domain.RequestObject{
		{Name: "associate", Value: "associate-id"},
		{Name: "attachment", Value: domain.RequestUpload{Name: "nota.txt", Size: 7, Content: "allegato", Whole: true}},
	}
	if !data.HasUpload() {
		t.Fatal("the multipart body's file part is not marked as an upload")
	}
}

// TestSubscriptionCreateFormAssociateDataNotAnObject covers create_subscription's
// first read, data['associate_data']['born_date'], on a form value: a string is a
// TypeError and a multipart file part names Django's upload class. Both are the
// 500 page and are raised before any write (the transaction is nil here).
func TestSubscriptionCreateFormAssociateDataNotAnObject(t *testing.T) {
	creates := &SubscriptionCreates{now: func() time.Time { return time.Date(2026, 11, 12, 0, 0, 0, 0, time.UTC) }}
	cases := []struct {
		name  string
		value any
		want  string
	}{
		{name: "string", value: "b", want: "string indices must be integers, not 'str'"},
		{name: "memory upload", value: domain.RequestUpload{Name: "a.json", Size: 2, Content: "{}", Whole: true},
			want: "'InMemoryUploadedFile' object is not subscriptable"},
		{name: "temporary upload", value: domain.RequestUpload{Name: "a.json", Size: 1 << 22, Temp: true},
			want: "'TemporaryUploadedFile' object is not subscriptable"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			data := domain.RequestObject{{Name: "associate_data", Value: test.value}}
			_, err := creates.createSubscription(context.Background(), nil, domain.ResolvedIdentity{}, data, SubscriptionCreateRequest{})
			var failure *domain.SubscriptionCreateFailure
			if !errors.As(err, &failure) || failure.Text != test.want {
				t.Fatalf("error %v, want the TypeError %q", err, test.want)
			}
			if !errors.Is(err, domain.ErrSubscriptionCreateServer) {
				t.Fatalf("error %v is not the 500 page", err)
			}
		})
	}
}
