package config

import (
	"github.com/jackc/pgx/v5"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func environment() map[string]string {
	return map[string]string{
		"AWS_ACCESS_KEY_ID":       "fixture-access",
		"AWS_SECRET_ACCESS_KEY":   "fixture-secret",
		"AWS_STORAGE_BUCKET_NAME": "fixture-bucket",
		"AWS_S3_REGION_NAME":      "us-east-1",
		"AWS_S3_ENDPOINT_URL":     "http://127.0.0.1:9000",
		"AWS_LOCATION":            "private",

		"DBTYPE": "POSTGRES", "DBNAME": "assozeta_contract", "DBUSER": "contract",
		"DBPASSWORD": "special ' \\ @ : ? # password", "DBHOST": "/tmp/contract", "DBPORT": "5432",
		"REDIS_HOST": "localhost", "REDIS_PASSWORD": "secret", "REDIS_PORT": "6379",
		"SECRET_KEY": "synthetic-session-secret", "DEBUG": "False", "SOCIAL_AUTH_GOOGLE_OAUTH2_KEY": "", "SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET": "", "JWT_SECRET_KEY": "synthetic-private", "JWT_PUBLIC_KEY": "synthetic-public",
	}
}
func TestExistingDatabaseConfigurationPreservesValues(t *testing.T) {
	env := environment()
	cfg, err := Parse(env)
	if err != nil {
		t.Fatal(err)
	}
	db, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		t.Fatal("invalid database URL")
	}
	if db.Password != env["DBPASSWORD"] || db.Host != env["DBHOST"] || db.Database != env["DBNAME"] || db.User != env["DBUSER"] {
		t.Fatal("database settings changed")
	}
	if cfg.ListenAddress != ":8000" || cfg.Version != "v0.0.0" {
		t.Fatal("incompatible defaults")
	}
}
func TestPgBouncerUsesExactExistingBooleanConvention(t *testing.T) {
	for _, value := range []string{"True", "true", "1", "False", ""} {
		env := environment()
		env["USE_PGBOUNCER"] = value
		env["PGBOUNCER_HOST"] = "pool"
		env["PGBOUNCER_PORT"] = "6432"
		cfg, err := Parse(env)
		if err != nil {
			t.Fatal(err)
		}
		parsed, err := pgx.ParseConfig(cfg.DatabaseURL())
		if err != nil {
			t.Fatal("parse failed")
		}
		want := env["DBHOST"]
		if value == "True" {
			want = "pool"
		}
		if parsed.Host != want {
			t.Fatalf("%q host = %q", value, parsed.Host)
		}
	}
}
func TestRedisLegacyTLSAndDatabaseTLS(t *testing.T) {
	for _, value := range []string{"", "False", "True", "0"} {
		env := environment()
		env["REDIS_SSLAUTH"] = value
		env["DBSSLMODE"] = "verify-full"
		env["DBSSLROOTCERT"] = "/tmp/root.pem"
		cfg, err := Parse(env)
		if err != nil {
			t.Fatal(err)
		}
		if cfg.RedisTLS != (value != "") {
			t.Fatalf("changed Django REDIS_SSLAUTH semantics for %q", value)
		}
		if !strings.Contains(cfg.DatabaseURL(), "sslmode=verify-full") {
			t.Fatal("missing SSL settings")
		}
	}
}
func TestInvalidSettingsNeverExposeSecrets(t *testing.T) {
	for _, key := range []string{"DBPORT", "REDIS_PORT", "DBTYPE", "GO_LISTEN_ADDR"} {
		env := environment()
		env[key] = "sensitive-invalid-value"
		_, err := Parse(env)
		if err == nil || strings.Contains(err.Error(), "sensitive-invalid-value") || strings.Contains(err.Error(), env["DBPASSWORD"]) {
			t.Fatalf("unsafe error for %s", key)
		}
	}
}
func TestDotenvDoesNotOverrideProcessAndKeepsQuotedValues(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".env")
	if err := os.WriteFile(path, []byte("AWS_ACCESS_KEY_ID=fixture-access\nAWS_SECRET_ACCESS_KEY=fixture-secret\nAWS_STORAGE_BUCKET_NAME=fixture-bucket\nAWS_S3_REGION_NAME=us-east-1\nAWS_S3_ENDPOINT_URL=http://127.0.0.1:9000\nAWS_LOCATION=private\nSECRET_KEY=synthetic-session-secret\nDEBUG=False\nDBTYPE=POSTGRES\nDBNAME=file\nDBUSER=contract\nDBPASSWORD='a # quoted password'\nDBHOST=localhost\nDBPORT=5432\nSOCIAL_AUTH_GOOGLE_OAUTH2_KEY=\nSOCIAL_AUTH_GOOGLE_OAUTH2_SECRET=\nJWT_SECRET_KEY=synthetic-private\nJWT_PUBLIC_KEY=synthetic-public\n"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DBNAME", "process")
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	db, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		t.Fatal("invalid URL")
	}
	if db.Database != "process" || db.Password != "a # quoted password" {
		t.Fatal("dotenv precedence or quoting changed")
	}
	if _, exists := os.LookupEnv("DBPASSWORD"); exists {
		t.Fatal("Load must not publish credentials into process environment")
	}
}

