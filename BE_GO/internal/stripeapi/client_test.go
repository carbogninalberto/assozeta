package stripeapi

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestEncodeMatchesStripePython(t *testing.T) {
	// urlencode(list(_api_encode(params, 'V1'))).replace('%5B', '[').replace('%5D', ']') in stripe 11.6.0.
	params := []Param{{Key: "amount", Value: int64(1050)}, {Key: "currency", Value: "eur"},
		{Key: "description", Value: "Mar-Ros-€10.00|a b/c&d=e"}, {Key: "skipped", Value: nil},
		{Key: "payment_method_types", Value: []any{"card", json.Number("7"), true, nil}},
		{Key: "metadata", Value: domain.RequestObject{{Name: "k[1]", Value: "10.00"}, {Name: "n", Value: nil}}},
		{Key: "items", Value: []any{domain.RequestObject{{Name: "price", Value: "p_1"}}}}}
	got, err := Encode(params)
	want := "amount=1050&currency=eur&description=Mar-Ros-%E2%82%AC10.00%7Ca+b%2Fc%26d%3De" +
		"&payment_method_types[0]=card&payment_method_types[1]=7&payment_method_types[2]=True&payment_method_types[3]=None" +
		"&metadata[k[1]]=10.00&items[0][price]=p_1"
	if err != nil || got != want {
		t.Fatalf("Encode = %q, %v; want %q", got, err, want)
	}
	if _, err := Encode([]Param{{Key: "x", Value: []any{[]any{"a"}}}}); !errors.Is(err, ErrNotPorted) {
		t.Fatalf("nested list: %v", err)
	}
	if _, err := Encode([]Param{{Key: "x", Value: json.Number("1.5")}}); !errors.Is(err, ErrNotPorted) {
		t.Fatalf("float: %v", err)
	}
}

func TestPythonInt(t *testing.T) {
	for text, want := range map[string]string{" 12 ": "12", "+7": "7", "-3": "-3", "1_000": "1000", "١٢": "12", "𝟗": "9"} {
		value, ok := pythonInt(text)
		if !ok || value.String() != want {
			t.Fatalf("pythonInt(%q) = %v, %v", text, value, ok)
		}
	}
	for _, text := range []string{"", "_1", "1_", "1__2", "0x1", "1.0", "soon", "+"} {
		if _, ok := pythonInt(text); ok {
			t.Fatalf("pythonInt(%q) accepted", text)
		}
	}
}

func sign(secret, stamp string, payload []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(stamp + "."))
	mac.Write(payload)
	return hex.EncodeToString(mac.Sum(nil))
}

func TestConstructEvent(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	payload := []byte(`{"type": "charge.succeeded", "type": "payout.paid"}`)
	stamp := strconv.FormatInt(now.Unix(), 10)
	good := sign("whsec_placeholder", stamp, payload)
	header := func(text string) *string { return &text }
	event, err := ConstructEvent(payload, header("t="+stamp+",v1=bad,v1="+good), "whsec_placeholder", now)
	if err != nil {
		t.Fatal(err)
	}
	if kind, _ := event.Lookup("type"); kind != "payout.paid" {
		t.Fatalf("duplicate keys: last wins, got %v", kind)
	}
	old := strconv.FormatInt(now.Unix()-301, 10)
	cases := map[string]error{
		"":                   ErrWebhookSignature,
		"t=" + stamp:         ErrWebhookSignature,
		"t,v1=" + good:       ErrWebhookSignature,
		"t=" + stamp + ",v1": ErrWebhookSignature,
		"t=" + old + ",v1=" + sign("whsec_placeholder", old, payload): ErrWebhookSignature,
		"t=" + stamp + ",v1=è":                 ErrWebhookUnhandled,
		"t=" + stamp + ",v1=" + good + ",v1=è": nil,
		"t= " + stamp + " ,v1=" + good:         nil,
	}
	for text, want := range cases {
		if _, err := ConstructEvent(payload, header(text), "whsec_placeholder", now); !errors.Is(err, want) && !(want == nil && err == nil) {
			t.Fatalf("header %q: %v, want %v", text, err, want)
		}
	}
	if _, err := ConstructEvent(payload, nil, "whsec_placeholder", now); !errors.Is(err, ErrWebhookSignature) {
		t.Fatalf("missing header: %v", err)
	}
	if _, err := ConstructEvent([]byte{0xff}, header("t="+stamp+",v1="+good), "whsec_placeholder", now); !errors.Is(err, ErrWebhookPayload) {
		t.Fatalf("invalid UTF-8: %v", err)
	}
	list := []byte(`[]`)
	if _, err := ConstructEvent(list, header("t="+stamp+",v1="+sign("whsec_placeholder", stamp, list)), "whsec_placeholder", now); !errors.Is(err, ErrWebhookUnhandled) {
		t.Fatalf("non-object event: %v", err)
	}
}

func TestSessionRetriesWithTheSameKeyAndSendsTelemetry(t *testing.T) {
	var mu sync.Mutex
	var seen []http.Header
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = append(seen, r.Header.Clone())
		count := len(seen)
		mu.Unlock()
		w.Header().Set("Request-Id", fmt.Sprintf("req_%d", count))
		if count == 1 {
			w.WriteHeader(500)
			_, _ = w.Write([]byte(`{"error": {"type": "api_error"}}`))
			return
		}
		if count == 3 {
			w.WriteHeader(404)
			_, _ = w.Write([]byte(`{"error": {"type": "invalid_request_error", "code": "resource_missing"}}`))
			return
		}
		_, _ = w.Write([]byte(`{"id": "pi_1", "status": "requires_payment_method"}`))
	}))
	defer server.Close()
	client := NewClient(server.URL)
	client.sleep = func(context.Context, time.Duration) error { return nil }
	session := client.Session("sk_test_placeholder")
	if _, err := session.CreatePaymentIntent(context.Background(), []Param{{Key: "amount", Value: int64(1)}}); err != nil {
		t.Fatal(err)
	}
	_, err := session.RetrievePaymentIntent(context.Background(), "pi 1/2")
	var failure *Error
	if !errors.As(err, &failure) || !failure.InvalidRequest || failure.Status != 404 {
		t.Fatalf("retrieve: %v", err)
	}
	if len(seen) != 3 || seen[0].Get("Idempotency-Key") == "" || seen[0].Get("Idempotency-Key") != seen[1].Get("Idempotency-Key") {
		t.Fatalf("retry keys: %v", seen)
	}
	if seen[0].Get("X-Stripe-Client-Telemetry") != "" || seen[2].Get("Idempotency-Key") != "" {
		t.Fatalf("first request headers: %v", seen)
	}
	var telemetry struct {
		Metrics struct {
			ID       string `json:"request_id"`
			Duration *int64 `json:"request_duration_ms"`
		} `json:"last_request_metrics"`
	}
	if err := json.Unmarshal([]byte(seen[2].Get("X-Stripe-Client-Telemetry")), &telemetry); err != nil || telemetry.Metrics.ID != "req_2" ||
		telemetry.Metrics.Duration == nil {
		t.Fatalf("telemetry: %q", seen[2].Get("X-Stripe-Client-Telemetry"))
	}
	if seen[2].Get("Authorization") != "Bearer sk_test_placeholder" || seen[2].Get("Stripe-Version") != APIVersion {
		t.Fatalf("headers: %v", seen[2])
	}
}
