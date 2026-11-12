package config

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

func TestLogoSpoolDirectoryEnvironmentAndRedaction(t *testing.T) {
	env := environment()
	cfg, err := Parse(env)
	if err != nil || cfg.LogoSpoolDirectory != "" {
		t.Fatal("native default must preserve os.TempDir", err)
	}
	env["GO_LOGO_SPOOL_DIR"] = "/var/cache/private-logo-spool"
	cfg, err = Parse(env)
	if err != nil || cfg.LogoSpoolDirectory != env["GO_LOGO_SPOOL_DIR"] {
		t.Fatal("explicit spool directory ignored", err)
	}
	encoded, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{string(encoded), fmt.Sprint(cfg), fmt.Sprintf("%#v", cfg)} {
		if strings.Contains(value, "private-logo-spool") {
			t.Fatal("private spool path leaked")
		}
	}
	env["GO_LOGO_SPOOL_DIR"] = "$PRIVATE_LOGO_DIRECTORY"
	env["PRIVATE_LOGO_DIRECTORY"] = "/private/owned-spool"
	cfg, err = Parse(env)
	if err != nil || cfg.LogoSpoolDirectory != env["PRIVATE_LOGO_DIRECTORY"] {
		t.Fatal("existing environment indirection lost", err)
	}
	delete(env, "PRIVATE_LOGO_DIRECTORY")
	cfg, err = Parse(env)
	if err != nil || cfg.LogoSpoolDirectory != "" {
		t.Fatal("missing optional environment proxy must retain default", err)
	}
	env["PRIVATE_LOGO_DIRECTORY"] = "$GO_LOGO_SPOOL_DIR"
	if _, err = Parse(env); err == nil || strings.Contains(err.Error(), "private/owned-spool") {
		t.Fatal("cyclic indirection must fail without disclosing values")
	}
}
