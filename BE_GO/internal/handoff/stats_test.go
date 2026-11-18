package handoff

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/labstack/echo/v4"
)

// eventually polls Stats: Serve records after the replayed response is flushed,
// so the client can see the response a moment before the count.
func eventually(t *testing.T, ok func(StatsSnapshot) bool) StatsSnapshot {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for {
		snapshot := Stats()
		if ok(snapshot) || time.Now().After(deadline) {
			return snapshot
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// statsRow finds the row for (route, outcome); ok is false when it is absent.
func statsRow(snapshot StatsSnapshot, route string, outcome Outcome) (StatsRow, bool) {
	for _, row := range snapshot.Rows {
		if row.Route == route && row.Outcome == outcome {
			return row, true
		}
	}
	return StatsRow{}, false
}

func TestStatsCountHandedOffRefusals(t *testing.T) {
	resetStats()
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 0)
	server := app(t, h, func(c echo.Context) error { return refuse(c, errRefused) })
	before := time.Now().UTC()
	for range 3 {
		if response, _ := send(t, http.MethodGet, server.URL+"/x?secret=1", nil, nil); response.StatusCode != http.StatusCreated {
			t.Fatalf("status %d", response.StatusCode)
		}
	}
	snapshot := eventually(t, func(s StatsSnapshot) bool { return s.Totals[OutcomeHandedOff] >= 3 })
	row, ok := statsRow(snapshot, "/*", OutcomeHandedOff)
	if !ok || row.Count != 3 || row.Method != http.MethodGet || row.Reason != "test input not ported" || row.Cause != "" {
		t.Fatalf("handed off row %+v (found %v) in %+v", row, ok, snapshot.Rows)
	}
	if row.Last.Before(before) || row.Last.Location() != time.UTC {
		t.Fatalf("last %v, want UTC at or after %v", row.Last, before)
	}
	if snapshot.Totals[OutcomeHandedOff] != 3 {
		t.Fatalf("totals %+v", snapshot.Totals)
	}
	if snapshot.Since.IsZero() || snapshot.Since.After(before) {
		t.Fatalf("since %v must be the process start, before %v", snapshot.Since, before)
	}
	// No request data: neither the query nor the raw path reach the counters.
	for _, row := range snapshot.Rows {
		if strings.Contains(row.Route, "secret") || strings.Contains(row.Route, "/x") {
			t.Fatalf("request data in row %+v", row)
		}
	}
}

func TestStatsCountSkipsWithAStableCause(t *testing.T) {
	resetStats()
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 0)
	server := app(t, h, func(c echo.Context) error {
		MarkEffect(c.Request().Context(), "object storage write")
		return refuse(c, errRefused)
	})
	for range 2 {
		if response, _ := send(t, http.MethodPost, server.URL+"/x", strings.NewReader("{}"), nil); response.StatusCode != 500 {
			t.Fatalf("status %d", response.StatusCode)
		}
	}
	row, ok := statsRow(Stats(), "/*", OutcomeSkipped)
	// The cause is a fixed class, without counts or effect names, so rows stay bounded.
	if !ok || row.Count != 2 || row.Cause != "effect" || row.Method != http.MethodPost {
		t.Fatalf("skipped row %+v (found %v)", row, ok)
	}
}

func TestStatsCountUpstreamErrorsWithoutTheirText(t *testing.T) {
	resetStats()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	listener.Close() // nothing listens: the replay fails to connect
	h := newHandoff(t, "http://"+address, 0)
	server := app(t, h, func(c echo.Context) error { return refuse(c, errRefused) })
	if response, _ := send(t, http.MethodGet, server.URL+"/x", nil, nil); response.StatusCode != 500 {
		t.Fatalf("status %d", response.StatusCode)
	}
	row, ok := statsRow(Stats(), "/*", OutcomeSkipped)
	if !ok || row.Count != 1 || row.Cause != "upstream error" {
		t.Fatalf("skipped row %+v (found %v)", row, ok)
	}
}

func TestStatsCountRefusalsWhenTheHandoffIsDisabled(t *testing.T) {
	resetStats()
	server := app(t, newHandoff(t, "", 0), func(c echo.Context) error { return refuse(c, errRefused) })
	if response, raw := send(t, http.MethodGet, server.URL+"/x", nil, nil); response.StatusCode != 500 || string(raw) != serverError {
		t.Fatalf("status %d body %q", response.StatusCode, raw)
	}
	snapshot := Stats()
	row, ok := statsRow(snapshot, "/*", OutcomeDisabled)
	if !ok || row.Count != 1 || row.Reason != "test input not ported" {
		t.Fatalf("disabled row %+v (found %v) in %+v", row, ok, snapshot.Rows)
	}
	if snapshot.Totals[OutcomeDisabled] != 1 || snapshot.Totals[OutcomeHandedOff] != 0 {
		t.Fatalf("totals %+v", snapshot.Totals)
	}
}

func TestStatsIgnoreErrorsThatAreNotRefusals(t *testing.T) {
	resetStats()
	server := app(t, newHandoff(t, "", 0), func(c echo.Context) error {
		return refuse(c, context.DeadlineExceeded)
	})
	send(t, http.MethodGet, server.URL+"/x", nil, nil)
	if snapshot := Stats(); len(snapshot.Rows) != 0 {
		t.Fatalf("rows %+v", snapshot.Rows)
	}
}

func TestStatsRowsAreSortedAndBounded(t *testing.T) {
	resetStats()
	e := echo.New()
	for i := range maxStatsRows + 50 {
		c := e.NewContext(httptest.NewRequest(http.MethodGet, "/", nil), httptest.NewRecorder())
		c.SetPath("/route/" + strings.Repeat("r", i%7) + string(rune('a'+i%26)) + "/" + time.Duration(i).String())
		Serve(c, errRefused)
	}
	c := e.NewContext(httptest.NewRequest(http.MethodGet, "/", nil), httptest.NewRecorder())
	c.SetPath("/route/hot")
	for range 5 {
		Serve(c, errRefused)
	}
	snapshot := Stats()
	if len(snapshot.Rows) > maxStatsRows {
		t.Fatalf("%d rows, cap %d", len(snapshot.Rows), maxStatsRows)
	}
	if snapshot.Dropped == 0 {
		t.Fatal("refusals past the row cap must be counted as dropped")
	}
	for i := 1; i < len(snapshot.Rows); i++ {
		if snapshot.Rows[i-1].Count < snapshot.Rows[i].Count {
			t.Fatalf("rows not sorted by count: %+v before %+v", snapshot.Rows[i-1], snapshot.Rows[i])
		}
	}
}
