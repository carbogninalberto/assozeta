package handler

import (
	"context"
	"io"
	"net"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// closedSessions is the session resolver of a request without a session.
type closedSessions struct{}

func (closedSessions) Resolve(context.Context, string) (domain.SessionIdentity, error) {
	return domain.SessionIdentity{Empty: true}, nil
}

// A reply that closes the connection is answered with no bytes at all, as
// uvicorn does when it refuses a header value. The response-writer wrappers the
// document routes install (DjangoCORS, the security headers and
// DjangoSessionAudit's sessionHeaderWriter) must keep net/http's hijack
// reachable.
func TestDocumentRenderClosedConnectionWritesNoResponse(t *testing.T) {
	server := echo.New()
	server.HideBanner, server.HidePort = true, true
	server.Pre(middleware.DjangoCORS(false, nil))
	server.Use(middleware.DjangoSecurityHeaders(), middleware.DjangoSessionAudit(closedSessions{}))
	renders := &DocumentRenderHandler{}
	server.GET("/document/subscription/:uid", func(c echo.Context) error {
		c.Response().Header().Set("Allow", "POST, GET, OPTIONS")
		return renders.respond(c, &service.DocumentRenderReply{Status: 200, CloseConnection: true})
	})
	live := httptest.NewServer(server)
	defer live.Close()

	connection, err := net.Dial("tcp", live.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	if _, err = io.WriteString(connection, "GET /document/subscription/a1000000-0000-4000-8000-000000000001 HTTP/1.1\r\n"+
		"Host: fixture\r\n\r\n"); err != nil {
		t.Fatal(err)
	}
	if err = connection.SetReadDeadline(time.Now().Add(10 * time.Second)); err != nil {
		t.Fatal(err)
	}
	received, err := io.ReadAll(connection)
	if err != nil {
		t.Fatalf("reading the closed connection: %v", err)
	}
	if len(received) != 0 {
		t.Fatalf("the client received %q, want a closed connection with no response", received)
	}
}
