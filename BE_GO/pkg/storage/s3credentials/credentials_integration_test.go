//go:build storagecredentialsintegration

package s3credentials

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
)

type forbiddenHTTP struct{ t *testing.T }

func (f forbiddenHTTP) Do(*http.Request) (*http.Response, error) {
	f.t.Error("unexpected credential network")
	return nil, fmt.Errorf("forbidden")
}

func TestDjangoCredentialSelection(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_STORAGE_CREDENTIALS_FIXTURE"))
	if err != nil {
		t.Fatal("run storage_credentials.py")
	}
	var cases []struct {
		Name        string
		Options     struct{ AccessKey, SecretKey, SessionToken, Profile string }
		Environment map[string]string
		Expected    []string
		Error       bool
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 44 {
		t.Fatal("missing credential cases")
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
			before := os.Environ()
			options := Options(tc.Options)
			p, err := New(context.Background(), options, forbiddenHTTP{t})
			var actual []string
			if err == nil {
				c, e := p.Retrieve(context.Background())
				err = e
				actual = []string{c.AccessKeyID, c.SecretAccessKey, c.SessionToken}
			}
			if (err != nil) != tc.Error {
				t.Fatal("credential error boundary differs", err)
			}
			if err == nil && !reflect.DeepEqual(actual, tc.Expected) {
				t.Fatal("credential selection differs")
			}
			if err != nil && (strings.Contains(err.Error(), "sensitive-") || strings.Contains(err.Error(), tc.Environment["AWS_CONFIG_FILE"])) {
				t.Fatal("private provider error exposed")
			}
			if !reflect.DeepEqual(before, os.Environ()) {
				t.Fatal("provider mutated environment")
			}
			encoded, _ := json.Marshal(options)
			for _, text := range []string{string(encoded), fmt.Sprint(options), fmt.Sprintf("%#v", options)} {
				for _, secret := range []string{tc.Options.AccessKey, tc.Options.SecretKey, tc.Options.SessionToken, tc.Options.Profile} {
					if secret != "" && strings.Contains(text, secret) {
						t.Fatal("private options exposed")
					}
				}
			}
		})
	}
}
