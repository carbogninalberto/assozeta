package middleware

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type LoginRateAdmission interface {
	Check(context.Context, string, string) error
	CheckAccount(context.Context, any) error
}

// LoginRateLimit wraps login before content negotiation, authentication and DRF
// body interpretation. The enabled flag is supplied by explicit configuration.
func LoginRateLimit(enabled bool, gate LoginRateAdmission) echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			r := c.Request()
			if !enabled || r.Method != http.MethodPost {
				return next(c)
			}
			ip, _, _ := strings.Cut(r.Header.Get("X-Forwarded-For"), ",")
			ip = strings.TrimFunc(ip, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
			if ip == "" {
				ip = r.RemoteAddr
				if host, _, err := net.SplitHostPort(ip); err == nil {
					ip = host
				}
				if ip == "" {
					ip = "unknown"
				}
			}
			if err := gate.Check(r.Context(), "login-ip", ip); err != nil {
				return loginRateError(c, err)
			}
			const maximum = 262144000 // existing DATA_UPLOAD_MAX_MEMORY_SIZE
			if r.ContentLength > maximum {
				return c.HTML(400, "<h1>Bad Request (400)</h1>")
			}
			var raw []byte
			if r.Body != nil {
				original := r.Body
				defer original.Close()
				var err error
				raw, err = io.ReadAll(io.LimitReader(original, maximum+1))
				if err != nil {
					return loginRateError(c, err)
				}
				if len(raw) > maximum {
					return c.HTML(400, "<h1>Bad Request (400)</h1>")
				}
			}
			r.Body = io.NopCloser(bytes.NewReader(raw))
			var body any
			var parseErr error
			if utf8.Valid(raw) {
				body, parseErr = dto.ParseLoginRateJSON(raw)
			} else {
				parseErr = errors.New("invalid UTF-8")
			}
			var err error
			switch {
			case errors.Is(parseErr, dto.ErrReferenceNesting), errors.Is(parseErr, dto.ErrLoginRateJSONValue):
				return loginRateError(c, parseErr)
			case parseErr != nil:
				err = gate.Check(r.Context(), "login-account", "invalid-request")
			default:
				err = gate.CheckAccount(r.Context(), body)
			}
			if err != nil {
				return loginRateError(c, err)
			}
			return next(c)
		}
	}
}

func loginRateError(c echo.Context, err error) error {
	if errors.Is(err, service.ErrLoginRateLimited) {
		return c.Blob(429, "text/html; charset=utf-8", []byte("Too many requests, blocked."))
	}
	return c.HTML(500, "\n<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>Server Error (500)</title>\n</head>\n<body>\n  <h1>Server Error (500)</h1><p></p>\n</body>\n</html>\n")
}
