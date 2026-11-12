package service

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type invoiceSyncAdmission struct {
	cutoff *time.Time
	err    error
	closed int
}

func (a *invoiceSyncAdmission) Acquire(context.Context) (io.Closer, *time.Time, error) {
	if a.err != nil {
		return nil, nil, a.err
	}
	return invoiceSyncCloser{a}, a.cutoff, nil
}

type invoiceSyncCloser struct{ admission *invoiceSyncAdmission }

func (c invoiceSyncCloser) Close() error { c.admission.closed++; return nil }

func TestInvoiceSyncPrintRequest(t *testing.T) {
	var mu sync.Mutex
	var seen []*http.Request
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		seen = append(seen, r)
		mu.Unlock()
		if r.URL.Path == "/document/invoice/abc" {
			http.Redirect(w, r, "/moved", http.StatusFound)
			return
		}
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()
	admission := &invoiceSyncAdmission{}
	printer := NewInvoiceSyncPrint(admission, server.URL)
	token := "Bearer t"
	if err := printer.Invoice(context.Background(), "abc", &token); err != nil {
		t.Fatal(err)
	}
	if len(seen) != 2 || seen[0].URL.RawQuery != "send_receipt_email=False" || seen[1].URL.Path != "/moved" {
		t.Fatalf("requests: %d", len(seen))
	}
	for _, r := range seen {
		want := map[string]string{"User-Agent": invoiceSyncUserAgent, "Accept-Encoding": "gzip, deflate", "Accept": "*/*",
			"Connection": "keep-alive", "Authorization": token}
		for name, value := range want {
			if r.Header.Get(name) != value {
				t.Fatalf("%s: %q", name, r.Header.Get(name))
			}
		}
		if r.Header.Get("Referer") != "" || len(r.Header) != len(want) {
			t.Fatalf("headers: %v", r.Header)
		}
	}
	if admission.closed != 1 {
		t.Fatal("lease not released")
	}
	seen = nil
	if err := printer.CustomerInvoice(context.Background(), "def", nil); err != nil || len(seen) != 1 || seen[0].Header.Get("Authorization") != "" {
		t.Fatalf("customer print: %v %d", err, len(seen))
	}
}

func TestInvoiceSyncPrintAdmission(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls++ }))
	defer server.Close()
	completed := time.Now()
	for _, admission := range []*invoiceSyncAdmission{{cutoff: &completed}, {err: errors.New("database down")}} {
		if err := NewInvoiceSyncPrint(admission, server.URL).Invoice(context.Background(), "x", nil); err != nil {
			t.Fatal(err)
		}
	}
	for _, err := range []error{domain.ErrRestorePending, domain.ErrRestoreBusy, domain.ErrRestoreVolumeUnavailable} {
		if got := NewInvoiceSyncPrint(&invoiceSyncAdmission{err: err}, server.URL).Invoice(context.Background(), "x", nil); !errors.Is(got,
			ErrInvoiceSyncPrintDeferred) {
			t.Fatalf("%v: %v", err, got)
		}
	}
	if calls != 0 {
		t.Fatalf("stale or deferred tasks made %d requests", calls)
	}
	// An unusable CURRENT_HOST is swallowed like requests' MissingSchema.
	if err := NewInvoiceSyncPrint(&invoiceSyncAdmission{}, "").Invoice(context.Background(), "x", nil); err != nil {
		t.Fatal(err)
	}
}

func TestInvoiceSyncProgressivoSequence(t *testing.T) {
	at := time.Unix(1_800_000_000, 0)
	first, _ := invoiceSyncNextProgressivo(at)
	second, _ := invoiceSyncNextProgressivo(at)
	third, _ := invoiceSyncNextProgressivo(at.Add(time.Second))
	decode := func(text string) int64 {
		const chars = "+-./0123456789=@ABCDEFGHIJKLMNOPQRSTUVWXYZ_"
		var value int64
		for _, r := range text {
			for i, c := range chars {
				if c == r {
					value = value*43 + int64(i)
				}
			}
		}
		return value
	}
	if decode(first) != 1_800_000_000<<16 || decode(second) != 1_800_000_000<<16+1 || decode(third) != 1_800_000_001<<16 {
		t.Fatalf("%s %s %s", first, second, third)
	}
}
