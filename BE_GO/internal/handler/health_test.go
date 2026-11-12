package handler

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
	"net/http/httptest"
	"testing"
	"time"
)

type probe struct{ err error }

func (p probe) Check(context.Context) error { return p.err }
func TestHealthAndReadinessWireContracts(t *testing.T) {
	for _, healthy := range []bool{true, false} {
		db := probe{}
		if !healthy {
			db.err = errors.New("secret")
		}
		h := NewHealthHandler(service.NewHealthService(db, probe{}, time.Second), "v1.0.17")
		for _, path := range []string{"/healthz", "/readyz"} {
			e := echo.New()
			e.Any("/healthz", h.Health)
			e.Any("/readyz", h.Readiness)
			rec := httptest.NewRecorder()
			e.ServeHTTP(rec, httptest.NewRequest("GET", path, nil))
			status := 200
			if path == "/readyz" && !healthy {
				status = 503
			}
			if rec.Code != status {
				t.Fatalf("status=%d", rec.Code)
			}
			var body map[string]any
			if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if path == "/healthz" {
				if len(body) != 2 || body["status"] != "ok" || body["version"] != "v1.0.17" {
					t.Fatalf("health=%v", body)
				}
			} else {
				want := "ready"
				if !healthy {
					want = "unavailable"
				}
				checks := body["checks"].(map[string]any)
				if len(body) != 2 || body["status"] != want || checks["database"] != healthy || checks["redis"] != true {
					t.Fatalf("ready=%v", body)
				}
			}
			for _, method := range []string{"POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"} {
				rec := httptest.NewRecorder()
				e.ServeHTTP(rec, httptest.NewRequest(method, path, nil))
				if rec.Code != 405 || rec.Header().Get("Allow") != "GET" {
					t.Fatalf("%s %s=%d, Allow=%s", method, path, rec.Code, rec.Header().Get("Allow"))
				}
			}
		}
	}
}
