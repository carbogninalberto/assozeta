package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRegistrationFlagsUseDjangoDefaultsAndValues(t *testing.T) {
	cfg, err := Parse(environment())
	if err != nil || !cfg.FamilyRegistrationEnabled || cfg.FederationRegistrationEnabled || cfg.MultipleRegistrationEnabled {
		t.Fatal("registration defaults differ", err)
	}
	for _, tc := range []struct {
		value string
		want  bool
	}{
		{"true", true}, {" YES ", true}, {"on", true}, {"ok", true}, {"Y", true}, {"1", true}, {"-2", true}, {"+20", true}, {"1_0", true}, {"٠١", true}, {" １２ ", true}, {"\x1c1\x1f", true},
		{"", false}, {"false", false}, {"off", false}, {"no", false}, {"0", false}, {"-0", false}, {"000_000", false}, {"٠٠", false}, {"1.0", false}, {"0x1", false}, {"1__0", false}, {"１_", false}, {"\x1c2", false}, {"enabled", false},
		{strings.Repeat("9", 4300), true}, {strings.Repeat("9", 4301), false},
	} {
		t.Run(tc.value[:min(len(tc.value), 30)], func(t *testing.T) {
			env := environment()
			for _, name := range []string{"FAMILY_REGISTRATION_ENABLED", "FEDERATION_REGISTRATION_ENABLED", "MULTIPLE_REGISTRATION_ENABLED"} {
				env[name] = tc.value
			}
			cfg, err := Parse(env)
			if err != nil || cfg.FamilyRegistrationEnabled != tc.want || cfg.FederationRegistrationEnabled != tc.want || cfg.MultipleRegistrationEnabled != tc.want {
				t.Fatal("Django boolean differs", err)
			}
		})
	}
}
func TestRegistrationFlagProxies(t *testing.T) {
	env := environment()
	env["FAMILY_REGISTRATION_ENABLED"] = "$MISSING"
	env["FEDERATION_REGISTRATION_ENABLED"] = "$$FEATURE"
	env["FEATURE"] = "$ENABLED"
	env["ENABLED"] = "-2"
	cfg, err := Parse(env)
	if err != nil || !cfg.FamilyRegistrationEnabled || !cfg.FederationRegistrationEnabled {
		t.Fatal("proxy/default differs", err)
	}
	env["FEATURE"] = "$FEDERATION_REGISTRATION_ENABLED"
	if _, err := Parse(env); err == nil {
		t.Fatal("cyclic proxy accepted")
	}
}
func TestRegistrationProcessEnvironmentOverridesFile(t *testing.T) {
	env := environment()
	var file strings.Builder
	for k, v := range env {
		if k == "DBPASSWORD" {
			continue
		}
		file.WriteString(k + "=" + v + "\n")
	}
	file.WriteString("FAMILY_REGISTRATION_ENABLED=False\nFEDERATION_REGISTRATION_ENABLED=True\n")
	path := filepath.Join(t.TempDir(), "fixture.env")
	if err := os.WriteFile(path, []byte(file.String()), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FAMILY_REGISTRATION_ENABLED", "true")
	t.Setenv("FEDERATION_REGISTRATION_ENABLED", "0")
	cfg, err := Load(path)
	if err != nil || !cfg.FamilyRegistrationEnabled || cfg.FederationRegistrationEnabled {
		t.Fatal("environment precedence differs", err)
	}
}

func TestRegistrationFileUsesDjangoLiteralParsing(t *testing.T) {
	for _, tc := range []struct {
		name, lines        string
		family, federation bool
	}{
		{"missing proxy inherits default", "FAMILY_REGISTRATION_ENABLED=$MISSING\n", true, false},
		{"reference resolved after entire file", "FAMILY_REGISTRATION_ENABLED=$LATER\nLATER=0\nFEDERATION_REGISTRATION_ENABLED=$YES\nYES=ok\n", false, true},
		{"inline comment is literal", "FAMILY_REGISTRATION_ENABLED=true # literal\n", false, false},
		{"spaces around key are invalid", " FAMILY_REGISTRATION_ENABLED=false\n", true, false},
		{"quoted words", "FAMILY_REGISTRATION_ENABLED='on'\nFEDERATION_REGISTRATION_ENABLED=\"yes\"\n", true, true},
		{"quoted escape stays literal", "FAMILY_REGISTRATION_ENABLED=\"\\ntrue\"\n", false, false},
		{"duplicate last wins", "FAMILY_REGISTRATION_ENABLED=0\nFAMILY_REGISTRATION_ENABLED=1\n", true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			for _, key := range []string{"FAMILY_REGISTRATION_ENABLED", "FEDERATION_REGISTRATION_ENABLED", "MULTIPLE_REGISTRATION_ENABLED", "MISSING", "LATER", "YES"} {
				t.Setenv(key, "")
				if err := os.Unsetenv(key); err != nil {
					t.Fatal(err)
				}
			}
			path := filepath.Join(t.TempDir(), "fixture.env")
			if err := os.WriteFile(path, []byte("AWS_ACCESS_KEY_ID=fixture-access\nAWS_SECRET_ACCESS_KEY=fixture-secret\nAWS_STORAGE_BUCKET_NAME=fixture-bucket\nAWS_S3_REGION_NAME=us-east-1\nAWS_S3_ENDPOINT_URL=http://127.0.0.1:9000\nAWS_LOCATION=private\nSECRET_KEY=synthetic-session-secret\nDEBUG=False\nDBTYPE=POSTGRES\nDBNAME=fixture\nDBHOST=/tmp/fixture\nDBUSER=fixture\nSOCIAL_AUTH_GOOGLE_OAUTH2_KEY=\nSOCIAL_AUTH_GOOGLE_OAUTH2_SECRET=\nJWT_SECRET_KEY=synthetic-private\nJWT_PUBLIC_KEY=synthetic-public\n"+tc.lines), 0600); err != nil {
				t.Fatal(err)
			}
			cfg, err := Load(path)
			if err != nil || cfg.FamilyRegistrationEnabled != tc.family || cfg.FederationRegistrationEnabled != tc.federation {
				t.Fatal("file registration semantics differ", err)
			}
		})
	}
}
