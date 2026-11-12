package instanceprobe

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"testing"
)

// goldenFile is TESTS/adapters/go/requests_url_golden.py's output, produced
// with requests 2.34.2 / urllib3 2.8.0 of the pinned interpreter.
type goldenFile struct {
	Requests string       `json:"requests"`
	Urllib3  string       `json:"urllib3"`
	Django   string       `json:"django"`
	Cases    []goldenCase `json:"cases"`
}

type goldenCase struct {
	Input       string  `json:"input"`
	Stored      *string `json:"stored"`
	Why         string  `json:"why"`
	Answer      string  `json:"answer"`
	URL         string  `json:"url"`
	Scheme      string  `json:"scheme"`
	ConnectHost string  `json:"connect_host"`
	Port        int     `json:"port"`
	HostHeader  string  `json:"host_header"`
	Target      string  `json:"target"`
}

func TestRequestsURLGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/requests_url_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden goldenFile
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if golden.Requests != RequestsVersion {
		t.Fatalf("requests version %q, this port sends %q", golden.Requests, RequestsVersion)
	}
	rows := 0
	for _, test := range golden.Cases {
		if test.Stored == nil {
			continue
		}
		rows++
		request, outcome, err := prepareAIRequest(*test.Stored)
		if err != nil {
			t.Fatalf("%q: %v", test.Input, err)
		}
		switch test.Answer {
		case "invalid":
			if outcome != aiRequestInvalid {
				t.Errorf("%q: outcome %v, golden invalid", test.Input, outcome)
			}
		case "raises":
			if outcome != aiRequestRaises {
				t.Errorf("%q: outcome %v, golden raises", test.Input, outcome)
			}
		case "request":
			if outcome != aiRequestReady || request == nil {
				t.Errorf("%q: outcome %v, golden request", test.Input, outcome)
				continue
			}
			if request.URL != test.URL || request.Scheme != test.Scheme || request.ConnectHost != test.ConnectHost ||
				request.Port != test.Port || request.HostHeader != test.HostHeader || request.Target != test.Target {
				t.Errorf("%q:\n got  %#v\n want url=%q scheme=%q host=%q port=%d header=%q target=%q",
					test.Input, request, test.URL, test.Scheme, test.ConnectHost, test.Port, test.HostHeader, test.Target)
			}
		default:
			t.Fatalf("%q: unknown golden answer %q", test.Input, test.Answer)
		}
	}
	if rows == 0 {
		t.Fatal("golden has no stored rows")
	}
}

func TestIntegrationTargetOutcomes(t *testing.T) {
	value := func(base string) map[string]any { return map[string]any{"api_key": "k", "base_url": base} }
	if target, invalid, err := IntegrationTarget("ai", value(`https://API.example.com/a"b`)); err != nil || invalid ||
		target != "https://api.example.com/a%22b/models" {
		t.Fatalf("quoted: target=%q invalid=%v err=%v", target, invalid, err)
	}
	// A port out of range raises in requests: no target, but not invalid.
	if target, invalid, err := IntegrationTarget("ai", value("https://api.example.com:65536/v1")); err != nil || invalid || target != "" {
		t.Fatalf("port: target=%q invalid=%v err=%v", target, invalid, err)
	}
	if _, invalid, err := IntegrationTarget("ai", value("https://user@api.example.com/v1")); err != nil || !invalid {
		t.Fatalf("user info: invalid=%v err=%v", invalid, err)
	}
	if _, _, err := IntegrationTarget("ai", value("https://:@api.example.com/v1")); err != ErrProbeNotPorted {
		t.Fatalf("empty user info: err=%v", err)
	}
}

// TestGetTargetSendsPreparedBytes proves the HTTP client sends the computed
// request line and Host header instead of net/url's re-encoding of Path.
func TestGetTargetSendsPreparedBytes(t *testing.T) {
	const body = `{"object":"list","data":[]}`
	type sent struct{ uri, host string }
	seen := make(chan sent, 8)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen <- sent{uri: r.RequestURI, host: r.Host}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, body)
	}))
	defer server.Close()

	client := &Client{Proxy: func(*http.Request) (*url.URL, error) { return nil, nil }}
	paths := []string{
		`/a"b{}`,
		`/a%7eb%2fc`,
		`/a/../v1`,
		`/a//../b`,
		`//a///b`,
		"/à/è",
		`/%41%7e%2d`,
	}
	for _, path := range paths {
		base := server.URL + path
		request, outcome, err := prepareAIRequest(base)
		if err != nil || outcome != aiRequestReady {
			t.Fatalf("%q: %v %v", base, outcome, err)
		}
		response, err := client.GetTarget(context.Background(), request, nil, maxProbeBody)
		if err != nil {
			t.Fatalf("%q: %v", base, err)
		}
		if response.Status != 200 || string(response.Body) != body {
			t.Fatalf("%q: status %d body %q", base, response.Status, response.Body)
		}
		got := <-seen
		if got.uri != request.Target {
			t.Errorf("%q: request line %q, prepared target %q", base, got.uri, request.Target)
		}
		if got.host != request.HostHeader {
			t.Errorf("%q: Host %q, prepared header %q", base, got.host, request.HostHeader)
		}
	}
}
