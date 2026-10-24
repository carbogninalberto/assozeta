//go:build storagetlsintegration

package s3credentials_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/instancelogo"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3credentials"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
)

func TestDjangoStorageTLS(t *testing.T) {
	path := os.Getenv("ASSOZETA_STORAGE_TLS_FIXTURE")
	if path == "" {
		t.Fatal("private TLS fixture required")
	}
	var cases []struct {
		Name        string            `json:"name"`
		Environment map[string]string `json:"environment"`
		Endpoint    string            `json:"endpoint"`
		Profile     bool              `json:"profile_credentials"`
		Expected    map[string]any    `json:"expected"`
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	var results []map[string]any
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			for _, key := range []string{"AWS_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"} {
				old, present := os.LookupEnv(key)
				_ = os.Unsetenv(key)
				t.Cleanup(func() {
					if present {
						_ = os.Setenv(key, old)
					} else {
						_ = os.Unsetenv(key)
					}
				})
			}
			for key, value := range c.Environment {
				t.Setenv(key, value)
			}
			o := s3credentials.Options{AccessKey: "fixture-access", SecretKey: "fixture-secret"}
			if c.Profile {
				o = s3credentials.Options{Profile: "chosen"}
			}
			result := map[string]any{"name": c.Name, "initialized": false, "credentials": false, "exists": false, "read": false, "body_sha256": nil}
			provider, err := s3credentials.New(context.Background(), o, nil)
			if err == nil {
				result["initialized"] = true
				value, retrieveErr := provider.Retrieve(context.Background())
				result["credentials"] = retrieveErr == nil && value.AccessKeyID == "fixture-access" && value.SecretAccessKey == "fixture-secret"
				store := instancelogo.NewS3(instancelogo.S3Options{Endpoint: c.Endpoint, Region: "us-east-1", Bucket: "private", TempDir: t.TempDir(),
					Trust:       func(ctx context.Context) (s3trust.Policy, error) { return s3credentials.LoadTrust(ctx, o.Profile) },
					Credentials: func(context.Context) (aws.CredentialsProvider, error) { return provider, nil }})
				ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
				exists, readErr := store.Exists(ctx, "instance/logo.png")
				result["exists"] = readErr == nil && exists
				if readErr == nil && exists {
					file, openErr := store.Open(ctx, "instance/logo.png")
					if openErr == nil {
						body, bodyErr := io.ReadAll(file.Body)
						closeErr := file.Body.Close()
						if bodyErr == nil && closeErr == nil {
							sum := sha256.Sum256(body)
							result["body_sha256"] = hex.EncodeToString(sum[:])
							result["read"] = true
						}
					}
				}
				cancel()
			} else {
				result["initialization_error"] = err.Error()
			}
			results = append(results, result)
			for key, expected := range c.Expected {
				if result[key] != expected {
					t.Errorf("%s = %v; want %v", key, result[key], expected)
				}
			}
		})
	}
	encoded, err := json.MarshalIndent(results, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(os.Getenv("ASSOZETA_STORAGE_TLS_RESULTS"), encoded, 0600); err != nil {
		t.Fatal(err)
	}
}
