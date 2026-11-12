package handler

import (
	"context"
	"mime"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/labstack/echo/v4"
)

// restWrite is an admitted write request: the parsed body, whether it came
// form-encoded (DRF's HTML input rules), its audit context and the admitted
// context for the service call.
type restWrite struct {
	Context context.Context
	Body    any
	Form    bool
	Audit   domain.LoginAuditContext
}

// admitRESTWrite runs the shared write prologue after identity and method
// dispatch: operation tracking (graceful shutdown), the request body, the audit
// context and admission. A nil write means an error response was written and
// the caller returns err; otherwise the caller defers release.
func admitRESTWrite(c echo.Context, operations *paymentcategorylifecycle.Lifecycle, audit LoginAuditReader) (*restWrite, func(), error) {
	request := c.Request()
	tracked, err := operations.Track(request.Context())
	if err != nil {
		return nil, nil, paymentCategoryError(c, err)
	}
	body, err := readMessageMutationsBody(c)
	if err != nil {
		tracked.Release()
		return nil, nil, restError(c, err)
	}
	media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	auditContext, err := audit(request)
	if err != nil {
		tracked.Release()
		return nil, nil, restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		tracked.Release()
		return nil, nil, paymentCategoryError(c, err)
	}
	return &restWrite{Context: ctx, Body: body, Form: media == "application/x-www-form-urlencoded" || media == "multipart/form-data",
		Audit: auditContext}, tracked.Release, nil
}
