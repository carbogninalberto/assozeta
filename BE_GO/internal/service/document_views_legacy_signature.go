package service

import (
	"bytes"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"time"
)

// LegacySignatureFetcher is Subscription.get_signature_base64's legacy URL
// read: requests.get(url, timeout=10) with response.content returned.
type LegacySignatureFetcher interface {
	Fetch(ctx context.Context, url string) ([]byte, error)
}

// requestsSignatureFetcher is requests 2.34.2 / urllib3 2.8.0 with its default
// headers: up to 30 redirects, the environment's proxies, no transparent
// decompression (the body is decoded here like urllib3's content decoders).
type requestsSignatureFetcher struct{ client *http.Client }

// NewLegacySignatureFetcher builds the fetcher of the subscription page.
func NewLegacySignatureFetcher() LegacySignatureFetcher {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = http.ProxyFromEnvironment
	transport.DialContext = (&net.Dialer{Timeout: 10 * time.Second}).DialContext
	transport.ResponseHeaderTimeout = 10 * time.Second
	transport.DisableCompression = true
	client := &http.Client{Transport: transport, CheckRedirect: func(request *http.Request, via []*http.Request) error {
		// requests follows at most 30 redirects (Session.max_redirects).
		if len(via) > 30 {
			return errors.New("too many redirects")
		}
		return nil
	}}
	return requestsSignatureFetcher{client: client}
}

// Fetch is requests.get: every transport error, a 4xx/5xx status or a body
// urllib3 cannot decode is an error (the caller answers None).
func (f requestsSignatureFetcher) Fetch(ctx context.Context, url string) ([]byte, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	request.Header = http.Header{"User-Agent": {invoiceSyncUserAgent}, "Accept-Encoding": {"gzip, deflate"}, "Accept": {"*/*"},
		"Connection": {"keep-alive"}}
	response, err := f.client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode >= 400 {
		// response.raise_for_status()
		return nil, fmt.Errorf("legacy signature: status %d", response.StatusCode)
	}
	reader, err := legacySignatureBody(response)
	if err != nil {
		return nil, err
	}
	return io.ReadAll(reader)
}

// legacySignatureBody follows urllib3's content decoders: each listed coding,
// gzip/x-gzip (concatenated members) and deflate (zlib, else raw), applied
// in reverse; unknown codings stop decoding and pass the bytes through.
func legacySignatureBody(response *http.Response) (io.Reader, error) {
	var codings []string
	for _, item := range strings.Split(strings.ToLower(response.Header.Get("Content-Encoding")), ",") {
		if item = strings.TrimSpace(item); item != "" {
			codings = append(codings, item)
		}
	}
	for _, coding := range codings {
		if coding != "gzip" && coding != "x-gzip" && coding != "deflate" {
			return response.Body, nil
		}
	}
	var reader io.Reader = response.Body
	for i := len(codings) - 1; i >= 0; i-- {
		switch codings[i] {
		case "gzip", "x-gzip":
			gz, err := gzip.NewReader(reader)
			if err != nil {
				return nil, err
			}
			reader = gz
		case "deflate":
			raw, err := io.ReadAll(reader)
			if err != nil {
				return nil, err
			}
			if z, err := zlib.NewReader(bytes.NewReader(raw)); err == nil {
				reader = z
			} else {
				reader = flate.NewReader(bytes.NewReader(raw))
			}
		}
	}
	return reader, nil
}
