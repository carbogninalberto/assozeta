//go:build configintegration

package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
)

func TestDjangoEnvironmentInterop(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_ENVIRONMENT_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/environment.py")
	}
	var cases []struct {
		Name, File string
		Process    map[string]string
		Expected   map[string]any
		Error      bool
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 55 {
		t.Fatal("invalid environment fixture")
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
			for k, v := range tc.Process {
				os.Setenv(k, v)
			}
			before := os.Environ()
			path := filepath.Join(t.TempDir(), "fixture.env")
			if os.WriteFile(path, []byte(tc.File), 0600) != nil {
				t.Fatal("cannot write fixture")
			}
			cfg, err := Load(path)
			if (err != nil) != tc.Error {
				t.Fatal("configuration error boundary differs")
			}
			if !reflect.DeepEqual(before, os.Environ()) {
				t.Fatal("configuration mutated process environment")
			}
			if err != nil {
				if strings.Contains(err.Error(), "sensitive-fixture-secret") {
					t.Fatal("configuration error exposes proxy contents")
				}
				return
			}
			db, err := pgx.ParseConfig(cfg.DatabaseURL())
			if err != nil {
				t.Fatal("invalid database configuration")
			}
			origins := []any{}
			for _, value := range cfg.CSRFTrustedOrigins {
				origins = append(origins, value)
			}
			actual := map[string]any{"debug": cfg.Debug, "rate_enabled": cfg.RateLimitEnabled, "google": cfg.GoogleClientID, "google_secret": cfg.GoogleClientSecret, "apple": cfg.AppleClientID, "apple_team": cfg.AppleTeamID, "apple_key": cfg.AppleKeyID, "apple_secret": cfg.AppleSecret,
				"jwt_private": cfg.JWTSigningKey, "jwt_public": cfg.JWTVerifyingKey, "database_password": db.Password, "redis_password": cfg.RedisPassword, "origins": origins, "family": cfg.FamilyRegistrationEnabled, "federation": cfg.FederationRegistrationEnabled, "multiple": cfg.MultipleRegistrationEnabled}
			if !reflect.DeepEqual(actual, tc.Expected) {
				t.Fatal("declared configuration values differ")
			}
			encoded, _ := json.Marshal(cfg)
			for _, representation := range []string{fmt.Sprint(cfg), fmt.Sprintf("%+v", cfg), fmt.Sprintf("%#v", cfg), string(encoded)} {
				for _, secret := range []string{cfg.GoogleClientSecret, cfg.AppleSecret, cfg.JWTSigningKey, cfg.JWTVerifyingKey, cfg.RedisPassword, db.Password} {
					if len(secret) >= 8 && strings.Contains(representation, secret) {
						t.Fatal("configuration representation exposes credentials")
					}
				}
			}
		})
	}
}
