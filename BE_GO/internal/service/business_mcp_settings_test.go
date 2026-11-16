package service

import (
	"context"
	"errors"
	"os"
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

func TestLoadReportSettingsStored(t *testing.T) {
	load := func(t *testing.T, text string) (dto.ReportSettings, error) {
		t.Helper()
		clearReportEnvironment(t)
		return loadReportSettings(context.Background(), storedSettings(text))
	}
	settings, err := load(t, `{"ai": {"revision": 3, "settings": {"max_results": 100, "query_timeout": 5}}}`)
	if err != nil || settings.MaxResults != 100 || settings.QueryTimeout != 5 {
		t.Fatalf("settings %+v err %v", settings, err)
	}
	// A stored null or an absent record falls back to the environment.
	if settings, err = load(t, `{"ai": {"settings": null}}`); err != nil || settings.MaxResults != 5000 ||
		settings.QueryTimeout != 10 {
		t.Fatalf("environment settings %+v err %v", settings, err)
	}
	if settings, err = load(t, `{}`); err != nil || settings.MaxResults != 5000 || settings.QueryTimeout != 10 {
		t.Fatalf("empty settings %+v err %v", settings, err)
	}

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
	if err != nil || settings.MaxResults != 1000 || settings.QueryTimeout != 3 {
		t.Fatalf("settings %+v err %v", settings, err)
	}

	for _, item := range []struct{ name, value string }{
		{"empty", ""}, {"text", "abc"}, {"float", "1.5"}, {"plus-sign-only", "+"},
		{"beyond-int64", "99999999999999999999999999"},
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
	// raises on them at use.
	for _, item := range []struct {
		name  string
		value string
		want  int64
	}{
		{"zero", "0", 0}, {"negative", "-5", -5},
	} {
		t.Run(item.name, func(t *testing.T) {
			clearReportEnvironment(t)
			t.Setenv("MCP_AGENT_QUERY_TIMEOUT", item.value)
			settings, err := loadReportSettings(context.Background(), settingsStore{})
			if err != nil || settings.QueryTimeout != item.want || settings.MaxResults != 5000 {
				t.Fatalf("settings %+v err %v, want query timeout %d", settings, err, item.want)
			}
		})
	}
	for _, item := range []struct {
		name  string
		value string
		want  int64
	}{
		{"zero", "0", 0}, {"negative", "-5", -5},
	} {
		t.Run("max-results-"+item.name, func(t *testing.T) {
			clearReportEnvironment(t)
			t.Setenv("MCP_AGENT_MAX_RESULTS", item.value)
			settings, err := loadReportSettings(context.Background(), settingsStore{})
			if err != nil || settings.MaxResults != item.want || settings.QueryTimeout != 10 {
				t.Fatalf("settings %+v err %v, want max results %d", settings, err, item.want)
			}
		})
	}
}
