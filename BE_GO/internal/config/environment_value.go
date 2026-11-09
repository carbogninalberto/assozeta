package config

import (
	"errors"
	"os"
	"unicode/utf8"
)

// EnvironmentValue is one setting as Load resolves it: the process
// environment wins over the optional deployment .env file (django-environ's
// read_env does not override variables that are already set). It is for
// settings Config does not carry, such as CURRENT_HOST for the Celery
// consumer's print tasks.
func EnvironmentValue(path, key string) (string, bool, error) {
	if value, ok := os.LookupEnv(key); ok {
		return value, true, nil
	}
	if path == "" {
		return "", false, nil
	}
	content, err := os.ReadFile(path)
	if err != nil || !utf8.Valid(content) {
		return "", false, errors.New("cannot read environment file")
	}
	value, ok := djangoEnvironmentFile(string(content))[key]
	return value, ok, nil
}
