package handler

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/wsproto"
)

// WebSocketHandler serves Django's Channels WebSocket routes (core.asgi):
// JWTAuthMiddleware, then URLRouter with ws/updates/, ws/health/ and
// ws/notifications/. ws/agent/ (the AI agent) is not served here: like any
// unrouted path it gets the router's exception (HTTP 500 before accept).
type WebSocketHandler struct {
	identities    *service.WebSocketIdentities
	notifications *service.NotificationManager
	hub           *service.RealtimeHub
	version       string
}

func NewWebSocketHandler(identities *service.WebSocketIdentities, notifications *service.NotificationManager, hub *service.RealtimeHub, version string) *WebSocketHandler {
	return &WebSocketHandler{identities: identities, notifications: notifications, hub: hub, version: version}
}

// Routes hands WebSocket upgrade requests to the WebSocket protocol before any
// HTTP middleware runs, as uvicorn does; every other request goes to next.
func (h *WebSocketHandler) Routes(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if wsproto.IsUpgrade(r) {
			h.serve(w, r)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// errCrash marks the ASGI application raising: HTTP 500 before accept, a TCP
// close without a close frame after.
var errCrash = errors.New("websocket application raised")

func writeHandshakeError(w http.ResponseWriter, failure *wsproto.HandshakeError) {
	for _, header := range failure.Headers {
		w.Header().Set(header[0], header[1])
	}
	w.Header().Set("Content-Length", strconv.Itoa(len(failure.Body)))
	w.Header().Set("Content-Type", "text/plain")
	w.Header().Set("Connection", "close")
	w.WriteHeader(failure.Status)
	_, _ = w.Write([]byte(failure.Body))
}

// rejectBeforeAccept is a websocket.close before accept: uvicorn answers 403.
func rejectBeforeAccept(w http.ResponseWriter) {
	writeHandshakeError(w, &wsproto.HandshakeError{Status: http.StatusForbidden})
}

// crashBeforeAccept is uvicorn's send_500_response.
func crashBeforeAccept(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Content-Length", "21")
	w.Header().Set("Connection", "close")
	w.WriteHeader(http.StatusInternalServerError)
	_, _ = w.Write([]byte("Internal Server Error"))
}

// parseQS is urllib.parse.parse_qs(qs) with its defaults: blank values are
// dropped, '+' is a space and percent escapes decode as UTF-8 with replacement.
func parseQS(query string) map[string][]string {
	result := map[string][]string{}
	for _, pair := range strings.Split(query, "&") {
		if pair == "" {
			continue
		}
		name, value, found := strings.Cut(pair, "=")
		if !found || value == "" {
			continue
		}
		key := pythonUnquote(strings.ReplaceAll(name, "+", " "))
		result[key] = append(result[key], pythonUnquote(strings.ReplaceAll(value, "+", " ")))
	}
	return result
}

func hexValue(ch byte) (byte, bool) {
	switch {
	case ch >= '0' && ch <= '9':
		return ch - '0', true
	case ch >= 'a' && ch <= 'f':
		return ch - 'a' + 10, true
	case ch >= 'A' && ch <= 'F':
		return ch - 'A' + 10, true
	}
	return 0, false
}

func pythonUnquote(text string) string {
	var raw []byte
	for i := 0; i < len(text); i++ {
		if text[i] == '%' && i+2 < len(text) {
			high, okHigh := hexValue(text[i+1])
			low, okLow := hexValue(text[i+2])
			if okHigh && okLow {
				raw = append(raw, high<<4|low)
				i += 2
				continue
			}
		}
		raw = append(raw, text[i])
	}
	var out strings.Builder
	for len(raw) > 0 {
		r, size := utf8.DecodeRune(raw)
		if r == utf8.RuneError && size == 1 {
			out.WriteRune(utf8.RuneError)
		} else {
			out.Write(raw[:size])
		}
		raw = raw[size:]
	}
	return out.String()
}

func first(values []string) string {
	if len(values) == 0 {
		return ""
	}
	return values[0]
}

// cookieToken is _get_token_from_cookie on the last Cookie header.
func cookieToken(r *http.Request) (string, error) {
	values := r.Header.Values("Cookie")
	if len(values) == 0 {
		return "", nil
	}
	header := values[len(values)-1]
	if !utf8.ValidString(header) {
		// bytes.decode('utf-8') raises inside the middleware.
		return "", errCrash
	}
	for _, cookie := range strings.Split(header, ";") {
		cookie = dto.PythonStrip(cookie)
		if strings.HasPrefix(cookie, "BKN_AUTH=") {
			return cookie[len("BKN_AUTH="):], nil
		}
	}
	return "", nil
}

// session is one connection's ASGI scope and consumer state.
type session struct {
	h             *WebSocketHandler
	ctx           context.Context
	path          string
	actor         *domain.Account // the authenticated user (nil: anonymous)
	user          *domain.Account // scope['user']: the impersonated target when impersonating
	identifier    string          // impersonation session
	impersonating bool
	association   string // scope['impersonation_association'] ("" for none)
	conn          *wsproto.Conn
	member        *service.RealtimeMember
	closed        bool // the middleware's `closed` flag
	consumer      consumer
}

type consumer interface {
	// connect returns accepted=false for a close before accept.
	connect(s *session) (accepted bool, err error)
	afterAccept(s *session) error
	receive(s *session, message *wsproto.Message) error
	event(s *session, message domain.RequestObject) error
	disconnect(s *session)
}

func (h *WebSocketHandler) serve(w http.ResponseWriter, r *http.Request) {
	key, failure := wsproto.CheckRequest(r)
	if failure != nil {
		writeHandshakeError(w, failure)
		return
	}
	ctx, cancel := context.WithCancel(context.WithoutCancel(r.Context()))
	defer cancel()
	s := &session{h: h, ctx: ctx, path: r.URL.Path}
	// JWTAuthMiddleware: the query token first, then the BKN_AUTH cookie.
	query := parseQS(r.URL.RawQuery)
	token := first(query["token"])
	if token == "" {
		var err error
		if token, err = cookieToken(r); err != nil {
			crashBeforeAccept(w)
			return
		}
	}
	if token != "" {
		s.actor = h.identities.Authenticate(ctx, token)
	}
	s.user = s.actor
	if identifier := first(query["impersonation"]); identifier != "" {
		target, ok, err := h.identities.Impersonate(ctx, s.actor, identifier, s.path)
		if err != nil {
			crashBeforeAccept(w)
			return
		}
		if !ok {
			rejectBeforeAccept(w)
			return
		}
		s.user, s.association, s.identifier, s.impersonating = target.User, target.Association, identifier, true
	}
	switch s.path {
	case "/ws/updates/":
		s.consumer = updateConsumer{}
	case "/ws/health/":
		s.consumer = healthConsumer{}
	case "/ws/notifications/":
		s.consumer = &notificationConsumer{}
	default:
		// URLRouter: "No route found for path".
		crashBeforeAccept(w)
		return
	}
	s.member = service.NewRealtimeMember()
	defer h.hub.LeaveAll(s.member)
	accepted, err := s.consumer.connect(s)
	if err == nil && accepted {
		accepted, err = s.checked()
	}
	if err != nil {
		crashBeforeAccept(w)
		return
	}
	if !accepted {
		rejectBeforeAccept(w)
		return
	}
	extensions, deflate, failure := wsproto.NegotiateExtensions(r)
	if failure != nil {
		s.consumer.disconnect(s)
		writeHandshakeError(w, failure)
		return
	}
	conn, err := wsproto.Accept(w, key, extensions, deflate)
	if err != nil {
		s.consumer.disconnect(s)
		return
	}
	s.conn = conn
	s.run()
}

// checked is selected_user() for an impersonating connection: false when the
// session no longer resolves.
func (s *session) checked() (bool, error) {
	if !s.impersonating {
		return true, nil
	}
	_, ok, err := s.h.identities.Impersonate(s.ctx, s.actor, s.identifier, s.path)
	return ok, err
}

// send is consumer.send through the middleware's checked_send.
func (s *session) send(value domain.RequestObject) error {
	return s.sendText(dto.PythonJSONDumps(value))
}

func (s *session) sendText(data []byte) error {
	if s.closed {
		return nil
	}
	ok, err := s.checked()
	if err != nil {
		return err
	}
	if !ok {
		s.closed = true
		s.conn.Close(4003, "")
		return nil
	}
	_ = s.conn.WriteText(data)
	return nil
}

func (s *session) run() {
	if err := s.consumer.afterAccept(s); err != nil {
		s.crash(err)
		return
	}
	for {
		select {
		case item := <-s.conn.Incoming():
			switch value := item.(type) {
			case *wsproto.Message:
				if s.impersonating {
					// checked_receive: a revoked session closes with 4003 and the
					// consumer receives a disconnect instead of the message.
					ok, err := false, error(nil)
					if !s.closed {
						ok, err = s.checked()
					}
					if err != nil {
						s.crash(err)
						return
					}
					if !ok {
						if !s.closed {
							s.conn.Close(4003, "")
						}
						s.closed = true
						s.consumer.disconnect(s)
						return
					}
				}
				if err := s.consumer.receive(s, value); err != nil {
					s.crash(err)
					return
				}
			case wsproto.Disconnect:
				s.consumer.disconnect(s)
				return
			}
		case <-s.conn.Done():
			// The connection was terminated (keepalive timeout, close timeout).
			s.consumer.disconnect(s)
			return
		case message := <-s.member.Messages():
			if err := s.consumer.event(s, message); err != nil {
				s.crash(err)
				return
			}
		}
	}
}

func (s *session) crash(err error) {
	if !errors.Is(err, errCrash) {
		slog.Warn("websocket consumer failed", "path", s.path, "error", err)
	}
	s.conn.Abort()
}
