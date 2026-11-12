//go:build storageconfigintegration

package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestDjangoStorageConfiguration(t *testing.T) {
	raw, e := os.ReadFile(os.Getenv("ASSOZETA_STORAGE_CONFIG_FIXTURE"))
	if e != nil {
		t.Fatal("run storage_configuration.py")
	}
	var cases []struct {
		Name, File          string
		Process             map[string]string
		Expected            map[string]any
		Error, BackendError bool
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 65 {
		t.Fatal("missing storage cases")
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
			file := filepath.Join(t.TempDir(), "fixture.env")
			if e := os.WriteFile(file, []byte(tc.File), 0600); e != nil {
				t.Fatal(e)
			}
			cfg, e := Load(file)
			if (e != nil) != tc.Error {
				t.Fatal("settings error boundary differs", e)
			}
			if !reflect.DeepEqual(before, os.Environ()) {
				t.Fatal("configuration mutated environment")
			}
			if e != nil {
				if strings.Contains(e.Error(), "sensitive-fixture-secret") {
					t.Fatal("secret proxy exposed")
				}
				return
			}
			s := cfg.Storage
			optional := func(p *string) any {
				if p == nil {
					return nil
				}
				return *p
			}
			actual := map[string]any{"AccessKey": s.AccessKey, "SecretKey": s.SecretKey, "Bucket": s.Bucket, "Region": s.Region, "Endpoint": s.Endpoint, "Location": s.Location, "DefaultACL": optional(s.DefaultACL), "FileOverwrite": s.FileOverwrite, "UseObjectACL": s.UseObjectACL, "PublicBaseURL": s.PublicBaseURL, "StoreOnDisk": s.StoreOnDisk, "StorageDir": s.StorageDir, "Backend": s.Backend, "CacheControl": s.CacheControl, "SessionToken": optional(s.SessionToken), "SessionProfile": optional(s.SessionProfile)}
			if !reflect.DeepEqual(actual, tc.Expected) {
				t.Fatal("declared storage values differ")
			}
			if (s.ValidateBackend() != nil) != tc.BackendError {
				t.Fatal("backend construction boundary differs")
			}
			encoded, _ := json.Marshal(s)
			cfgJSON, _ := json.Marshal(cfg)
			for _, representation := range []string{fmt.Sprint(s), fmt.Sprintf("%+v", s), fmt.Sprintf("%#v", s), string(encoded), string(cfgJSON)} {
				for _, secret := range []string{s.AccessKey, s.SecretKey, s.Endpoint, fmt.Sprint(optional(s.SessionToken)), fmt.Sprint(optional(s.SessionProfile))} {
					if len(secret) > 7 && strings.Contains(representation, secret) {
						t.Fatal("storage representation exposed private configuration")
					}
				}
			}
		})
	}
}
