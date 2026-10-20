// Package config reads the existing deployment environment. No credentials are
// duplicated in a Go-specific configuration file.
package config

import (
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"

	"github.com/joho/godotenv"
)

type Config struct {
	CSRFTrustedOrigins   []string
	ListenAddress        string
	Version              string
	databaseURL          string
	RedisAddress         string
	RedisUsername        string
	RedisPassword        string
	RedisTLS             bool
	RedisRootCertificate string
}

// String intentionally omits credentials (including database URLs).
func (c Config) String() string      { return "Assozeta Go configuration (credentials redacted)" }
func (c Config) DatabaseURL() string { return c.databaseURL }

// Load gives process environment priority over the optional existing .env file.
// It does not mutate the process environment or log parser input on failure.
func Load(path string) (Config, error) {
	values := map[string]string{}
	if path != "" {
		parsed, err := godotenv.Read(path)
		if err != nil {
			return Config{}, errors.New("cannot read environment file")
		}
		values = parsed
	}
	for _, entry := range os.Environ() {
		key, value, ok := strings.Cut(entry, "=")
		if ok {
			values[key] = value
		}
	}
	return Parse(values)
}

func Parse(values map[string]string) (Config, error) {
	get := func(key, fallback string) string {
		value, ok := values[key]
		if !ok {
			return fallback
		}
		return value
	}
	for _, key := range []string{"DBNAME", "DBUSER", "DBHOST"} {
		if values[key] == "" {
			return Config{}, fmt.Errorf("%s is required", key)
		}
	}
	if values["DBTYPE"] != "POSTGRES" {
		return Config{}, errors.New("DBTYPE must select PostgreSQL")
	}
	host, port := values["DBHOST"], get("DBPORT", "5432")
	if values["USE_PGBOUNCER"] == "True" {
		host, port = get("PGBOUNCER_HOST", "localhost"), get("PGBOUNCER_PORT", "6432")
	}
	if !validPort(port) {
		return Config{}, errors.New("invalid database port")
	}
	redisPort := get("REDIS_PORT", "6379")
	if !validPort(redisPort) {
		return Config{}, errors.New("invalid Redis port")
	}
	address := get("GO_LISTEN_ADDR", ":8000")
	_, listenPort, err := net.SplitHostPort(address)
	if err != nil || !validPort(listenPort) {
		return Config{}, errors.New("invalid GO_LISTEN_ADDR")
	}
	query := url.Values{"host": {host}, "port": {port}, "dbname": {values["DBNAME"]}, "connect_timeout": {"10"}}
	// core.settings applies these options only when BOTH are supplied.
	if values["DBSSLMODE"] != "" && values["DBSSLROOTCERT"] != "" {
		query.Set("sslmode", values["DBSSLMODE"])
		query.Set("sslrootcert", values["DBSSLROOTCERT"])
	}
	dsn := url.URL{Scheme: "postgres", User: url.UserPassword(values["DBUSER"], values["DBPASSWORD"]), RawQuery: query.Encode()}
	return Config{CSRFTrustedOrigins: strings.FieldsFunc(get("CSRF_TRUSTED_ORIGINS", ""), func(r rune) bool { return r == ',' }), ListenAddress: address, Version: get("RUNNING_VERSION", "v0.0.0"), databaseURL: dsn.String(),
		RedisAddress:  net.JoinHostPort(get("REDIS_HOST", "localhost"), redisPort),
		RedisUsername: get("REDIS_USERNAME", "default"), RedisPassword: values["REDIS_PASSWORD"],
		// Existing Django code uses string truthiness, not env.bool, for this flag.
		RedisTLS: values["REDIS_SSLAUTH"] != "", RedisRootCertificate: values["REDIS_SSLCERT"]}, nil
}

func validPort(value string) bool {
	number, err := strconv.Atoi(value)
	return err == nil && number > 0 && number <= 65535
}
