package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBootstrapEnvironmentTruthinessAndRedaction(t *testing.T) {
	for _, key := range []string{"AI_API_KEY", "STRIPE_PUBLIC_KEY", "STRIPE_KEY", "STRIPE_WEBHOOK_SECRET"} {
		for _, value := range []string{"", " ", "False", "private-fixture-value"} {
			env := environment()
			env[key] = value
			cfg, err := Parse(env)
			if err != nil {
				t.Fatal(err)
			}
			actual := cfg.StripeEnabled
			if key == "AI_API_KEY" {
				actual = cfg.AIEnabled
			}
			if actual != (value != "") {
				t.Fatalf("truthiness differs for %s", key)
			}
			data, _ := json.Marshal(cfg)
			if strings.Contains(string(data), "private-fixture-value") {
				t.Fatal("secret leaked")
			}
		}
	}
}
func TestManualImageMetadataAndEnvironmentPrecedence(t *testing.T) {
	env := environment()
	env["MANUAL_APPLICATION_REVISION"] = "environment-revision"
	env["MANUAL_CORPUS_BASE_URL"] = "  configured  "
	cfg, err := Parse(env)
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	got, err := manualImageSettings(root, cfg.Manual, env)
	if err != nil {
		t.Fatal(err)
	}
	if got.ApplicationRevision != "environment-revision" || got.BundleRequired || got.BundlePath != filepath.Join(root, "release-manual") || got.IndexPath != filepath.Join(root, "manuale/index.json") || got.CorpusBaseURL != "  configured  " {
		t.Fatal("manual defaults differ")
	}
	for _, revision := range []string{"image-revision\n", "unknown\n", ""} {
		if err := os.WriteFile(filepath.Join(root, "CODE_REVISION"), []byte(revision), 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(root, "MANUAL_REQUIRED"), []byte("true\n"), 0600); err != nil {
			t.Fatal(err)
		}
		got, err = manualImageSettings(root, cfg.Manual, env)
		if err != nil {
			t.Fatal(err)
		}
		want := strings.TrimSpace(revision)
		if want == "unknown" {
			want = ""
		}
		if got.ApplicationRevision != want || !got.BundleRequired {
			t.Fatal("image metadata did not override environment")
		}
	}
	env["MANUAL_INDEX_PATH"] = "explicit-relative.json"
	env["MANUAL_DEVELOPMENT_ROOT"] = "explicit-development"
	cfg, err = Parse(env)
	if err != nil {
		t.Fatal(err)
	}
	got, err = manualImageSettings(root, cfg.Manual, env)
	if err != nil {
		t.Fatal(err)
	}
	if got.IndexPath != "explicit-relative.json" || got.DevelopmentRoot != "explicit-development" {
		t.Fatal("explicit relative paths changed")
	}
}

func TestManualConfigurationProxyErrorsDoNotExposeValues(t *testing.T) {
	env := environment()
	env["MANUAL_CORPUS_BASE_URL"] = "$PRIVATE_MANUAL"
	env["PRIVATE_MANUAL"] = "$MANUAL_CORPUS_BASE_URL"
	if _, err := Parse(env); err == nil || strings.Contains(err.Error(), "PRIVATE_MANUAL") {
		t.Fatal("proxy error must identify only originating setting")
	}
	env = environment()
	env["AI_API_KEY"] = "$PRIVATE_AI"
	env["PRIVATE_AI"] = " secret-value "
	cfg, err := Parse(env)
	if err != nil || !cfg.AIEnabled {
		t.Fatal("AI proxy truthiness differs", err)
	}
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "CODE_REVISION"), []byte{255}, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := manualImageSettings(root, cfg.Manual, env); err == nil || strings.Contains(err.Error(), root) {
		t.Fatal("metadata failure must be redacted")
	}
}

func TestManualAssetRootRetainsExplicitPathAndDefault(t *testing.T) {
	for _, value := range []string{"", "relative/images", " /private/fixture/images "} {
		env := environment()
		if value != "" {
			env["MANUAL_ASSET_ROOT"] = value
		}
		cfg, err := Parse(env)
		if err != nil || cfg.Manual.AssetRoot != value {
			t.Fatal("manual asset-root setting changed", err)
		}
		got, err := manualImageSettings(t.TempDir(), cfg.Manual, env)
		if err != nil || got.AssetRoot != value {
			t.Fatal("image defaults changed explicit asset root", err)
		}
	}
}
