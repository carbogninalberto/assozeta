package service

import (
	"context"
	"errors"
	"math/big"
	"os"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// settingsStore answers the instance integration record of loadReportSettings.
type settingsStore struct{ stored *string }

func (s settingsStore) ReportIntegration(context.Context) (bool, *string, error) {
	return s.stored != nil, s.stored, nil
}

func storedSettings(text string) settingsStore { return settingsStore{stored: &text} }

// clearReportEnvironment removes the two settings variables for one test.
func clearReportEnvironment(t *testing.T) {
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

// withReportEnvironment installs the deployment environment's texts for one
// test and restores the previous wiring.
func withReportEnvironment(t *testing.T, maxResults, queryTimeout *string) {
	t.Helper()
	previous := reportEnvironment
	SetReportEnvironment(maxResults, queryTimeout)
	t.Cleanup(func() { reportEnvironment = previous })
}

// bigText is a setting's decimal text ("<nil>" when it is unset).
func bigText(number *big.Int) string {
	if number == nil {
		return "<nil>"
	}
	return number.String()
}

// requireReportSettings checks both settings' decimal texts.
func requireReportSettings(t *testing.T, settings dto.ReportSettings, err error, maximum, timeout string) {
	t.Helper()
	if err != nil || bigText(settings.MaxResults) != maximum || bigText(settings.QueryTimeout) != timeout {
		t.Fatalf("settings {%s, %s} err %v, want {%s, %s}",
			bigText(settings.MaxResults), bigText(settings.QueryTimeout), err, maximum, timeout)
	}
}

func TestLoadReportSettingsStored(t *testing.T) {
	load := func(t *testing.T, text string) (dto.ReportSettings, error) {
		t.Helper()
		clearReportEnvironment(t)
		return loadReportSettings(context.Background(), storedSettings(text))
	}
	settings, err := load(t, `{"ai": {"revision": 3, "settings": {"max_results": 100, "query_timeout": 5}}}`)
	requireReportSettings(t, settings, err, "100", "5")
	// A stored null or an absent record falls back to the environment.
	if settings, err = load(t, `{"ai": {"settings": null}}`); err != nil {
		t.Fatal(err)
	}
	requireReportSettings(t, settings, err, "5000", "10")
	if settings, err = load(t, `{}`); err != nil {
		t.Fatal(err)
	}
	requireReportSettings(t, settings, err, "5000", "10")

	for _, item := range []struct{ name, text string }{
		{"missing-max", `{"ai": {"settings": {"query_timeout": 5}}}`},
		{"missing-timeout", `{"ai": {"settings": {"max_results": 100}}}`},
		{"zero", `{"ai": {"settings": {"max_results": 0, "query_timeout": 5}}}`},
		{"negative", `{"ai": {"settings": {"max_results": 100, "query_timeout": -1}}}`},
		{"float", `{"ai": {"settings": {"max_results": 100.5, "query_timeout": 5}}}`},
		{"string", `{"ai": {"settings": {"max_results": "100", "query_timeout": 5}}}`},
		{"null-setting", `{"ai": {"settings": {"max_results": null, "query_timeout": 5}}}`},
		{"settings-list", `{"ai": {"settings": []}}`},
		{"record-list", `{"ai": []}`},
		{"root-list", `[]`},
	} {
		t.Run(item.name, func(t *testing.T) {
			_, err := load(t, item.text)
			if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
				t.Fatalf("expected an application-state error, got %v", err)
			}
		})
	}
}

func TestLoadReportSettingsEnvironment(t *testing.T) {
	clearReportEnvironment(t)
	t.Setenv("MCP_AGENT_MAX_RESULTS", " 1_000 ")
	t.Setenv("MCP_AGENT_QUERY_TIMEOUT", "٣")
	settings, err := loadReportSettings(context.Background(), settingsStore{})
	requireReportSettings(t, settings, err, "1000", "3")

	for _, item := range []struct{ name, value string }{
		{"empty", ""}, {"text", "abc"}, {"float", "1.5"}, {"plus-sign-only", "+"},
	} {
		t.Run(item.name, func(t *testing.T) {
			clearReportEnvironment(t)
			t.Setenv("MCP_AGENT_MAX_RESULTS", item.value)
			_, err := loadReportSettings(context.Background(), settingsStore{})
			if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
				t.Fatalf("expected an application-state error, got %v", err)
			}
		})
	}

	// Django's env.int keeps 0 ('' after `or ''`) and negatives; the tool
	// hands the value to PostgreSQL at use.
	for _, item := range []struct{ name, value string }{
		{"zero", "0"}, {"negative", "-5"},
	} {
		t.Run(item.name, func(t *testing.T) {
			clearReportEnvironment(t)
			t.Setenv("MCP_AGENT_QUERY_TIMEOUT", item.value)
			settings, err := loadReportSettings(context.Background(), settingsStore{})
			requireReportSettings(t, settings, err, "5000", item.value)
		})
		t.Run("max-results-"+item.name, func(t *testing.T) {
			clearReportEnvironment(t)
			t.Setenv("MCP_AGENT_MAX_RESULTS", item.value)
			settings, err := loadReportSettings(context.Background(), settingsStore{})
			requireReportSettings(t, settings, err, item.value, "10")
		})
	}
}

