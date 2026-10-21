//go:build sessionhttpintegration

package middleware

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
	"github.com/labstack/echo/v4"
	"github.com/redis/go-redis/v9"
)

func TestSessionAuditPortableHTTP(t *testing.T) {
	var fixture struct {
		Secret, Address string
		Payloads        map[string]string
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_SESSION_HTTP_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil {
		t.Fatal("run accounts.py --only session-auth")
	}
	client := redis.NewClient(&redis.Options{Addr: fixture.Address, Password: "synthetic-identity-only", DB: 1, MaxRetries: -1, ContextTimeoutEnabled: true, PoolSize: 4})
	defer client.Close()
	cache, err := repository.NewDjangoSessionCache(client)
	if err != nil {
		t.Fatal(err)
	}
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	resolver := service.NewSessionAuthentication(cache, repository.NewSessionAccounts(db), fixture.Secret)
	e := echo.New()
	e.POST("/__fixtures/session/reset", func(c echo.Context) error {
		for key, value := range fixture.Payloads {
			decoded, err := base64.StdEncoding.DecodeString(value)
			if err != nil {
				return err
			}
			if err := client.Set(c.Request().Context(), key, decoded, 300*time.Second).Err(); err != nil {
				return err
			}
		}
		return c.JSON(200, map[string]bool{"reset": true})
	})
	e.Any("/*", func(c echo.Context) error {
		audit, err := SessionAuditContext(c.Request())
		if err != nil {
			return err
		}
		raw, err := io.ReadAll(c.Request().Body)
		if err != nil {
			return err
		}
		status := 200
		if strings.HasPrefix(c.Request().URL.Path, "/oauth2/status/") {
			status, err = strconv.Atoi(strings.TrimPrefix(c.Request().URL.Path, "/oauth2/status/"))
			if err != nil {
				return err
			}
		}
		if vary, ok := c.Request().Header["X-Fixture-Vary"]; ok {
			c.Response().Header().Set("Vary", vary[0])
		}
		if c.Request().Header.Get("X-Fixture-Other-Cookie") != "" {
			c.Response().Header().Add("Set-Cookie", "fixture=retained; Path=/")
		}
		if c.Request().Header.Get("X-Fixture-Session-Cookie") != "" {
			c.Response().Header().Add("Set-Cookie", "sessionid=fixture-replacement; Path=/")
		}
		sum := sha256.Sum256(raw)
		if c.Request().Header.Get("X-Fixture-Stream") != "" {
			c.Response().Header().Set("Content-Type", "application/json")
			c.Response().Flush()
			return json.NewEncoder(c.Response()).Encode(map[string]any{"audit": audit, "sha256": hex.EncodeToString(sum[:])})
		}
		return c.JSON(status, map[string]any{"audit": audit, "sha256": hex.EncodeToString(sum[:])})
	}, DjangoSessionAudit(resolver))
	server := httptest.NewServer(e)
	defer server.Close()
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Getenv("ASSOZETA_CONTRACT_PYTHON"), "-m", "unittest", "TESTS.contracts.test_session_http", "-v")
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "TESTS_BASE_URL="+server.URL)
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("portable session HTTP failed: %v\n%s", err, output)
	}
	t.Logf("%s", output)
	if db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 || client.PoolStats().TotalConns > 4 {
		t.Fatal("HTTP session pool bound exceeded")
	}
}
