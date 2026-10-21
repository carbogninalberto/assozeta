//go:build storagerolesintegration

package s3credentials

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestDjangoAssumeRole(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_STORAGE_ROLE_FIXTURE"))
	if err != nil {
		t.Fatal("run storage_roles.py")
	}
	var cases []struct {
		Name        string
		Environment map[string]string
		Error       bool
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 44 {
		t.Fatal("missing role cases", len(cases))
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			previous := os.Environ()
			os.Clearenv()
			defer func() {
				os.Clearenv()
				for _, entry := range previous {
					k, v, _ := strings.Cut(entry, "=")
					os.Setenv(k, v)
				}
			}()
			for k, v := range tc.Environment {
				if err := os.Setenv(k, v); err != nil {
					t.Fatal(err)
				}
			}
			p, err := New(context.Background(), Options{Profile: "target"}, nil)
			if err == nil {
				for i := 0; i < 2; i++ {
					c, e := p.Retrieve(context.Background())
					err = e
					if err != nil {
						break
					}
					if c.AccessKeyID != "target-access" || c.SecretAccessKey != "target-secret" || c.SessionToken != "target-token" || c.Expired() {
						t.Fatal("wrong role credentials")
					}
				}
			}
			if (err != nil) != tc.Error {
				t.Fatal("role result differs", err)
			}
			if err != nil && (strings.Contains(err.Error(), "sensitive-provider-message") || strings.Contains(err.Error(), tc.Environment["AWS_CONFIG_FILE"])) {
				t.Fatal("private provider error exposed")
			}
		})
	}
}