func TestJWTEnvironmentMatchesDjangoKeyPrecedence(t *testing.T) {
	env := environment()
	env["JWT_SECRET_KEY"] = "raw\\nprivate\\nkey"
	env["JWT_PUBLIC_KEY"] = "raw\\npublic\\nkey"
	cfg, err := Parse(env)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.JWTSigningKey != "raw\nprivate\nkey" || cfg.JWTVerifyingKey != "raw\npublic\nkey" {
		t.Fatal("literal multiline keys not expanded")
	}
	env["JWT_SECRET_KEY_B64"] = "ZW5jb2RlZCBwcml2YXRl"
	env["JWT_PUBLIC_KEY_B64"] = "ZW5j b2RlZCBwdWJsaWM=\n"
	cfg, err = Parse(env)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.JWTSigningKey != "encoded private" || cfg.JWTVerifyingKey != "encoded public" {
		t.Fatal("base64 key precedence changed")
	}
	env["JWT_SECRET_KEY_B64"] = "invalid-secret-value"
	_, err = Parse(env)
	if err == nil || strings.Contains(err.Error(), "invalid-secret-value") {
		t.Fatal("invalid encoded key error must not leak input")
	}
}

func TestRequestsCABundleEnvironmentPrecedence(t *testing.T) {
	for _, tc := range []struct{ requests, curl, want string }{
		{"", "", ""}, {"", "curl.pem", "curl.pem"}, {"requests.pem", "curl.pem", "requests.pem"}, {" requests.pem ", "curl.pem", " requests.pem "},
	} {
		env := environment()
		env["REQUESTS_CA_BUNDLE"] = tc.requests
		env["CURL_CA_BUNDLE"] = tc.curl
		cfg, err := Parse(env)
		if err != nil {
			t.Fatal(err)
		}
		if cfg.ProviderCABundle != tc.want {
			t.Fatal("requests CA precedence differs")
		}
	}
}

func TestSenjaWebhookSecretPresenceAndProxy(t *testing.T) {
	env := environment()
	if cfg, err := Parse(env); err != nil || cfg.SenjaWebhookSecret != nil {
		t.Fatal("absent secret must stay unset", err)
	}
	env["SENJA_WEBHOOK_SECRET"], env["SENJA_SOURCE"] = "$SENJA_SOURCE", "proxied-webhook-secret"
	if cfg, err := Parse(env); err != nil || cfg.SenjaWebhookSecret == nil || *cfg.SenjaWebhookSecret != "proxied-webhook-secret" {
		t.Fatal("proxied secret differs", err)
	}
	env["SENJA_WEBHOOK_SECRET"] = ""
	if cfg, err := Parse(env); err != nil || cfg.SenjaWebhookSecret == nil || *cfg.SenjaWebhookSecret != "" {
		t.Fatal("empty secret must stay set", err)
	}
}
