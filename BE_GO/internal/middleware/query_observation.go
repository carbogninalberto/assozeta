package middleware

import (
	"github.com/carbogninalberto/assozeta/BE_GO/internal/queryobservation"
	"github.com/labstack/echo/v4"
)

// QueryObservation wraps the diagnostic request before authentication, session,
// and audit middleware. A nil observer is an identity middleware. The response
// writer and Echo error handling are untouched; only the derived request context
// and generated response header are installed. Client headers never select IDs.
// Finish runs as this chain unwinds, including on panic. Database work in a custom
// outer error handler must remain inside the observation lifetime as well.
func QueryObservation(observer *queryobservation.Observer) echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		if observer == nil {
			return next
		}
		return func(c echo.Context) error {
			r := c.Request()
			ctx, id, finish := observer.Begin(r.Context())
			defer finish()
			c.SetRequest(r.WithContext(ctx))
			c.Response().Header().Set("X-Query-Observation-ID", id)
			return next(c)
		}
	}
}
