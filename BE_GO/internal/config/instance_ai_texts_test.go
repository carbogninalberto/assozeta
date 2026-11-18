package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// AIIntegerTexts is env.int's input after django-environ's proxy resolution:
// the value of MCP_AGENT_MAX_RESULTS (index 1) and MCP_AGENT_QUERY_TIMEOUT
// (index 2), nil when the variable or the end of its chain is unset.
func TestInstanceAdminAIIntegerTexts(t *testing.T) {
	text := func(settings InstanceAdminSettings, index int) string {
		t.Helper()
		if settings.AIIntegerTexts[index] == nil {
			return "<unset>"
		}
		return *settings.AIIntegerTexts[index]
	}
	for _, tc := range []struct {
		name   string
		values map[string]string
		want   [2]string
	}{
		{"unset", map[string]string{}, [2]string{"<unset>", "<unset>"}},
		{"plain", map[string]string{"MCP_AGENT_MAX_RESULTS": " 100 ", "MCP_AGENT_QUERY_TIMEOUT": "5"},
			[2]string{" 100 ", "5"}},
		// "$OTHER" reads OTHER (lstrip('$')); the text is OTHER's value.
		{"proxy", map[string]string{"MCP_AGENT_MAX_RESULTS": "$RESULTS", "RESULTS": "99999999999999999999",
			"MCP_AGENT_QUERY_TIMEOUT": "$$TIMEOUT", "TIMEOUT": "7"}, [2]string{"99999999999999999999", "7"}},
		// A proxy to an unset variable leaves env.int's default in place.
		{"proxy-unset", map[string]string{"MCP_AGENT_MAX_RESULTS": "$MISSING", "MCP_AGENT_QUERY_TIMEOUT": "$"},
			[2]string{"<unset>", "<unset>"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			settings, err := parseInstanceAdmin(tc.values)
			if err != nil {
				t.Fatal(err)
			}
			if got := [2]string{text(settings, 1), text(settings, 2)}; got != tc.want {
				t.Fatalf("texts %q, want %q", got, tc.want)
			}
		})
	}

	cyclic := map[string]string{"MCP_AGENT_MAX_RESULTS": "$LOOP", "LOOP": "$MCP_AGENT_MAX_RESULTS"}
	if _, err := parseInstanceAdmin(cyclic); err == nil {
		t.Fatal("a proxy cycle was accepted")
	}
}

// TestLoadAIIntegerTextsPrecedence is read_env followed by the process
// environment overlay: the file supplies the value when the variable is unset
// and the process environment wins when both set it.
func TestLoadAIIntegerTextsPrecedence(t *testing.T) {
	clearReportSettingsEnvironment(t)
	values := environment()
	values["MCP_AGENT_MAX_RESULTS"] = "123"
	values["MCP_AGENT_QUERY_TIMEOUT"] = "$FILE_TIMEOUT"
	values["FILE_TIMEOUT"] = "7"
	var content strings.Builder
	for key, value := range values {
		content.WriteString(key + "=" + value + "\n")
	}
	path := filepath.Join(t.TempDir(), ".env")
	if err := os.WriteFile(path, []byte(content.String()), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.InstanceAdmin.AIIntegerTexts[1] == nil || *cfg.InstanceAdmin.AIIntegerTexts[1] != "123" ||
		cfg.InstanceAdmin.AIIntegerTexts[2] == nil || *cfg.InstanceAdmin.AIIntegerTexts[2] != "7" {
		t.Fatalf("environment file texts: %v", cfg.InstanceAdmin.AIIntegerTexts)
	}
	t.Setenv("MCP_AGENT_MAX_RESULTS", "456")
	cfg, err = Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.InstanceAdmin.AIIntegerTexts[1] == nil || *cfg.InstanceAdmin.AIIntegerTexts[1] != "456" {
		t.Fatalf("process environment did not win: %v", cfg.InstanceAdmin.AIIntegerTexts)
	}
}

// clearReportSettingsEnvironment removes the two settings variables for one
// test.
func clearReportSettingsEnvironment(t *testing.T) {
	t.Helper()
	for _, name := range []string{"MCP_AGENT_MAX_RESULTS", "MCP_AGENT_QUERY_TIMEOUT"} {
		value, present := os.LookupEnv(name)
		if err := os.Unsetenv(name); err != nil {
			t.Fatal(err)
		}
		if present {
			t.Cleanup(func() { _ = os.Setenv(name, value) })
		}
	}
}
