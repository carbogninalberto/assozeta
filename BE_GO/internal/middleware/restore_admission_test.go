package middleware

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/labstack/echo/v4"
)

type restoreGateFunc func(context.Context, string, string) (io.Closer, error)

func (f restoreGateFunc) AcquireHTTP(ctx context.Context, method, path string) (io.Closer, error) {
	return f(ctx, method, path)
}

func TestRestoreErrorsPrecedeSecurityAndDoNotExposeWrappedDetails(t *testing.T) {
	for _, failure := range []error{domain.ErrRestoreBusy, domain.ErrRestoreVolumeUnavailable, domain.ErrRestorePending, errors.New("fixture-private database error")} {
		e := echo.New()
		called := false
		e.Pre(DjangoCORS(true, nil), DjangoRestoreAdmission(restoreGateFunc(func(context.Context, string, string) (io.Closer, error) {
			return nil, fmt.Errorf("fixture-private: %w", failure)
		})), DjangoSecurityHeaders())
		e.POST("/work", func(c echo.Context) error { called = true; return c.NoContent(204) })
		r := httptest.NewRequest("POST", "/work", nil)
		r.Header.Set("Origin", "https://allowed.example.test")
		w := httptest.NewRecorder()
		e.ServeHTTP(w, r)
		if called || strings.Contains(w.Body.String(), "fixture-private") || w.Header().Get("X-Content-Type-Options") != "" || w.Header().Get("Access-Control-Allow-Origin") != "*" {
			t.Fatal("restore failure crossed admission or exposed internal details")
		}
		if failure == domain.ErrRestoreBusy || failure == domain.ErrRestoreVolumeUnavailable || failure == domain.ErrRestorePending {
			var payload map[string]string
			if w.Code != 503 || json.Unmarshal(w.Body.Bytes(), &payload) != nil || payload["error"] != failure.Error() || w.Header().Get("Retry-After") != "10" {
				t.Fatal("restore denial changed wire contract")
			}
		} else if w.Code != 500 || !strings.Contains(w.Body.String(), "Server Error (500)") {
			t.Fatal("unexpected middleware error was not production HTML 500")
		}
	}
}

func TestRestoreLeaseCoversInflightHandlerAndReleasesAfterReturn(t *testing.T) {
	lock := repository.NewRestoreFileLock(filepath.Join(t.TempDir(), "lifecycle.lock"))
	e := echo.New()
	e.Pre(DjangoRestoreAdmission(restoreGateFunc(func(ctx context.Context, _, _ string) (io.Closer, error) { return lock.Acquire(ctx, false, true) })))
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	defer once.Do(func() { close(release) })
	e.POST("/work", func(c echo.Context) error { close(entered); <-release; return c.NoContent(204) })
	server := httptest.NewServer(e)
	defer func() { once.Do(func() { close(release) }); server.Close() }()
	done := make(chan error, 1)
	go func() {
		client := http.Client{Timeout: 5 * time.Second}
		response, err := client.Post(server.URL+"/work", "text/plain", nil)
		if err == nil {
			response.Body.Close()
			if response.StatusCode != 204 {
				err = errors.New("wrong terminal status")
			}
		}
		done <- err
	}()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("handler did not start")
	}
	lease, err := lock.Acquire(context.Background(), true, true)
	if lease != nil || !errors.Is(err, domain.ErrRestoreBusy) {
		t.Fatal("exclusive replacement entered while handler was running")
	}
	once.Do(func() { close(release) })
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	lease, err = lock.Acquire(context.Background(), true, true)
	if err != nil {
		t.Fatal("completed handler leaked lease")
	}
	_ = lease.Close()
}

func TestRestoreLeaseReleasesOnHandlerPanic(t *testing.T) {
	lock := repository.NewRestoreFileLock(filepath.Join(t.TempDir(), "lifecycle.lock"))
	e := echo.New()
	e.Pre(DjangoRestoreAdmission(restoreGateFunc(func(ctx context.Context, _, _ string) (io.Closer, error) { return lock.Acquire(ctx, false, true) })))
	e.GET("/work", func(echo.Context) error { panic("fixture handler failure") })
	func() {
		defer func() {
			if recover() == nil {
				t.Error("handler panic was lost")
			}
		}()
		e.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/work", nil))
	}()
	lease, err := lock.Acquire(context.Background(), true, true)
	if err != nil {
		t.Fatal("panicking handler leaked lease")
	}
	_ = lease.Close()
}
