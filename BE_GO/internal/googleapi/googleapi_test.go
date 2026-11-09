package googleapi

import (
	"testing"
	"time"
)

func TestQuoteMatchesPython(t *testing.T) {
	// urllib.parse.quote(value, safe='') and quote_plus.
	if got := quote("a b/c@x~_.-+"); got != "a%20b%2Fc%40x~_.-%2B" {
		t.Fatal(got)
	}
	if got := quotePlus("page two/+è"); got != "page+two%2F%2B%C3%A8" {
		t.Fatal(got)
	}
}

func TestCredentialsRoundTrip(t *testing.T) {
	now := time.Date(2026, 11, 9, 8, 0, 0, 0, time.UTC)
	stored := `{"token": "t", "refresh_token": "r", "token_uri": "https://oauth2.googleapis.com/token", "client_id": "c", "client_secret": "s", ` +
		`"scopes": ["a", "b"], "universe_domain": "googleapis.com", "account": "", "expiry": "2026-11-09T09:00:00Z"}`
	credentials, err := LoadCredentials(stored, now)
	if err != nil {
		t.Fatal(err)
	}
	if credentials.Expired(now) || !credentials.Valid(now) {
		t.Fatal("expected valid credentials")
	}
	if got := credentials.ToJSON(); got != stored {
		t.Fatalf("to_json: %s", got)
	}
	// Within REFRESH_THRESHOLD (3 min 45 s) of the expiry the credentials are expired.
	if !credentials.Expired(time.Date(2026, 11, 9, 8, 56, 15, 0, time.UTC)) {
		t.Fatal("expected expired inside the threshold")
	}
	at := time.Date(2026, 11, 9, 9, 0, 0, 120000000, time.UTC)
	credentials.Expiry = &at
	if got := isoformat(at); got != "2026-11-09T09:00:00.120000" {
		t.Fatal(got)
	}
}

func TestLoadCredentialsFailures(t *testing.T) {
	now := time.Now()
	for _, stored := range []any{map[string]any{}, `[]`, `{"client_id": "c", "client_secret": "s"}`, `{"refresh_token": "r", "client_id": "c", ` +
		`"client_secret": "s", "expiry": "2026-02-30T00:00:00"}`} {
		if _, err := LoadCredentials(stored, now); err == nil {
			t.Fatalf("expected an error for %v", stored)
		}
	}
	credentials, err := LoadCredentials(`{"refresh_token": "r", "client_id": "c", "client_secret": "s", "scopes": "x y"}`, now)
	if err != nil || !credentials.Expired(now) || len(credentials.Scopes) != 2 {
		t.Fatalf("missing expiry must be expired: %v %v", credentials, err)
	}
}

func TestHTTPErrorMentions(t *testing.T) {
	cases := []struct {
		body, reason string
		want         bool
	}{
		{`{"error": {"code": 403, "message": "Rate", "errors": [{"reason": "quotaExceeded"}]}}`, "Forbidden", true},
		{`{"error": {"code": 403, "message": "quotaExceeded for user"}}`, "Forbidden", true},
		{`{"error": {"code": 403, "message": 5, "errors": [{"reason": "quotaExceeded"}]}}`, "Forbidden", false},
		{`<html>slow down</html>`, "quotaExceeded", true},
		{`{"error": {"code": 500, "message": "Backend Error"}}`, "quotaExceeded", false},
		{`[]`, "Forbidden", false},
	}
	for _, item := range cases {
		failure := &HTTPError{Status: 403, Reason: item.reason, Body: []byte(item.body), URI: APIHost + "/calendar/v3/calendars?alt=json"}
		if got := failure.Mentions("quotaExceeded"); got != item.want {
			t.Fatalf("%s: got %v", item.body, got)
		}
	}
}
