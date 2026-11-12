package service

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"testing"
)

type socialIDs struct {
	value     any
	err       error
	providers []string
}

func (s *socialIDs) ClientID(_ context.Context, provider string) (any, error) {
	s.providers = append(s.providers, provider)
	return s.value, s.err
}

type socialClient struct {
	token, audience any
	info            map[string]any
	valid           bool
	err             error
	calls           int
	cancel          context.CancelFunc
}

func (c *socialClient) Verify(_ context.Context, token, audience any) (map[string]any, bool, error) {
	c.calls++
	c.token = token
	c.audience = audience
	if c.cancel != nil {
		c.cancel()
	}
	return c.info, c.valid, c.err
}

func TestSocialProvidersCurrentConfigurationAndDispatch(t *testing.T) {
	ids := &socialIDs{value: "first"}
	google := &socialClient{valid: true, info: map[string]any{"email": "google@example.test"}}
	apple := &socialClient{valid: true, info: map[string]any{"email": "apple@example.test"}}
	s := NewSocialProviders(ids, google, apple)
	token := map[string]any{"raw": json.Number("17")}
	for _, tc := range []struct {
		provider string
		client   *socialClient
		aud      any
		email    string
	}{
		{"google", google, "first", "google@example.test"},
		{"apple", apple, []any{"second", "third"}, "apple@example.test"},
		{"google", google, "changed", "google@example.test"},
	} {
		ids.value = tc.aud
		identity, valid, err := s.Verify(context.Background(), tc.provider, token)
		if err != nil || !valid || identity.Email != tc.email || !reflect.DeepEqual(tc.client.token, token) || !reflect.DeepEqual(tc.client.audience, tc.aud) {
			t.Fatal("current provider input changed")
		}
	}
	if !reflect.DeepEqual(ids.providers, []string{"google", "apple", "google"}) || google.calls != 2 || apple.calls != 1 {
		t.Fatal("provider dispatch/read count")
	}
	for _, disabled := range []any{nil, "", false, json.Number("0"), []any{}, map[string]any{}} {
		ids.value = disabled
		if identity, valid, err := s.Verify(context.Background(), "apple", token); err != nil || valid || identity.Email != nil || apple.calls != 1 {
			t.Fatal("disabled configuration called provider")
		}
	}
}

func TestSocialProvidersFailureAndCancellation(t *testing.T) {
	failure := errors.New("synthetic failure")
	for _, stage := range []string{"configuration", "provider", "invalid", "missing email", "canceled entry", "canceled provider", "unknown provider"} {
		t.Run(stage, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			ids := &socialIDs{value: "client"}
			client := &socialClient{valid: true, info: map[string]any{"email": "valid@example.test"}}
			provider := "apple"
			wantErr := error(nil)
			wantValid := false
			switch stage {
			case "configuration":
				ids.err = failure
				wantErr = failure
			case "provider":
				client.err = failure
				wantErr = failure
			case "invalid":
				client.valid = false
			case "missing email":
				client.info = nil
				wantValid = true
			case "canceled entry":
				cancel()
				wantErr = context.Canceled
			case "canceled provider":
				client.cancel = cancel
				wantErr = context.Canceled
			case "unknown provider":
				provider = "other"
				wantErr = ErrSocialConfiguration
			}
			identity, valid, err := NewSocialProviders(ids, client, client).Verify(ctx, provider, "token")
			if !errors.Is(err, wantErr) || valid != wantValid || identity.Email != nil {
				t.Fatal("failure exposed a successful identity", valid, err)
			}
			if (stage == "configuration" || stage == "canceled entry" || stage == "unknown provider") && client.calls != 0 {
				t.Fatal("failure contacted provider")
			}
		})
	}
}
