package instanceprobe

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/url"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// A body shorter than its Content-Length raises http.client.IncompleteRead in
// read_json, which release_catalog.py:45 does not catch (it is an
// HTTPException, not an OSError or ValueError): Django answers its 500 page,
// so Go answers the same application-state page and never hands the request
// off.
func TestReadJSONShortBodyIsAppState(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	done := make(chan struct{})
	go func() {
		defer close(done)
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		buffer := make([]byte, 4096)
		if _, err = conn.Read(buffer); err != nil { // the request line and headers
			return
		}
		_, _ = conn.Write([]byte("HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc"))
	}()

	client := &Client{Proxy: func(*http.Request) (*url.URL, error) { return nil, nil }}
	_, err = client.ReadJSON(context.Background(), "http://"+listener.Addr().String()+"/x", nil)
	<-done
	if err == nil {
		t.Fatal("short body: no error")
	}
	if !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("short body: error %v, want refusal.ErrAppState", err)
	}
	if errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("short body: error %v must not be a refusal", err)
	}
	if reason, ok := refusal.AppStateReason(err); !ok || reason != "instanceprobe: response shorter than its Content-Length" {
		t.Fatalf("short body: reason %q, ok %v", reason, ok)
	}
}
