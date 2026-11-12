package s3credentials

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func TestMetadataRoleConcurrentCache(t *testing.T) {
	for _, kind := range []string{"EcsContainer", "Ec2InstanceMetadata"} {
		t.Run(kind, func(t *testing.T) {
			directory := isolatedEnvironment(t)
			config := "[profile target]\nrole_arn=arn:aws:iam::123456789012:role/target\ncredential_source=" + kind + "\n"
			if err := os.WriteFile(filepath.Join(directory, "config"), []byte(config), 0600); err != nil {
				t.Fatal(err)
			}
			var mu sync.Mutex
			counts := map[string]int{}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				counts[r.URL.Path]++
				mu.Unlock()
				switch r.URL.Path {
				case "/prefix/latest/api/token":
					if r.Method != "PUT" {
						t.Error("wrong token method")
					}
					w.Header().Set("X-aws-ec2-metadata-token-ttl-seconds", "300")
					fmt.Fprint(w, "synthetic-metadata-token")
				case "/prefix/latest/meta-data/iam/security-credentials/":
					if r.Header.Get("X-aws-ec2-metadata-token") != "synthetic-metadata-token" {
						t.Error("missing metadata token")
					}
					fmt.Fprint(w, "source")
				case "/container", "/prefix/latest/meta-data/iam/security-credentials/source":
					if r.URL.Path == "/container" {
						if r.Header.Get("Authorization") != "synthetic-container-token" {
							t.Error("wrong container token")
						}
					} else if r.Header.Get("X-aws-ec2-metadata-token") != "synthetic-metadata-token" {
						t.Error("missing metadata token")
					}
					w.Header().Set("Content-Type", "application/json")
					json.NewEncoder(w).Encode(map[string]string{"Code": "Success", "AccessKeyId": "source-access", "SecretAccessKey": "source-secret", "Token": "source-token", "Expiration": "2099-01-01T00:00:00Z"})
				case "/sts":
					if !strings.Contains(r.Header.Get("Authorization"), "Credential=source-access/") || r.Header.Get("X-Amz-Security-Token") != "source-token" {
						t.Error("wrong STS signing identity")
					}
					w.Header().Set("Content-Type", "text/xml")
					fmt.Fprint(w, `<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><AssumeRoleResult><Credentials><AccessKeyId>target-access</AccessKeyId><SecretAccessKey>target-secret</SecretAccessKey><SessionToken>target-token</SessionToken><Expiration>2099-01-01T00:00:00Z</Expiration></Credentials></AssumeRoleResult></AssumeRoleResponse>`)
				default:
					t.Error("unexpected credential request path")
					w.WriteHeader(404)
				}
			}))
			defer server.Close()
			os.Setenv("AWS_REGION", "us-east-1")
			os.Setenv("AWS_ENDPOINT_URL_STS", server.URL+"/sts")
			if kind == "EcsContainer" {
				os.Setenv("AWS_CONTAINER_CREDENTIALS_FULL_URI", server.URL+"/container")
				os.Setenv("AWS_CONTAINER_AUTHORIZATION_TOKEN", "synthetic-container-token")
			} else {
				os.Setenv("AWS_EC2_METADATA_DISABLED", "false")
				os.Setenv("AWS_EC2_METADATA_SERVICE_ENDPOINT", server.URL+"/prefix")
			}
			provider, err := New(context.Background(), Options{Profile: "target"}, server.Client())
			if err != nil {
				t.Fatal(err)
			}
			var callers sync.WaitGroup
			for i := 0; i < 64; i++ {
				callers.Add(1)
				go func() {
					defer callers.Done()
					c, err := provider.Retrieve(context.Background())
					if err != nil || c.AccessKeyID != "target-access" {
						t.Error("role retrieval differs", err)
					}
				}()
			}
			callers.Wait()
			expectedPaths := 2
			if kind == "Ec2InstanceMetadata" {
				expectedPaths = 4
			}
			mu.Lock()
			defer mu.Unlock()
			if len(counts) != expectedPaths {
				t.Fatal("metadata fan-out differs", len(counts))
			}
			for _, count := range counts {
				if count != 1 {
					t.Fatal("repeated metadata request", count)
				}
			}
		})
	}
}
