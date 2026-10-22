// Package config reads the existing deployment environment. No credentials are
// duplicated in a Go-specific configuration file.
package config

import (
	"encoding/base64"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"strconv"
	"strings"
	"unicode/utf8"
)

type Config struct {
	Storage                       StorageSettings `json:"-"`
	ProviderCABundle              string          `json:"-"`
	SessionSecret                 string          `json:"-"`
	Debug                         bool
	RateLimitEnabled              bool
	GoogleClientID                string `json:"-"`
	GoogleClientSecret            string `json:"-"`
	AppleClientID                 string `json:"-"`
	AppleTeamID                   string `json:"-"`
	AppleKeyID                    string `json:"-"`
	AppleSecret                   string `json:"-"`
	FamilyRegistrationEnabled     bool
	FederationRegistrationEnabled bool
	MultipleRegistrationEnabled   bool
	JWTSigningKey                 string `json:"-"`
	JWTVerifyingKey               string `json:"-"`
	CSRFTrustedOrigins            []string
	CORSAllowAll                  bool
	CORSAllowedOrigins            []string
	RestoreLockPath               string
	ListenAddress                 string
	Version                       string
	databaseURL                   string
	RedisAddress                  string
	RedisUsername                 string
	RedisPassword                 string `json:"-"`
	RedisTLS                      bool
	RedisRootCertificate          string
}

// String intentionally omits credentials (including database URLs).
func (c Config) String() string      { return "Assozeta Go configuration (credentials redacted)" }
func (c Config) GoString() string    { return c.String() }
func (c Config) DatabaseURL() string { return c.databaseURL }

