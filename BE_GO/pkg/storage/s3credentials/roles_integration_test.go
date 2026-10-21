//go:build storagerolesintegration

package s3credentials

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"
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
		Attempts    []bool
		Delay       float64
		MFA         *struct {
			Code    string
			Serials []string
			Fail    bool
		}
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 62 {
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
			calls := 0
			options := Options{Profile: "target"}
			if tc.MFA != nil {
				options.MFAToken = func(ctx context.Context, serial string) (string, error) {
					if calls >= len(tc.MFA.Serials) || serial != tc.MFA.Serials[calls] {
						return "", errors.New("wrong MFA callback")
					}
					calls++
					if tc.MFA.Fail {
						return "", errors.New("sensitive-mfa-message")
					}
					return tc.MFA.Code, nil
				}
			}
			p, initErr := New(context.Background(), options, nil)
			for attempt, expectedError := range tc.Attempts {
				if attempt > 0 && tc.Delay > 0 {
					time.Sleep(time.Duration(tc.Delay * float64(time.Second)))
				}
				err := initErr
				if err == nil {
					c, e := p.Retrieve(context.Background())
					err = e
					if err == nil && (c.AccessKeyID != "target-access" || c.SecretAccessKey != "target-secret" || c.SessionToken != "target-token" || c.Expired()) {
						t.Fatal("wrong role credentials")
					}
				}
				if (err != nil) != expectedError {
					t.Fatal("role result differs", attempt, err)
				}
				if err != nil && (strings.Contains(err.Error(), "sensitive-") || strings.Contains(err.Error(), tc.Environment["AWS_CONFIG_FILE"])) {
					t.Fatal("private provider error exposed")
				}
			}
			expectedCalls := 0
			if tc.MFA != nil {
				expectedCalls = len(tc.MFA.Serials)
			}
			if calls != expectedCalls {
				t.Fatal("MFA callback count differs", calls, expectedCalls)
			}

		})
	}
}
