//go:build storagewebidentityintegration

package s3credentials

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestDjangoWebIdentity(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_STORAGE_WEB_FIXTURE"))
	if err != nil {
		t.Fatal("run storage_web_identity.py")
	}
	var cases []struct {
		Name, Profile, Prefix string
		Environment           map[string]string
		GoError               bool
		Options               struct{ AccessKey, SecretKey, SessionToken string }
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 38 {
		t.Fatal("missing web identity cases", len(cases))
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
			p, err := New(context.Background(), Options{Profile: tc.Profile, AccessKey: tc.Options.AccessKey, SecretKey: tc.Options.SecretKey, SessionToken: tc.Options.SessionToken}, nil)
			if err == nil {
				for i := 0; i < 2; i++ {
					c, e := p.Retrieve(context.Background())
					err = e
					if err != nil {
						break
					}
					if c.AccessKeyID != tc.Prefix+"-access" || c.SecretAccessKey != tc.Prefix+"-secret" || c.SessionToken != tc.Prefix+"-token" || c.Expired() {
						t.Fatal("wrong web identity credentials")
					}
				}
			}
			if (err != nil) != tc.GoError {
				t.Fatal("web identity error differs", err)
			}
			if err != nil && (strings.Contains(err.Error(), "sensitive-web-provider-message") || strings.Contains(err.Error(), tc.Environment["AWS_CONFIG_FILE"])) {
				t.Fatal("private provider error exposed")
			}
		})
	}
}
