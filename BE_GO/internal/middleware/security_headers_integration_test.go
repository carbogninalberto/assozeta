//go:build securityheadersintegration

package middleware

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/labstack/echo/v4"
)

func TestSecurityHeadersPortableHTTP(t *testing.T) {
	if os.Getenv("ASSOZETA_CONTRACT_PYTHON") == "" {
		t.Fatal("run TESTS/adapters/django/security_headers.py")
	}
	e := echo.New()
	e.Pre(DjangoCORS(true, nil), DjangoSecurityHeaders())
	e.Any("/terminal/:code", func(c echo.Context) error {
		code, err := strconv.Atoi(c.Param("code"))
		if err != nil {
			return err
		}
		if value, ok := c.Request().Header["X-Fixture-Security"]; ok {
			for _, name := range []string{"X-Content-Type-Options", "Referrer-Policy", "Cross-Origin-Opener-Policy"} {
				c.Response().Header().Set(name, value[0])
			}
		}
		return c.JSON(code, map[string]string{"method": c.Request().Method})
	})
	e.GET("/stream", func(c echo.Context) error {
		c.Response().Header().Set("Content-Type", "text/plain")
		if _, err := c.Response().Write([]byte("A")); err != nil {
			return err
		}
		if err := http.NewResponseController(c.Response().Writer).Flush(); err != nil {
			return err
		}
		_, err := c.Response().Write([]byte("B"))
		return err
	})
	server := httptest.NewServer(e)
	defer server.Close()
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Getenv("ASSOZETA_CONTRACT_PYTHON"), "-m", "unittest", "TESTS.contracts.test_security_headers", "-v")
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "TESTS_BASE_URL="+server.URL)
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("portable security contracts failed: %v\n%s", err, output)
	}
	t.Logf("%s", output)
}
