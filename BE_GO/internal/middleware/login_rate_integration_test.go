//go:build ratelimitintegration

package middleware

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
	"github.com/redis/go-redis/v9"
)

func TestLoginRatePortableHTTP(t *testing.T) {
	var fixture struct{ Address, Password string }
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_RATE_ADMISSION_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || os.Getenv("ASSOZETA_CONTRACT_PYTHON") == "" {
		t.Fatal("run Django login-rate adapter")
	}
	client := redis.NewClient(&redis.Options{Addr: fixture.Address, Password: fixture.Password, DB: 1, MaxRetries: -1, ContextTimeoutEnabled: true, PoolSize: 4})
	defer client.Close()
	cache, err := repository.NewDjangoLoginRateCache(client)
	if err != nil {
		t.Fatal(err)
	}
	gate := service.NewLoginRate(cache, func() time.Time { return time.Unix(1800000000, 0) })
	e := echo.New()
	terminal := func(c echo.Context) error {
		raw, err := io.ReadAll(c.Request().Body)
		if err != nil {
			return err
		}
		sum := sha256.Sum256(raw)
		return c.JSON(200, map[string]string{"sha256": hex.EncodeToString(sum[:])})
	}
	e.Any("/oauth2/login", terminal, LoginRateLimit(true, gate))
	e.Any("/__fixtures/login-rate/disabled", terminal, LoginRateLimit(false, gate))
	e.POST("/__fixtures/login-rate/reset", func(c echo.Context) error {
		if err := client.FlushDB(c.Request().Context()).Err(); err != nil {
			return err
		}
		return c.JSON(200, map[string]bool{"reset": true})
	})
	server := httptest.NewServer(e)
	defer server.Close()
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Getenv("ASSOZETA_CONTRACT_PYTHON"), "-m", "unittest", "TESTS.contracts.test_login_rate", "-v")
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "TESTS_BASE_URL="+server.URL)
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("portable rate admission failed: %v\n%s", err, output)
	}
	t.Logf("%s", output)
}
