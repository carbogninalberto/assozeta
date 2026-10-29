package middleware

import (
	"crypto/subtle"
	"net/http"
	"net/url"
	"strings"

	"github.com/labstack/echo/v4"
)

const csrfCharacters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

// DjangoCSRF implements the cookie/header admission used by non-exempt Django
// views. Apply explicitly to those routes; DRF's exempt views have their own
// authentication contract. It does not issue session or CSRF cookies.
func DjangoCSRF(trustedOrigins []string) echo.MiddlewareFunc {
	trusted := append([]string(nil), trustedOrigins...)
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			request := c.Request()
			switch request.Method {
			case "GET", "HEAD", "OPTIONS", "TRACE":
				return next(c)
			}
			reject := func() error {
				return c.HTML(http.StatusForbidden, "<!DOCTYPE html><html><body><h1>Forbidden (403)</h1><p>CSRF verification failed. Request aborted.</p></body></html>")
			}
			scheme := "http"
			if request.TLS != nil || strings.TrimSpace(strings.Split(request.Header.Get("X-Forwarded-Proto"), ",")[0]) == "https" {
				scheme = "https"
			}
			host := request.Host
			if forwarded := request.Header.Get("X-Forwarded-Host"); forwarded != "" {
				host = forwarded
			}
			if origin, hasOrigin := request.Header["Origin"]; hasOrigin {
				if len(origin) != 1 || !trustedOrigin(origin[0], scheme+"://"+host, trusted) {
					return reject()
				}
			} else if scheme == "https" {
				referer, err := url.Parse(request.Referer())
				if err != nil || referer.Scheme != "https" || referer.Host == "" || !trustedReferer(referer.Host, host, trusted) {
					return reject()
				}
			}
			cookie, err := request.Cookie("csrftoken")
			if err != nil {
				return reject()
			}
			secret, ok := csrfSecret(cookie.Value)
			if !ok {
				return reject()
			}
			token := ""
			if request.Method == "POST" {
				token = request.PostFormValue("csrfmiddlewaretoken")
			}
			if token == "" {
				token = request.Header.Get("X-CSRFToken")
			}
			supplied, ok := csrfSecret(token)
			if !ok || subtle.ConstantTimeCompare([]byte(secret), []byte(supplied)) != 1 {
				return reject()
			}
			return next(c)
		}
	}
}

func csrfSecret(token string) (string, bool) {
	if len(token) != 32 && len(token) != 64 {
		return "", false
	}
	for _, character := range token {
		if !strings.ContainsRune(csrfCharacters, character) {
			return "", false
		}
	}
	if len(token) == 32 {
		return token, true
	}
	secret := make([]byte, 32)
	for index := range secret {
		mask := strings.IndexByte(csrfCharacters, token[index])
		cipher := strings.IndexByte(csrfCharacters, token[index+32])
		secret[index] = csrfCharacters[(cipher-mask+len(csrfCharacters))%len(csrfCharacters)]
	}
	return string(secret), true
}
func trustedOrigin(origin, sameOrigin string, trusted []string) bool {
	if origin == sameOrigin {
		return true
	}
	parsed, err := url.Parse(origin)
	if err != nil || parsed.Host == "" || parsed.User != nil || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return false
	}
	for _, allowed := range trusted {
		if origin == allowed {
			return true
		}
		value, err := url.Parse(allowed)
		if err != nil {
			continue
		}
		if value.Scheme == parsed.Scheme && strings.HasPrefix(value.Host, "*.") && sameDomain(parsed.Host, strings.TrimPrefix(value.Host, "*")) {
			return true
		}
	}
	return false
}
func trustedReferer(host, sameHost string, trusted []string) bool {
	if host == sameHost {
		return true
	}
	for _, origin := range trusted {
		parsed, err := url.Parse(origin)
		if err == nil && sameDomain(host, strings.TrimPrefix(parsed.Host, "*")) {
			return true
		}
	}
	return false
}
func sameDomain(host, pattern string) bool {
	if strings.HasPrefix(pattern, ".") {
		return host == pattern[1:] || strings.HasSuffix(host, pattern)
	}
	return host == pattern
}
