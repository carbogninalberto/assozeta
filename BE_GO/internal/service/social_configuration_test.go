package service

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type socialConfigurationReader struct {
	row   *domain.SocialConfiguration
	err   error
	calls int
}

func (r *socialConfigurationReader) SocialConfiguration(context.Context) (*domain.SocialConfiguration, error) {
	r.calls++
	return r.row, r.err
}

func TestSocialConfigurationPrecedence(t *testing.T) {
	for _, tc := range []struct {
		name, provider, integration, display string
		selfHosted, absent, failure          bool
		want                                 any
	}{
		{name: "no instance", provider: "google", absent: true, want: "env-google"},
		{name: "default display", provider: "google", selfHosted: true, integration: `{}`, display: `{}`, want: "env-google"},
		{name: "hidden google", provider: "google", selfHosted: true, integration: `{}`, display: `{"login":{"allowOauthLogin":false}}`, want: ""},
		{name: "apple ignores visibility", provider: "apple", selfHosted: true, integration: `{}`, display: `{"login":{"allowOauthLogin":false}}`, want: "env-apple"},
		{name: "hosted ignores overrides", provider: "google", integration: `{"google":{"settings":{"enabled":false}}}`, display: `{"login":{"allowOauthLogin":false}}`, want: "env-google"},
		{name: "override ignores visibility", provider: "google", selfHosted: true, integration: `{"google":{"settings":{"enabled":true,"client_id":"saved"}}}`, display: `{"login":{"allowOauthLogin":false}}`, want: "saved"},
		{name: "disabled missing client", provider: "apple", selfHosted: true, integration: `{"apple":{"settings":{"enabled":false}}}`, display: `{}`, want: ""},
		{name: "reset", provider: "google", selfHosted: true, integration: `{"google":{"settings":null,"revision":4}}`, display: `{}`, want: "env-google"},
		{name: "empty settings error", provider: "google", selfHosted: true, integration: `{"google":{"settings":{}}}`, display: `{}`, failure: true},
		{name: "enabled missing client error", provider: "apple", selfHosted: true, integration: `{"apple":{"settings":{"enabled":true}}}`, display: `{}`, failure: true},
		{name: "null client preserved", provider: "apple", selfHosted: true, integration: `{"apple":{"settings":{"enabled":true,"client_id":null}}}`, display: `{}`, want: nil},
		{name: "list audience preserved", provider: "google", selfHosted: true, integration: `{"google":{"settings":{"enabled":"false","client_id":["a","b"]}}}`, display: `{}`, want: []any{"a", "b"}},
		{name: "null integration fallback", provider: "google", selfHosted: true, integration: `null`, display: `{}`, want: "env-google"},
		{name: "empty list integration fallback", provider: "google", selfHosted: true, integration: `[]`, display: `{}`, want: "env-google"},
		{name: "invalid integration", provider: "google", selfHosted: true, integration: `[1]`, display: `{}`, failure: true},
		{name: "null provider record", provider: "google", selfHosted: true, integration: `{"google":null}`, display: `{}`, failure: true},
		{name: "null login", provider: "google", selfHosted: true, integration: `{}`, display: `{"login":null}`, failure: true},
		{name: "empty display fallback", provider: "google", selfHosted: true, integration: `{}`, display: `[]`, want: "env-google"},
		{name: "truthy visibility string", provider: "google", selfHosted: true, integration: `{}`, display: `{"login":{"allowOauthLogin":"false"}}`, want: "env-google"},
		{name: "empty visibility object", provider: "google", selfHosted: true, integration: `{}`, display: `{"login":{"allowOauthLogin":{}}}`, want: ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := &socialConfigurationReader{row: &domain.SocialConfiguration{SelfHosted: tc.selfHosted, Integrations: json.RawMessage(tc.integration), Display: json.RawMessage(tc.display)}}
			if tc.absent {
				r.row = nil
			}
			s := NewSocialClientIDs(r, "env-google", "env-apple")
			got, err := s.ClientID(context.Background(), tc.provider)
			if (err != nil) != tc.failure || !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("got %#v, %v; want %#v, failure %v", got, err, tc.want, tc.failure)
			}
			if r.calls != 1 {
				t.Fatalf("reads %d", r.calls)
			}
		})
	}
}

func TestSocialConfigurationReloadAndFailure(t *testing.T) {
	r := &socialConfigurationReader{}
	s := NewSocialClientIDs(r, "env-google", "")
	for _, tc := range []struct {
		row  *domain.SocialConfiguration
		want any
	}{
		{nil, "env-google"},
		{&domain.SocialConfiguration{SelfHosted: true, Integrations: json.RawMessage(`{"google":{"settings":{"enabled":true,"client_id":"changed"}}}`)}, "changed"},
		{&domain.SocialConfiguration{SelfHosted: true, Integrations: json.RawMessage(`{"google":{"settings":{"enabled":false}}}`)}, ""},
		{nil, "env-google"},
	} {
		r.row = tc.row
		got, err := s.ClientID(context.Background(), "google")
		if err != nil || got != tc.want {
			t.Fatalf("got %v %v", got, err)
		}
	}
	if r.calls != 4 {
		t.Fatal("cached mutable configuration")
	}
	r.err = errors.New("database unavailable")
	if got, err := s.ClientID(context.Background(), "google"); got != nil || !errors.Is(err, r.err) {
		t.Fatal("database failure fell back to environment")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	before := r.calls
	if got, err := s.ClientID(ctx, "google"); got != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("canceled read admitted")
	}
	if r.calls != before {
		t.Fatal("canceled request queried")
	}
}
