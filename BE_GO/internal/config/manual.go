package config

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
)

func parseManual(values map[string]string) (manual.Settings, error) {
	settings := manual.Settings{}
	fields := []struct {
		key, fallback string
		target        *string
	}{
		{"MANUAL_INDEX_PATH", "manuale/index.json", &settings.IndexPath},
		{"MANUAL_ASSET_ROOT", "", &settings.AssetRoot},
		{"MANUAL_CORPUS_BASE_URL", "", &settings.CorpusBaseURL},
		{"MANUAL_APPLICATION_REVISION", "", &settings.ApplicationRevision},
		{"MANUAL_SOURCE_ROOT", "", &settings.SourceRoot},
		{"ASSOZETA_MANUAL_RUN_ID", "", &settings.RunID},
		{"MANUAL_DEVELOPMENT_ROOT", "manuale/development", &settings.DevelopmentRoot},
		{"ASSOZETA_DEPLOYMENT_MODE", "development", &settings.DeploymentMode},
	}
	for _, field := range fields {
		value, err := environmentString(values, field.key, field.fallback, false)
		if err != nil {
			return settings, err
		}
		*field.target = value
	}
	return settings, nil
}

// Image-owned metadata has the same authority as Django's BASE_DIR files.
// Explicit environment paths remain relative to the working directory.
func manualImageSettings(root string, settings manual.Settings, values map[string]string) (manual.Settings, error) {
	settings.BundlePath = filepath.Join(root, "release-manual")
	if _, ok := values["MANUAL_INDEX_PATH"]; !ok {
		settings.IndexPath = filepath.Join(root, "manuale/index.json")
	}
	if _, ok := values["MANUAL_DEVELOPMENT_ROOT"]; !ok {
		settings.DevelopmentRoot = filepath.Join(root, "manuale/development")
	}
	for _, name := range []string{"CODE_REVISION", "MANUAL_REQUIRED"} {
		path := filepath.Join(root, name)
		info, err := os.Stat(path)
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil {
			return settings, errors.New("cannot inspect manual image metadata")
		}
		if name == "MANUAL_REQUIRED" && !info.Mode().IsRegular() {
			continue
		}
		content, err := os.ReadFile(path)
		if err != nil || !utf8.Valid(content) {
			return settings, errors.New("cannot read manual image metadata")
		}
		value := strings.TrimSpace(string(content))
		if name == "CODE_REVISION" {
			if value == "unknown" {
				value = ""
			}
			settings.ApplicationRevision = value
		} else {
			settings.BundleRequired = value == "true"
		}
	}
	return settings, nil
}
