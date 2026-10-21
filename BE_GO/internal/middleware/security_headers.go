package middleware

import "github.com/labstack/echo/v4"

// DjangoSecurityHeaders preserves the SecurityMiddleware defaults selected by
// core.settings. Register after CORS so preflight short-circuits this middleware.
// Existing explicit response values, including empty ones, take precedence.
func DjangoSecurityHeaders() echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			c.Response().Writer = &sessionHeaderWriter{ResponseWriter: c.Response().Writer, before: func() {
				header := c.Response().Header()
				if _, present := header["X-Content-Type-Options"]; !present {
					header.Set("X-Content-Type-Options", "nosniff")
				}
				if _, present := header["Referrer-Policy"]; !present {
					header.Set("Referrer-Policy", "same-origin")
				}
				if _, present := header["Cross-Origin-Opener-Policy"]; !present {
					header.Set("Cross-Origin-Opener-Policy", "same-origin")
				}
			}}
			return next(c)
		}
	}
}