// Load gives process environment priority over the optional existing .env file.
// It does not mutate the process environment or log parser input on failure.
func Load(path string) (Config, error) {
	values := map[string]string{}
	if path != "" {
		content, err := os.ReadFile(path)
		if err != nil || !utf8.Valid(content) {
			return Config{}, errors.New("cannot read environment file")
		}
		values = djangoEnvironmentFile(string(content))
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
	debugValue, err := environmentString(values, "DEBUG", "", true)
	if err != nil {
		return Config{}, err
	}
	debug := djangoBoolean(debugValue)
	rateEnabled, err := registrationFlag(values, "RATELIMIT_ENABLE", !debug)
	if err != nil {
		return Config{}, err
	}
	corsAll, err := registrationFlag(values, "CORS_ORIGIN_ALLOW_ALL", debug)
	if err != nil {
		return Config{}, err
	}
	sessionSecret, err := environmentString(values, "SECRET_KEY", "", true)
	if err != nil {
		return Config{}, err
	}
	privateKey, err := environmentKey(values, "JWT_SECRET_KEY")
	if err != nil {
		return Config{}, err
	}
	publicKey, err := environmentKey(values, "JWT_PUBLIC_KEY")
	if err != nil {
		return Config{}, err
	}
	family, err := registrationFlag(values, "FAMILY_REGISTRATION_ENABLED", true)
	if err != nil {
		return Config{}, err
	}
	federation, err := registrationFlag(values, "FEDERATION_REGISTRATION_ENABLED", false)
	if err != nil {
		return Config{}, err
	}
	multiple, err := registrationFlag(values, "MULTIPLE_REGISTRATION_ENABLED", false)
	if err != nil {
		return Config{}, err
	}
	providerValues := make([]string, 6)
	for index, name := range []string{"SOCIAL_AUTH_GOOGLE_OAUTH2_KEY", "SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET", "SOCIAL_AUTH_APPLE_ID_CLIENT", "SOCIAL_AUTH_APPLE_ID_TEAM", "SOCIAL_AUTH_APPLE_ID_KEY", "SOCIAL_AUTH_APPLE_ID_SECRET"} {
		value, err := environmentString(values, name, "", index < 2)
		if err != nil {
			return Config{}, err
		}
		providerValues[index] = value
	}
	origins, err := environmentList(values, "CSRF_TRUSTED_ORIGINS")
	if err != nil {
		return Config{}, err
	}
	corsOrigins, err := environmentList(values, "CORS_ALLOWED_ORIGINS")
	if err != nil {
		return Config{}, err
	}
	restoreLockPath, err := environmentString(values, "DATA_RESTORE_LOCK_PATH", "", false)
	if err != nil {
		return Config{}, err
	}
	storage, err := parseStorage(values)
	if err != nil {
		return Config{}, err
	}
	providerCABundle := values["REQUESTS_CA_BUNDLE"]
	if providerCABundle == "" {
		providerCABundle = values["CURL_CA_BUNDLE"]
	}
	return Config{ProviderCABundle: providerCABundle, Storage: storage, SessionSecret: sessionSecret, Debug: debug, RateLimitEnabled: rateEnabled, GoogleClientID: providerValues[0], GoogleClientSecret: providerValues[1], AppleClientID: providerValues[2], AppleTeamID: providerValues[3], AppleKeyID: providerValues[4], AppleSecret: environmentMultiline(providerValues[5]),
		FamilyRegistrationEnabled: family, FederationRegistrationEnabled: federation, MultipleRegistrationEnabled: multiple, JWTSigningKey: privateKey, JWTVerifyingKey: publicKey,
		CSRFTrustedOrigins: origins, CORSAllowAll: corsAll, CORSAllowedOrigins: corsOrigins, RestoreLockPath: restoreLockPath, ListenAddress: address, Version: get("RUNNING_VERSION", "v0.0.0"), databaseURL: dsn.String(),
		RedisAddress:  net.JoinHostPort(get("REDIS_HOST", "localhost"), redisPort),
		RedisUsername: get("REDIS_USERNAME", "default"), RedisPassword: values["REDIS_PASSWORD"],
		// Existing Django code uses string truthiness, not env.bool, for this flag.
		RedisTLS: values["REDIS_SSLAUTH"] != "", RedisRootCertificate: values["REDIS_SSLCERT"]}, nil
}

func environmentList(values map[string]string, name string) ([]string, error) {
	value, err := environmentString(values, name, "", false)
	if err != nil {
		return nil, err
	}
	items := strings.FieldsFunc(value, func(r rune) bool { return r == ',' })
	// django-environ casts a nonempty resolved list again, raising AttributeError.
	// Preserve startup rejection rather than silently accepting different policy.
	if strings.HasPrefix(values[name], "$") && len(items) > 0 {
		return nil, errors.New("invalid " + name + " proxy")
	}
	return items, nil
}

// Match core.settings.env_key: nonempty base64 takes priority; raw values use
// django-environ's multiline expansion. Errors identify only the variable name.
func environmentKey(values map[string]string, name string) (string, error) {
	encoded, err := environmentString(values, name+"_B64", "", false)
	if err != nil {
		return "", err
	}
	if encoded != "" {
		for _, r := range encoded {
			if r > 127 {
				return "", fmt.Errorf("invalid %s_B64", name)
			}
		}
		// Python b64decode(validate=False) ignores non-alphabet characters.
		filtered := strings.Map(func(r rune) rune {
			if strings.ContainsRune("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=", r) {
				return r
			}
			return -1
		}, encoded)
		decoded, err := base64.StdEncoding.DecodeString(filtered)
		if err != nil || !utf8.Valid(decoded) {
			return "", fmt.Errorf("invalid %s_B64", name)
		}
		return string(decoded), nil
	}
	value, err := environmentString(values, name, "", true)
	if err != nil {
		return "", err
	}
	return environmentMultiline(value), nil
}

func environmentMultiline(value string) string {
	return strings.NewReplacer(`\r\n`, "\n", `\n`, "\n").Replace(value)
}

// Only Env-based settings follow proxies. os.environ-based DB/Redis/PgBouncer
// values stay literal. The originating default/required rule follows every hop.
func environmentString(values map[string]string, name, fallback string, required bool) (string, error) {
	origin := name
	seen := map[string]bool{}
	for {
		if seen[name] {
			return "", fmt.Errorf("cyclic %s environment proxy", origin)
		}
		seen[name] = true
		value, present := values[name]
		if !present {
			if required {
				return "", fmt.Errorf("%s is required", origin)
			}
			return fallback, nil
		}
		if strings.HasPrefix(value, "$") {
			name = strings.TrimLeft(value, "$")
			continue
		}
		return value, nil
	}
}

func validPort(value string) bool {
	number, err := strconv.Atoi(value)
	return err == nil && number > 0 && number <= 65535
}
