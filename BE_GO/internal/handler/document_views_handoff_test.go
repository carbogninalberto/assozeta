package handler

import (
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jackc/pgx/v5"
	"github.com/labstack/echo/v4"
)

// A view that saved get_main_tutor's promotion and is then refused must not be
// replayed to Django: the relation and its audit entry are already committed.
// The promotion runs on the admitted context, which keeps the request's
// handoff state, so its statements count as effects of the request.
func TestRefusalAfterTutorPromotionIsNotHandedOff(t *testing.T) {
	var upstreamCalls atomic.Int32
	django := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		upstreamCalls.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer django.Close()
	h, err := handoff.New(django.URL, 0, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	audit := func(*http.Request) (domain.LoginAuditContext, error) { return domain.LoginAuditContext{}, nil }
	refused := fmt.Errorf("%w: template input", service.ErrDocumentViewNotPorted)
	for name, test := range map[string]struct {
		promote bool
		status  int
		calls   int32
	}{
		"refusal after the promotion":   {promote: true, status: 500, calls: 0},
		"refusal without the promotion": {promote: false, status: 200, calls: 1},
	} {
		upstreamCalls.Store(0)
		e := echo.New()
		e.Pre(h.Middleware())
		e.GET("/document/subscription/:uid/view/", func(c echo.Context) error {
			promotion, release := documentViewPromotion(c, audit, paymentcategorylifecycle.New())
			defer release()
			if test.promote {
				admitted, _, err := promotion.Admit()
				if err != nil {
					return err
				}
				// The statements of DocumentViews.PromoteMainTutor, as the pool's tracer sees them.
				for _, statement := range []string{
					"begin",
					"SELECT r.id FROM application_associatetutorrelation r WHERE r.associate_id=$1::uuid FOR UPDATE OF r",
					"UPDATE application_associatetutorrelation SET is_primary=true WHERE id=$1",
					"commit",
				} {
					handoff.QueryTracer{}.TraceQueryStart(admitted, nil, pgx.TraceQueryStartData{SQL: statement})
				}
			}
			return messageReadsError(c, refused)
		})
		server := httptest.NewServer(e)
		response, err := http.Get(server.URL + "/document/subscription/x/view/")
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		server.Close()
		if response.StatusCode != test.status || upstreamCalls.Load() != test.calls {
			t.Errorf("%s: status %d with %d upstream calls, want %d with %d", name, response.StatusCode, upstreamCalls.Load(), test.status, test.calls)
		}
	}
}
