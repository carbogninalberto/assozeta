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
	"testing"
	"time"
)

const testMFASerial = "arn:aws:iam::123456789012:mfa/fixture"

func mfaProfile(t *testing.T) {
	t.Helper()
	directory := isolatedEnvironment(t)
	config := "[profile target]\nrole_arn=arn:aws:iam::123456789012:role/target\nsource_profile=source\nmfa_serial=" + testMFASerial + "\n"
	credentials := "[source]\naws_access_key_id=source-access\naws_secret_access_key=source-secret\n"
	for name, body := range map[string]string{"config": config, "credentials": credentials} {
		if err := os.WriteFile(filepath.Join(directory, name), []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
	}
	os.Setenv("AWS_REGION", "us-east-1")
}

func TestMFARequiresExplicitCallback(t *testing.T) {
	mfaProfile(t)
	if _, err := New(context.Background(), Options{Profile: "target"}, nil); !errors.Is(err, ErrConfiguration) {
		t.Fatal("interactive MFA must not be implicitly enabled", err)
	}
}

func TestMFAConcurrentWaitersAndCancellation(t *testing.T) {
	for _, failure := range []bool{false, true} {
		t.Run(fmt.Sprint("callback failure=", failure), func(t *testing.T) {
			mfaProfile(t)
			var requests, prompts atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests.Add(1)
				if err := r.ParseForm(); err != nil {
					t.Error(err)
				}
				if r.Form.Get("SerialNumber") != testMFASerial || r.Form.Get("TokenCode") != "012345" {
					t.Error("MFA fields differ")
				}
				w.Header().Set("Content-Type", "text/xml")
				fmt.Fprint(w, `<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><AssumeRoleResult><Credentials><AccessKeyId>target-access</AccessKeyId><SecretAccessKey>target-secret</SecretAccessKey><SessionToken>target-token</SessionToken><Expiration>2099-01-01T00:00:00Z</Expiration></Credentials></AssumeRoleResult></AssumeRoleResponse>`)
			}))
			defer server.Close()
			os.Setenv("AWS_ENDPOINT_URL_STS", server.URL)
			entered, release := make(chan struct{}), make(chan struct{})
			var releaseOnce sync.Once
			defer releaseOnce.Do(func() { close(release) })
			callback := func(ctx context.Context, serial string) (string, error) {
				if prompts.Add(1) != 1 {
					return "", errors.New("repeated callback")
				}
				if serial != testMFASerial {
					t.Error("wrong MFA device")
				}
				if deadline, ok := ctx.Deadline(); !ok || time.Until(deadline) > 10*time.Second {
					t.Error("unbounded callback context")
				}
				close(entered)
				select {
				case <-release:
				case <-ctx.Done():
					return "", ctx.Err()
				}
				if failure {
					return "", errors.New("sensitive-mfa-message")
				}
				return "012345", nil
			}
			p, err := New(context.Background(), Options{Profile: "target", MFAToken: callback}, server.Client())
			if err != nil {
				t.Fatal(err)
			}
			cancelled, cancel := context.WithCancel(context.Background())
			cancel()
			if _, err := p.Retrieve(cancelled); !errors.Is(err, context.Canceled) || prompts.Load() != 0 {
				t.Fatal("cancelled request consumed MFA")
			}
			first, stop := context.WithCancel(context.Background())
			defer stop()
			done := make(chan error, 1)
			go func() { _, err := p.Retrieve(first); done <- err }()
			select {
			case <-entered:
			case <-time.After(time.Second):
				t.Fatal("callback did not start")
			}
			stop()
			select {
			case err := <-done:
				if !errors.Is(err, context.Canceled) {
					t.Fatal("caller cancellation differs", err)
				}
			case <-time.After(time.Second):
				t.Fatal("caller blocked on MFA callback")
			}
			var callers sync.WaitGroup
			for i := 0; i < 32; i++ {
				callers.Add(1)
				go func() {
					defer callers.Done()
					c, err := p.Retrieve(context.Background())
					if failure {
						if !errors.Is(err, ErrCredentials) {
							t.Error("callback error category differs", err)
						}
					} else if err != nil || c.AccessKeyID != "target-access" {
						t.Error("concurrent MFA retrieval differs", err)
					}
				}()
			}
			releaseOnce.Do(func() { close(release) })
			callers.Wait()
			expectedRequests := int32(1)
			if failure {
				expectedRequests = 0
			}
			if prompts.Load() != 1 || requests.Load() != expectedRequests {
				t.Fatal("MFA operation bound differs", prompts.Load(), requests.Load())
			}
		})
	}
}
