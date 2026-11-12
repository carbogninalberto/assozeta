package s3credentials

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func isolatedEnvironment(t *testing.T) string {
	t.Helper()
	previous := os.Environ()
	os.Clearenv()
	t.Cleanup(func() {
		os.Clearenv()
		for _, entry := range previous {
			k, v, _ := strings.Cut(entry, "=")
			os.Setenv(k, v)
		}
	})
	directory := t.TempDir()
	for _, name := range []string{"config", "credentials"} {
		if err := os.WriteFile(filepath.Join(directory, name), nil, 0600); err != nil {
			t.Fatal(err)
		}
	}
	os.Setenv("AWS_CONFIG_FILE", filepath.Join(directory, "config"))
	os.Setenv("AWS_SHARED_CREDENTIALS_FILE", filepath.Join(directory, "credentials"))
	os.Setenv("AWS_EC2_METADATA_DISABLED", "true")
	return directory
}

func TestEndpointCredentialsConcurrentCacheAndCancellation(t *testing.T) {
	isolatedEnvironment(t)
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("Authorization") != "synthetic-authorization" {
			t.Error("missing credential endpoint authorization")
		}
		json.NewEncoder(w).Encode(map[string]any{"AccessKeyId": "endpoint-access", "SecretAccessKey": "endpoint-secret", "Token": "endpoint-token", "Expiration": time.Now().Add(time.Hour).UTC().Format(time.RFC3339)})
	}))
	defer server.Close()
	os.Setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", server.URL)
	os.Setenv("AWS_CONTAINER_AUTHORIZATION_TOKEN", "synthetic-authorization")
	p, err := New(context.Background(), Options{}, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	var group sync.WaitGroup
	for i := 0; i < 64; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			c, err := p.Retrieve(context.Background())
			if err != nil || c.AccessKeyID != "endpoint-access" || c.SecretAccessKey != "endpoint-secret" || c.SessionToken != "endpoint-token" || !c.CanExpire {
				t.Error("endpoint credentials differ", err)
			}
		}()
	}
	group.Wait()
	if calls.Load() != 1 {
		t.Fatal("concurrent credentials were not cached", calls.Load())
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err = p.Retrieve(ctx); !errors.Is(err, context.Canceled) {
		t.Fatal("cached credentials ignored cancellation", err)
	}
	if _, err = New(ctx, Options{}, server.Client()); !errors.Is(err, context.Canceled) {
		t.Fatal("construction ignored cancellation", err)
	}
	for _, value := range []string{fmt.Sprint(p), fmt.Sprintf("%#v", p)} {
		if strings.Contains(value, "endpoint-secret") || strings.Contains(value, "synthetic-authorization") {
			t.Fatal("provider representation exposed secrets")
		}
	}
}

func TestCredentialEndpointDeadlineAndRedaction(t *testing.T) {
	for _, mode := range []string{"blocked", "malformed", "denied", "unavailable", "redirect", "expired", "oversized"} {
		t.Run(mode, func(t *testing.T) {
			isolatedEnvironment(t)
			var calls, redirected atomic.Int32
			done := make(chan struct{}, 16)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/redirected" {
					redirected.Add(1)
					w.WriteHeader(500)
					return
				}
				calls.Add(1)
				defer func() { done <- struct{}{} }()
				switch mode {
				case "blocked":
					<-r.Context().Done()
				case "oversized":
					io.WriteString(w, strings.Repeat(" ", 1<<20)+`{"AccessKeyId":"oversized-access","SecretAccessKey":"oversized-secret"}`)
				case "malformed":
					io.WriteString(w, "sensitive-provider-secret")
				case "denied":
					w.WriteHeader(403)
					io.WriteString(w, "sensitive-provider-secret")
				case "unavailable":
					w.WriteHeader(503)
					io.WriteString(w, "sensitive-provider-secret")
				case "redirect":
					http.Redirect(w, r, "/redirected", 302)
				case "expired":
					io.WriteString(w, `{"AccessKeyId":"expired-access","SecretAccessKey":"expired-secret","Token":"expired-token","Expiration":"2000-01-01T00:00:00Z"}`)
				}
			}))
			defer server.Close()
			os.Setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", server.URL)
			var client HTTPClient
			if mode == "blocked" {
				c := server.Client()
				c.Timeout = 100 * time.Millisecond
				client = c
			}
			p, err := New(context.Background(), Options{}, client)
			if err != nil {
				t.Fatal(err)
			}
			ctx := context.Background()
			if mode == "blocked" {
				var cancel context.CancelFunc
				ctx, cancel = context.WithTimeout(ctx, 20*time.Millisecond)
				defer cancel()
			}
			_, err = p.Retrieve(ctx)
			if mode == "blocked" {
				if !errors.Is(err, context.DeadlineExceeded) {
					t.Fatal("credential deadline ignored", err)
				}
			} else if !errors.Is(err, ErrCredentials) {
				t.Fatal("provider failure not normalized", err)
			}
			if err != nil && strings.Contains(err.Error(), "sensitive-provider-secret") {
				t.Fatal("provider secret exposed")
			}
			select {
			case <-done:
			case <-time.After(time.Second):
				t.Fatal("credential HTTP operation remained unbounded")
			}
			if calls.Load() != 1 || redirected.Load() != 0 {
				t.Fatal("credential request retried or redirected", calls.Load(), redirected.Load())
			}
		})
	}
}

func TestCredentialSharedFilesBounded(t *testing.T) {
	directory := isolatedEnvironment(t)
	if err := os.WriteFile(filepath.Join(directory, "config"), []byte(strings.Repeat("x", (1<<20)+1)), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := New(context.Background(), Options{AccessKey: "static-access", SecretKey: "static-secret"}, nil); !errors.Is(err, ErrConfiguration) {
		t.Fatal("unbounded configuration accepted", err)
	}
}

func TestEndpointShortLivedCredentialsRefresh(t *testing.T) {
	isolatedEnvironment(t)
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sequence := calls.Add(1)
		json.NewEncoder(w).Encode(map[string]any{"AccessKeyId": fmt.Sprintf("access-%d", sequence), "SecretAccessKey": "fixture-secret", "Token": "fixture-token", "Expiration": time.Now().Add(time.Second).UTC().Format(time.RFC3339Nano)})
	}))
	defer server.Close()
	os.Setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", server.URL)
	p, err := New(context.Background(), Options{}, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	first, err := p.Retrieve(context.Background())
	if err != nil || first.AccessKeyID != "access-1" || first.Expired() {
		t.Fatal("valid short-lived credentials rejected", err)
	}
	time.Sleep(time.Until(first.Expires) + 10*time.Millisecond)
	second, err := p.Retrieve(context.Background())
	if err != nil || second.AccessKeyID != "access-2" || second.Expired() || calls.Load() != 2 {
		t.Fatal("expired credentials not refreshed", err)
	}
}
