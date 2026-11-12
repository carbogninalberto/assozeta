package middleware

import (
	"context"
	"errors"
	"io"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/labstack/echo/v4"
)

type RestoreAdmissionGate interface {
	AcquireHTTP(context.Context, string, string) (io.Closer, error)
}

// DjangoRestoreAdmission belongs after CORS and before security/session/auth.
// Restore uploads/execution are separate workflows; this gate does not register
// those endpoints or implement their upload handler and recovery protocol.
func DjangoRestoreAdmission(gate RestoreAdmissionGate) echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			r := c.Request()
			lease, err := gate.AcquireHTTP(r.Context(), r.Method, r.URL.Path)
			if err != nil {
				for _, denial := range []error{domain.ErrRestoreBusy, domain.ErrRestoreVolumeUnavailable, domain.ErrRestorePending} {
					if errors.Is(err, denial) {
						c.Response().Header().Set("Retry-After", "10")
						return c.JSON(503, map[string]string{"error": denial.Error()})
					}
				}
				return djangoServerError(c)
			}
			if lease != nil {
				defer lease.Close()
			}
			return next(c)
		}
	}
}
