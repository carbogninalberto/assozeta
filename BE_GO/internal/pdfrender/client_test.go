package pdfrender

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// testdata/django_values.json: Content-Disposition values as Django 5.2's
// HttpResponse stores them (latin-1 bytes or email.header MIME words), and
// json.dumps of the FastRender payload as the document-render contract
// authors it (the HTTP family checks Django's own request bytes against it).
type goldenValues struct {
	Headers []struct {
		Text  string `json:"text"`
		Bytes string `json:"bytes_b64"`
	} `json:"headers"`
	Payloads []struct {
		Host          string  `json:"host"`
		URL           string  `json:"url"`
		Authorization *string `json:"authorization"`
		Body          string  `json:"body_b64"`
	} `json:"payloads"`
}

func loadGolden(t *testing.T) goldenValues {
	t.Helper()
	raw, err := os.ReadFile("testdata/django_values.json")
	if err != nil {
		t.Fatal(err)
	}
	var values goldenValues
	if err := json.Unmarshal(raw, &values); err != nil {
		t.Fatal(err)
	}
	return values
}

func TestHeaderBytesMatchesDjango(t *testing.T) {
	for _, item := range loadGolden(t).Headers {
		want, _ := base64.StdEncoding.DecodeString(item.Bytes)
		if got := HeaderBytes(item.Text); got != string(want) {
			t.Errorf("%q: got %q want %q", item.Text, got, want)
		}
	}
}

func TestPayloadMatchesJSONDumps(t *testing.T) {
	for _, item := range loadGolden(t).Payloads {
		want, _ := base64.StdEncoding.DecodeString(item.Body)
		if got := Payload(item.Host, item.URL, item.Authorization); !bytes.Equal(got, want) {
			t.Errorf("%q: got\n%s\nwant\n%s", item.URL, got, want)
		}
	}
}

func TestHeaderSafe(t *testing.T) {
	for text, want := range map[string]bool{"a\tb.pdf": true, "Società – €.pdf": true, "a\nb": false, "a\rb": false, "a\x00b": false,
		"a\x7fb": false, "a b": false, "a\u0085b": false} {
		if HeaderSafe(text) != want {
			t.Errorf("%q: want %v", text, want)
		}
	}
}

func TestRenderAcceptanceRules(t *testing.T) {
	var gzipped bytes.Buffer
	writer := gzip.NewWriter(&gzipped)
	_, _ = writer.Write([]byte("%PDF-1.7 zipped"))
	_ = writer.Close()
	cases := []struct {
		name, contentType, encoding string
		status                      int
		body                        []byte
		pdf                         string
		failure                     string
	}{
		{"pdf", "application/pdf", "", 200, []byte("%PDF-1.7 x"), "%PDF-1.7 x", ""},
		{"created", "application/pdf; charset=binary", "", 201, []byte("%PDF-x"), "%PDF-x", ""},
		{"gzip", "application/pdf", "gzip", 200, gzipped.Bytes(), "%PDF-1.7 zipped", ""},
		{"error latin-1 text", "text/plain", "", 500, []byte("caf\xe9"), "", "Renderer returned HTTP 500: café"},
		{"error utf-8", "text/plain; charset=utf-8", "", 502, []byte(strings.Repeat("è", 600)), "",
			"Renderer returned HTTP 502: " + strings.Repeat("è", 500)},
		{"error json", "application/json", "", 400, []byte(`{"error": "è"}`), "", `Renderer returned HTTP 400: {"error": "è"}`},
		{"html", "text/html", "", 200, []byte("%PDF-1.7"), "", "Renderer returned invalid PDF data (content-type: text/html)"},
		{"no magic", "application/pdf", "", 200, []byte("PDF"), "", "Renderer returned invalid PDF data (content-type: application/pdf)"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			var received *http.Request
			var body []byte
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				received = r
				body, _ = io.ReadAll(r.Body)
				if test.contentType != "" {
					w.Header().Set("Content-Type", test.contentType)
				}
				if test.encoding != "" {
					w.Header().Set("Content-Encoding", test.encoding)
				}
				w.WriteHeader(test.status)
				_, _ = w.Write(test.body)
			}))
			defer server.Close()
			host, port, _ := strings.Cut(strings.TrimPrefix(server.URL, "http://"), ":")
			authorization := "Bearer token"
			pdf, err := New(host, port, "http://app").Render(context.Background(), "document/x/1/view", &authorization)
			if received.Method != http.MethodPost || received.URL.Path != "/render" || received.Header.Get("User-Agent") != RequestsUserAgent ||
				received.Header.Get("Accept-Encoding") != "gzip, deflate" || received.Header.Get("Content-Type") != "application/json" ||
				!bytes.Equal(body, Payload("http://app", "document/x/1/view", &authorization)) {
				t.Fatalf("request: %v %v %v", received.Method, received.URL, received.Header)
			}
			var failure *Failure
			if test.failure != "" {
				if !errors.As(err, &failure) || failure.Text != test.failure {
					t.Fatalf("got %v want failure %q", err, test.failure)
				}
				return
			}
			if err != nil || string(pdf) != test.pdf {
				t.Fatalf("got %q %v", pdf, err)
			}
		})
	}
}
