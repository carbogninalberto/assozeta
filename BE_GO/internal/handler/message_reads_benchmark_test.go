package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// Deliberately retains no response body. The store also creates no row slice.
type messageListDiscardResponse struct {
	header http.Header
	status int
	bytes  int64
	first  byte
	tail   [2]byte
	sample func()
	writes int
}

func (w *messageListDiscardResponse) Header() http.Header    { return w.header }
func (w *messageListDiscardResponse) WriteHeader(status int) { w.status = status }
func (w *messageListDiscardResponse) Write(p []byte) (int, error) {
	if w.bytes == 0 && len(p) > 0 {
		w.first = p[0]
	}
	for _, v := range p {
		w.tail[0], w.tail[1] = w.tail[1], v
	}
	w.bytes += int64(len(p))
	w.writes++
	if w.sample != nil && w.writes%32 == 0 {
		w.sample()
	}
	return len(p), nil
}

// Each call allocates a new backing string. Equal contents must not alias one
// payload across the population: a slice of retained records must exceed budget.
func messageListLazyRecord() domain.MessageReadRecord {
	return domain.MessageReadRecord{Type: "EMAIL", Message: strings.Repeat("fixed-size <&> 雪 ", 16)}
}

func messageListLazyHandler(count int, sample func()) (*MessageReadsHandler, domain.MessageReadRecord) {
	return messageListHeapHandler(count, sample, false)
}

func messageListHeapHandler(count int, sample func(), bufferRows bool) (*MessageReadsHandler, domain.MessageReadRecord) {
	store := &messageReadsStoreHandlerTest{visit: func(ctx context.Context, consume func(domain.MessageReadRecord) error) error {
		// Negative control only: simulate a repository retaining all records before
		// consumption, without changing the production repository/service/handler.
		var retained []domain.MessageReadRecord
		for i := 0; i < count; i++ {
			if err := ctx.Err(); err != nil {
				return err
			}
			row := messageListLazyRecord()
			if bufferRows {
				retained = append(retained, row)
			} else if err := consume(row); err != nil {
				return err
			}
			if sample != nil && (i%512 == 0 || i == count-1) {
				sample()
			}
		}
		for _, row := range retained {
			if err := consume(row); err != nil {
				return err
			}
		}
		runtime.KeepAlive(retained)
		return nil
	}}
	accounts := messageReadsAccountsTest{"a1000000-0000-4000-8000-000000700001": {ID: "a1000000-0000-4000-8000-000000700001", Role: domain.RoleAssociation, Active: true}}
	h := NewMessageReadsHandler(service.NewMessageReads(store), service.NewActorAuthentication(accounts, &messageReadsTokensTest{sub: "a1000000-0000-4000-8000-000000700001"}), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil))
	return h, messageListLazyRecord()
}
func messageListDiscardRun(h *MessageReadsHandler, w *messageListDiscardResponse) error {
	r := httptest.NewRequest("GET", "/communications/messages/list", nil)
	r.Header.Set("Authorization", "Bearer valid")
	return h.List(echo.New().NewContext(r, w))
}
func messageListHeapMeasurements(t *testing.T, bufferRows bool) []uint64 {
	t.Helper()
	var peaks []uint64
	for _, count := range []int{512, 32768} {
		runtime.GC()
		var baseline runtime.MemStats
		runtime.ReadMemStats(&baseline)
		peak := baseline.HeapAlloc
		sample := func() {
			runtime.GC()
			var current runtime.MemStats
			runtime.ReadMemStats(&current)
			if current.HeapAlloc > peak {
				peak = current.HeapAlloc
			}
		}
		h, row := messageListHeapHandler(count, sample, bufferRows)
		w := &messageListDiscardResponse{header: make(http.Header), sample: sample}
		if err := messageListDiscardRun(h, w); err != nil {
			t.Fatal(err)
		}
		encoded, err := json.Marshal(dto.MessageReadFromRecord(row))
		if err != nil {
			t.Fatal(err)
		}
		expected := int64(count*(len(encoded)+1) + 2)
		if w.status != 200 || w.bytes != expected || w.first != '[' || w.tail != [2]byte{']', '\n'} {
			t.Fatalf("incomplete response: status=%d bytes=%d want=%d tail=%q", w.status, w.bytes, expected, w.tail)
		}
		delta := peak - baseline.HeapAlloc
		peaks = append(peaks, delta)
		t.Logf("buffering_control=%t rows=%d response_bytes=%d peak_live_heap_delta=%d (forced-GC samples; direct handler, no outer middleware)", bufferRows, count, w.bytes, delta)
	}
	return peaks
}

func messageListHeapWithinBound(peaks []uint64) bool {
	// Same 4 MiB guard for production and negative control, with 64x row growth.
	return peaks[1] <= peaks[0]+4<<20
}

func TestMessageReadsListLiveHeapBoundWithLazyInputAndDiscardOutput(t *testing.T) {
	peaks := messageListHeapMeasurements(t, false)
	if !messageListHeapWithinBound(peaks) {
		t.Fatalf("live heap grew with population: small=%d large=%d", peaks[0], peaks[1])
	}
}

func TestMessageReadsListLiveHeapGuardRejectsBufferingControl(t *testing.T) {
	peaks := messageListHeapMeasurements(t, true)
	if messageListHeapWithinBound(peaks) {
		t.Fatalf("same heap guard accepted O(N) retention: small=%d large=%d", peaks[0], peaks[1])
	}
	t.Logf("same 4 MiB guard rejected buffering: growth=%d bytes", peaks[1]-peaks[0])
}
func BenchmarkMessageReadsListSpool(b *testing.B) {
	for _, count := range []int{512, 32768} {
		b.Run(fmt.Sprintf("rows_%d", count), func(b *testing.B) {
			h, _ := messageListLazyHandler(count, nil)
			b.ReportAllocs()
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				w := &messageListDiscardResponse{header: make(http.Header)}
				if err := messageListDiscardRun(h, w); err != nil {
					b.Fatal(err)
				}
				if w.status != 200 {
					b.Fatal(w.status)
				}
			}
		})
	}
}
