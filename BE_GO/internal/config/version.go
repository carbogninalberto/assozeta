package config

import (
	"errors"
	"os"
	"strings"
	"unicode/utf8"
)

// Match Django's image-owned VERSION precedence. An absent/empty file permits
// development's existing RUNNING_VERSION fallback; unreadable metadata does not.
func runningVersion(path, fallback string) (string, error) {
	content, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return fallback, nil
	}
	if err != nil || !utf8.Valid(content) {
		return "", errors.New("cannot read image version")
	}
	if version := strings.TrimSpace(string(content)); version != "" {
		return version, nil
	}
	return fallback, nil
}
