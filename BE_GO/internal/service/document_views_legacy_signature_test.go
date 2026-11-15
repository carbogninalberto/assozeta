package service

import (
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

// TestLegacySignatureFetcher is requests.get(url, timeout=10): the body after
// urllib3's content decoding, an error for 4xx/5xx, redirects followed, and a
// transport error when nothing accepts the connection.
func TestLegacySignatureFetcher(t *testing.T) {
	png := []byte("\x89PNG\r\n\x1a\nlegacy signature")
	var gzipped bytes.Buffer
	gz := gzip.NewWriter(&gzipped)
	if _, err := gz.Write(png); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/signature.png":
			w.Header().Set("Content-Type", "image/png")
			_, _ = w.Write(png)
		case "/gzip.png":
			w.Header().Set("Content-Encoding", "gzip")
			_, _ = w.Write(gzipped.Bytes())
		case "/redirect/1":
			http.Redirect(w, r, "/redirect/2", http.StatusFound)
		case "/redirect/2":
			http.Redirect(w, r, "/redirect/3", http.StatusFound)
		case "/redirect/3":
			http.Redirect(w, r, "/signature.png", http.StatusFound)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	fetcher := NewLegacySignatureFetcher()
	cases := []struct {
		name string
		url  string
		want []byte
		ok   bool
	}{
		{name: "200 png", url: server.URL + "/signature.png", want: png, ok: true},
		{name: "gzip body", url: server.URL + "/gzip.png", want: png, ok: true},
		{name: "redirect chain", url: server.URL + "/redirect/1", want: png, ok: true},
		{name: "404", url: server.URL + "/missing.png"},
		{name: "unreachable", url: "http://127.0.0.1:9/x.png"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			body, err := fetcher.Fetch(context.Background(), test.url)
			if !test.ok {
				if err == nil {
					t.Fatalf("fetch succeeded with %q, want an error", body)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(body, test.want) {
				t.Errorf("body %q, want %q", body, test.want)
			}
		})
	}
}

// The fetcher sends requests' default headers and follows at most 30 redirects.
func TestLegacySignatureFetcherHeadersAndRedirectLimit(t *testing.T) {
	var seen http.Header
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = r.Header.Clone()
		http.Redirect(w, r, r.URL.Path, http.StatusFound)
	}))
	defer server.Close()
	_, err := NewLegacySignatureFetcher().Fetch(context.Background(), server.URL+"/loop.png")
	if err == nil {
		t.Fatal("a redirect loop succeeded, want TooManyRedirects")
	}
	if seen.Get("User-Agent") != "python-requests/2.34.2" || seen.Get("Accept") != "*/*" ||
		seen.Get("Accept-Encoding") != "gzip, deflate" || seen.Get("Connection") != "keep-alive" {
		t.Errorf("headers %v", seen)
	}
}

// A gzip body is read through the coding, not passed through as bytes.
func TestLegacySignatureFetcherDecodesGzip(t *testing.T) {
	var gzipped bytes.Buffer
	gz := gzip.NewWriter(&gzipped)
	_, _ = io.WriteString(gz, "signature")
	_ = gz.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Encoding", "gzip")
		_, _ = w.Write(gzipped.Bytes())
	}))
	defer server.Close()
	body, err := NewLegacySignatureFetcher().Fetch(context.Background(), server.URL+"/signature.png")
	if err != nil || string(body) != "signature" {
		t.Fatalf("body %q, err %v", body, err)
	}
}
