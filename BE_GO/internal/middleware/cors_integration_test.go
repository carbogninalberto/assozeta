//go:build corsintegration

package middleware

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/labstack/echo/v4"
)

func TestCORSPortableHTTP(t *testing.T) {
	var fixture struct {
		Policies []struct {
			Name    string
			All     bool
			Origins []string
		}
		URLs []struct {
			Raw, Scheme, Netloc string
			Valid               bool
		}
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_CORS_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.Policies) != 5 || len(fixture.URLs) != 40 || os.Getenv("ASSOZETA_CONTRACT_PYTHON") == "" {
		t.Fatal("run TESTS/adapters/django/cors.py")
	}
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	for i, test := range fixture.URLs {
		actual, valid := splitCORSOrigin(test.Raw)
		if valid != test.Valid || valid && (actual.scheme != test.Scheme || actual.netloc != test.Netloc) {
			t.Fatalf("URL comparison case %d differs", i)
		}
	}
	for _, policy := range fixture.Policies {
		t.Run(policy.Name, func(t *testing.T) {
			e := echo.New()
			e.Pre(DjangoCORS(policy.All, policy.Origins))
			var calls atomic.Int64
			e.GET("/count", func(c echo.Context) error { return c.JSON(200, map[string]int64{"calls": calls.Load()}) })
			e.Any("/terminal/:code", func(c echo.Context) error {
				calls.Add(1)
				code, err := strconv.Atoi(c.Param("code"))
				if err != nil {
					return err
				}
				raw, err := io.ReadAll(c.Request().Body)
				if err != nil {
					return err
				}
				sum := sha256.Sum256(raw)
				if values, ok := c.Request().Header["X-Fixture-Vary"]; ok {
					c.Response().Header().Set("Vary", values[0])
				}
				return c.JSON(code, map[string]string{"method": c.Request().Method, "sha256": hex.EncodeToString(sum[:])})
			})
			e.GET("/stream", func(c echo.Context) error {
				c.Response().Header().Set("Vary", "Cookie")
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
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			testClass := "CORSContracts"
			if policy.Name == "invalid" {
				testClass = "CORSInvalidConfigurationContracts"
			}
			cmd := exec.CommandContext(ctx, os.Getenv("ASSOZETA_CONTRACT_PYTHON"), "-m", "unittest", "TESTS.contracts.test_cors."+testClass, "-v")
			cmd.Dir = root
			cmd.Env = append(os.Environ(), "TESTS_BASE_URL="+server.URL, "TESTS_CORS_POLICY="+policy.Name)
			output, err := cmd.CombinedOutput()
			if err != nil {
				t.Fatalf("portable CORS contracts failed: %v\n%s", err, output)
			}
			t.Logf("%s", output)
		})
	}
}
