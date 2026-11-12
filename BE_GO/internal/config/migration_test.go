package config

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
)

func TestMigrationDatabaseUsesOriginalPostgresAlongsideOptionalPooler(t *testing.T) {
	for _, pooled := range []string{"True", "true", "1", "False", ""} {
		t.Run(pooled, func(t *testing.T) {
			env := environment()
			env["DBHOST"], env["DBPORT"] = "direct-postgres.internal", "5442"
			env["USE_PGBOUNCER"], env["PGBOUNCER_HOST"], env["PGBOUNCER_PORT"] = pooled, "transaction-pool.internal", "6432"
			env["DBSSLMODE"], env["DBSSLROOTCERT"] = "verify-full", "/private/fixture-root.pem"
			cfg, err := Parse(env)
			if err != nil {
				t.Fatal(err)
			}
			direct, err := url.Parse(cfg.MigrationDatabaseURL())
			if err != nil {
				t.Fatal("invalid direct database URL")
			}
			runtime, err := url.Parse(cfg.DatabaseURL())
			if err != nil {
				t.Fatal("invalid runtime database URL")
			}
			if direct.Query().Get("host") != env["DBHOST"] || direct.Query().Get("port") != env["DBPORT"] {
				t.Fatal("migration routed through pooler or dropped original port")
			}
			wantHost, wantPort := env["DBHOST"], env["DBPORT"]
			if pooled == "True" {
				wantHost, wantPort = env["PGBOUNCER_HOST"], env["PGBOUNCER_PORT"]
			}
			if runtime.Query().Get("host") != wantHost || runtime.Query().Get("port") != wantPort {
				t.Fatal("runtime pooler selection changed")
			}
			for _, dsn := range []*url.URL{direct, runtime} {
				password, present := dsn.User.Password()
				if !present || password != env["DBPASSWORD"] || dsn.User.Username() != env["DBUSER"] || dsn.Query().Get("dbname") != env["DBNAME"] {
					t.Fatal("database identity or credential encoding changed")
				}
				if dsn.Query().Get("sslmode") != "verify-full" || dsn.Query().Get("sslrootcert") != env["DBSSLROOTCERT"] || dsn.Query().Get("connect_timeout") != "10" {
					t.Fatal("database TLS or connection bounds changed")
				}
			}
			encoded, err := json.Marshal(cfg)
			if err != nil {
				t.Fatal(err)
			}
			for _, rendered := range []string{fmt.Sprint(cfg), fmt.Sprintf("%#v", cfg), string(encoded)} {
				for _, private := range []string{env["DBPASSWORD"], env["DBHOST"], env["PGBOUNCER_HOST"], cfg.MigrationDatabaseURL(), cfg.DatabaseURL()} {
					if strings.Contains(rendered, private) {
						t.Fatal("configuration diagnostics expose database details")
					}
				}
			}
		})
	}
}

func TestUnusedDirectPortDoesNotChangePooledRuntimeAcceptance(t *testing.T) {
	env := environment()
	env["USE_PGBOUNCER"], env["PGBOUNCER_HOST"], env["PGBOUNCER_PORT"] = "True", "pool", "6432"
	env["DBPORT"] = "private-invalid-direct-port-canary"
	cfg, err := Parse(env)
	if err != nil {
		t.Fatal("unused direct port changed pooled configuration acceptance")
	}
	if _, err = pgx.ParseConfig(cfg.DatabaseURL()); err != nil {
		t.Fatal("pooled runtime database became invalid")
	}
	if _, err = pgx.ParseConfig(cfg.MigrationDatabaseURL()); err == nil {
		t.Fatal("migration accepted an invalid direct endpoint")
	}
	for _, key := range []string{"DBHOST", "DBUSER", "DBNAME"} {
		invalid := make(map[string]string, len(env))
		for name, value := range env {
			invalid[name] = value
		}
		delete(invalid, key)
		_, err := Parse(invalid)
		if err == nil || !strings.Contains(err.Error(), key) {
			t.Fatal("required original database setting changed")
		}
		if strings.Contains(err.Error(), env["DBPASSWORD"]) || strings.Contains(err.Error(), env["DBPORT"]) {
			t.Fatal("invalid environment leaked private database settings")
		}
	}
}

func TestMigrationDatabaseRetainsExistingDefaultsAndConditionalTLS(t *testing.T) {
	for _, fields := range []map[string]string{{}, {"DBSSLMODE": "verify-full"}, {"DBSSLROOTCERT": "/private/fixture-root.pem"}} {
		env := environment()
		delete(env, "DBPORT")
		for key, value := range fields {
			env[key] = value
		}
		cfg, err := Parse(env)
		if err != nil {
			t.Fatal(err)
		}
		direct, err := url.Parse(cfg.MigrationDatabaseURL())
		if err != nil {
			t.Fatal(err)
		}
		if direct.Query().Get("port") != "5432" || direct.Query().Has("sslmode") || direct.Query().Has("sslrootcert") {
			t.Fatal("direct migration changed default port or conditional Django TLS policy")
		}
		if cfg.MigrationDatabaseURL() != cfg.DatabaseURL() {
			t.Fatal("unpooled deployment needs no second configuration")
		}
	}
}
