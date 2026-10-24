package s3credentials

import (
	"context"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// These requests use New's production HTTP client, not httptest.Client or a
// substitute credential provider. Existing Django matrices separately verify
// complete STS signatures and identity precedence.
func TestCredentialHTTPSPreservesBoundsAcrossProviders(t *testing.T) {
	for _, kind := range []string{"container", "nested-role", "web-identity"} {
		for _, mode := range []string{"success", "oversize", "redirect307", "redirect308"} {
			t.Run(kind+"/"+mode, func(t *testing.T) {
				directory := isolatedEnvironment(t)
				var calls, redirected atomic.Int32
				server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls.Add(1)
					if r.URL.Path == "/redirected" {
						redirected.Add(1)
						w.WriteHeader(500)
						return
					}
					if strings.HasPrefix(mode, "redirect") {
						status := 307
						if mode == "redirect308" {
							status = 308
						}
						http.Redirect(w, r, "/redirected", status)
						return
					}
					if err := r.ParseForm(); err != nil {
						t.Error(err)
						w.WriteHeader(400)
						return
					}
					if mode == "oversize" {
						_, _ = io.WriteString(w, strings.Repeat(" ", 1<<20))
					}
					if kind == "container" {
						_ = json.NewEncoder(w).Encode(map[string]any{"AccessKeyId": "container-access", "SecretAccessKey": "fixture-secret", "Token": "fixture-token", "Expiration": time.Now().Add(time.Hour).UTC().Format(time.RFC3339)})
						return
					}
					action := r.Form.Get("Action")
					access := "web-access"
					if kind == "web-identity" {
						if action != "AssumeRoleWithWebIdentity" || r.Header.Get("Authorization") != "" || r.Form.Get("WebIdentityToken") != "private-fixture-token" {
							t.Error("web identity transport changed")
						}
					} else {
						access = "first-access"
						source := "root-access"
						if strings.HasSuffix(r.Form.Get("RoleArn"), "/second") {
							access = "second-access"
							source = "first-access"
						}
						if action != "AssumeRole" || !strings.Contains(r.Header.Get("Authorization"), "Credential="+source+"/") {
							t.Error("nested role source identity changed")
						}
					}
					fmt.Fprintf(w, "<%sResponse><%sResult><Credentials><AccessKeyId>%s</AccessKeyId><SecretAccessKey>fixture-secret</SecretAccessKey><SessionToken>fixture-token</SessionToken><Expiration>%s</Expiration></Credentials></%sResult></%sResponse>", action, action, access, time.Now().Add(time.Hour).UTC().Format(time.RFC3339), action, action)
				}))
				defer server.Close()
				ca := filepath.Join(directory, "private-ca.pem")
				if err := os.WriteFile(ca, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0600); err != nil {
					t.Fatal(err)
				}
				t.Setenv("AWS_CA_BUNDLE", ca)
				t.Setenv("AWS_REGION", "us-east-1")
				options := Options{}
				expected := kind + "-access"
				switch kind {
				case "container":
					t.Setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", server.URL)
				case "nested-role":
					t.Setenv("AWS_ENDPOINT_URL_STS", server.URL)
					options.Profile = "second"
					expected = "second-access"
					config := "[profile second]\nrole_arn=arn:aws:iam::123456789012:role/second\nsource_profile=first\n[profile first]\nrole_arn=arn:aws:iam::123456789012:role/first\nsource_profile=root\n[profile root]\naws_access_key_id=root-access\naws_secret_access_key=fixture-secret\n"
					if err := os.WriteFile(filepath.Join(directory, "config"), []byte(config), 0600); err != nil {
						t.Fatal(err)
					}
				case "web-identity":
					t.Setenv("AWS_ENDPOINT_URL_STS", server.URL)
					t.Setenv("AWS_ROLE_ARN", "arn:aws:iam::123456789012:role/web")
					file := filepath.Join(directory, "token")
					if err := os.WriteFile(file, []byte("private-fixture-token"), 0600); err != nil {
						t.Fatal(err)
					}
					t.Setenv("AWS_WEB_IDENTITY_TOKEN_FILE", file)
					expected = "web-access"
				}
				provider, err := New(context.Background(), options, nil)
				if err != nil {
					t.Fatal("TLS provider initialization", err)
				}
				value, err := provider.Retrieve(context.Background())
				if mode == "success" {
					if err != nil || value.AccessKeyID != expected {
						t.Fatal("trusted TLS credential retrieval", err)
					}
				} else if err == nil {
					t.Fatal("provider protection lost")
				}
				if redirected.Load() != 0 {
					t.Fatal("credential redirect followed")
				}
				want := int32(1)
				if kind == "nested-role" && mode == "success" {
					want = 2
				}
				if calls.Load() != want {
					t.Fatal("provider retry count changed", calls.Load(), want)
				}
			})
		}
	}
}
