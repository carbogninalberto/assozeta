package s3credentials

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

func TestWebIdentityTokenRotationAndConcurrentCache(t *testing.T) {
	directory := isolatedEnvironment(t)
	live := filepath.Join(directory, "live-token")
	for _, version := range []string{"first", "second"} {
		if err := os.WriteFile(filepath.Join(directory, version), []byte(version+"-synthetic-token"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(filepath.Join(directory, "first"), live); err != nil {
		t.Fatal(err)
	}
	config := "[profile target]\nrole_arn=arn:aws:iam::123456789012:role/web\nweb_identity_token_file=" + live + "\n"
	if err := os.WriteFile(filepath.Join(directory, "config"), []byte(config), 0600); err != nil {
		t.Fatal(err)
	}
	var calls atomic.Int32
	var phase atomic.Int32
	phase.Store(1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if err := r.ParseForm(); err != nil {
			t.Error(err)
		}
		version := "first"
		duration := 2 * time.Second
		if phase.Load() == 2 {
			version = "second"
			duration = time.Hour
		}
		if r.Form.Get("WebIdentityToken") != version+"-synthetic-token" || r.Header.Get("Authorization") != "" || r.Header.Get("X-Amz-Security-Token") != "" {
			t.Error("token rotation or unsigned request differs")
		}
		w.Header().Set("Content-Type", "text/xml")
		fmt.Fprintf(w, `<AssumeRoleWithWebIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><AssumeRoleWithWebIdentityResult><Credentials><AccessKeyId>%s-access</AccessKeyId><SecretAccessKey>synthetic-secret</SecretAccessKey><SessionToken>synthetic-session</SessionToken><Expiration>%s</Expiration></Credentials></AssumeRoleWithWebIdentityResult></AssumeRoleWithWebIdentityResponse>`, version, time.Now().Add(duration).UTC().Format(time.RFC3339Nano))
	}))
	defer server.Close()
	os.Setenv("AWS_REGION", "us-east-1")
	os.Setenv("AWS_ENDPOINT_URL_STS", server.URL)
	provider, err := New(context.Background(), Options{Profile: "target"}, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	var expiry time.Time
	for wave := 1; wave <= 2; wave++ {
		if wave == 2 {
			temporary := filepath.Join(directory, "next-token")
			if err := os.Symlink(filepath.Join(directory, "second"), temporary); err != nil {
				t.Fatal(err)
			}
			if err := os.Rename(temporary, live); err != nil {
				t.Fatal(err)
			}
			time.Sleep(time.Until(expiry) + 20*time.Millisecond)
			phase.Store(2)
		}
		expected := "first-access"
		if wave == 2 {
			expected = "second-access"
		}
		var group sync.WaitGroup
		var mu sync.Mutex
		for i := 0; i < 64; i++ {
			group.Add(1)
			go func() {
				defer group.Done()
				c, err := provider.Retrieve(context.Background())
				if err != nil || c.AccessKeyID != expected || c.Expired() {
					t.Error("web identity retrieval differs", err)
				}
				mu.Lock()
				expiry = c.Expires
				mu.Unlock()
			}()
		}
		group.Wait()
		if calls.Load() != int32(wave) {
			t.Fatal("web identity request count differs", calls.Load(), wave)
		}
	}
}

func TestWebIdentityRejectsFIFOWithoutBlocking(t *testing.T) {
	path := filepath.Join(t.TempDir(), "token.fifo")
	if err := syscall.Mkfifo(path, 0600); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, err := identityTokenFile(path).GetIdentityToken(); done <- err }()
	select {
	case err := <-done:
		if !errors.Is(err, ErrCredentials) {
			t.Fatal("nonregular token file accepted", err)
		}
	case <-time.After(time.Second):
		t.Fatal("credential FIFO blocked")
	}
}