// TestLoadReportSettingsConfiguredTexts is SetReportEnvironment: the texts
// config resolved (deployment file, $ proxies) are env.int's input, and
// Python's int() keeps every digit.
func TestLoadReportSettingsConfiguredTexts(t *testing.T) {
	text := func(value string) *string { return &value }
	for _, item := range []struct {
		name                     string
		maxResults, queryTimeout *string
		wantMaximum, wantTimeout string
	}{
		{"beyond-int64", text("99999999999999999999"), text("-99999999999999999999"), "99999999999999999999", "-99999999999999999999"},
		{"underscores", text("1_0"), text("1_0"), "10", "10"},
		{"spaces", text(" 7 "), text(" 7 "), "7", "7"},
		{"unicode-digits", text("٣"), text("٣"), "3", "3"},
		{"max-digits", text(strings.Repeat("1", 4300)), text("1"), strings.Repeat("1", 4300), "1"},
		{"unset", nil, nil, "5000", "10"},
	} {
		t.Run(item.name, func(t *testing.T) {
			clearReportEnvironment(t)
			// The process environment is not read once the texts are installed.
			t.Setenv("MCP_AGENT_MAX_RESULTS", "1")
			t.Setenv("MCP_AGENT_QUERY_TIMEOUT", "1")
			withReportEnvironment(t, item.maxResults, item.queryTimeout)
			settings, err := loadReportSettings(context.Background(), settingsStore{})
			requireReportSettings(t, settings, err, item.wantMaximum, item.wantTimeout)
		})
	}
	// The timeout text of a 20-digit value keeps every digit (the 25-digit
	// product PostgreSQL rejects with its own message).
	withReportEnvironment(t, text("99999999999999999999"), text("99999999999999999999"))
	settings, err := loadReportSettings(context.Background(), settingsStore{})
	if err != nil || settings.QueryTimeoutText() != "99999999999999999999000" {
		t.Fatalf("timeout text: %q, %v", settings.QueryTimeoutText(), err)
	}

	// Two texts dto.PythonIntText accepts are int() ValueErrors: a control
	// byte int() does not strip (\x1c-\x1f) and more than
	// sys.int_info.default_max_str_digits digits.
	for _, item := range []struct{ name, value string }{
		{"leading-unit-separator", "\x1c12"},
		{"trailing-unit-separator", "12\x1f"},
		{"too-many-digits", strings.Repeat("1", 4301)},
		{"leading-zeros", strings.Repeat("0", 4300) + "1"},
	} {
		t.Run(item.name, func(t *testing.T) {
			clearReportEnvironment(t)
			withReportEnvironment(t, text(item.value), text("1"))
			_, err := loadReportSettings(context.Background(), settingsStore{})
			if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
				t.Fatalf("expected an application-state error, got %v", err)
			}
		})
	}
}
