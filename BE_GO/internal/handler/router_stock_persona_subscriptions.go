package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// errRouterStockSubscriptionWrite refuses the stock create, update and
// partial_update of AssociateSubscriptionSerializer (a ModelSerializer write
// of every Subscription field through Subscription.save()): not ported.
var errRouterStockSubscriptionWrite = errors.New("AssociateSubscriptionSerializer writes are not ported")

// RegisterPersonaSubscriptions adds router.register(r'personas-subscriptions',
// AssociateSubscriptionViewSet): list(request) with pk None, the stock
// retrieve, destroy (Subscription.delete() through the Collector), and the
// viewset's delete() on the list route. The stock create, update and
// partial_update are refused before any write.
func (h *RouterStockHandler) RegisterPersonaSubscriptions(writes *PersonasWritesHandler, destroy *service.RouterStockDestroy) {
	subscriptions := writes.subscriptions
	refuse := func(c echo.Context, _ *domain.ResolvedIdentity, _ string) error {
		return messageReadsError(c, errRouterStockSubscriptionWrite)
	}
	render := func(c echo.Context, body any, err error) error {
		if err != nil {
			return personaWriteError(c, err)
		}
		return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
	}
	h.register(&routerStockViewset{
		prefix: "personas-subscriptions", name: "Associate Subscription",
		formatted: map[string]bool{"retrieve": true, "destroy": true},
		list: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			request := c.Request()
			body, err := subscriptions.RouterList(request.Context(), *identity, parseReferenceForm(request.URL.RawQuery), request.Header.Get("X-Group-Id"))
			return render(c, body, err)
		},
		listDelete: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return personaWriteError(c, subscriptions.DeleteWithoutPK(c.Request().Context(), *identity))
		},
		create: refuse, update: refuse, partialUpdate: refuse,
		postFields: func(echo.Context, *domain.ResolvedIdentity) (map[string]any, error) {
			return associateSubscriptionFields(), nil
		},
		putFields: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (map[string]any, error) {
			_, err := subscriptions.Locate(c.Request().Context(), *identity, pk, c.Request().Header.Get("X-Group-Id"))
			switch {
			case errors.Is(err, service.ErrPersonaNotSportAssociation), errors.Is(err, service.ErrPersonaInvalidPK),
				errors.Is(err, domain.ErrPersonaSubscriptionNotFound):
				return nil, nil
			case err != nil:
				return nil, err
			}
			return associateSubscriptionFields(), nil
		},
		retrieve: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			body, err := subscriptions.Retrieve(c.Request().Context(), *identity, pk, c.Request().Header.Get("X-Group-Id"))
			return render(c, body, err)
		},
		destroy: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			header := c.Request().Header.Get("X-Group-Id")
			row, err := subscriptions.Locate(c.Request().Context(), *identity, pk, header)
			if err != nil {
				return personaWriteError(c, err)
			}
			write, release, err := admitRESTWrite(c, writes.operations, writes.audit)
			if write == nil {
				return err
			}
			defer release()
			if err = destroy.Delete(write.Context, *identity, "application.Subscription", row.ID, header, write.Audit); err != nil {
				return personaWriteError(c, err)
			}
			return c.NoContent(http.StatusNoContent)
		},
	})
}

// associateSubscriptionFields is SimpleMetadata's description of
// AssociateSubscriptionSerializer (Meta.exclude signature_storage_key; the
// nested associate is BasicAssociateSerializer, as the personas tutors').
func associateSubscriptionFields() map[string]any {
	field := func(kind, label string, readOnly, required bool, extra map[string]any) map[string]any {
		value := map[string]any{"type": kind, "required": required, "read_only": readOnly, "label": label}
		for key, item := range extra {
			value[key] = item
		}
		return value
	}
	choices := func(pairs ...[2]any) map[string]any {
		list := make([]map[string]any, len(pairs))
		for i, pair := range pairs {
			list[i] = map[string]any{"value": pair[0], "display_name": pair[1]}
		}
		return map[string]any{"choices": list}
	}
	tutors := personaOptionsPost()["POST"].(map[string]any)["tutors"].(map[string]any)
	tutor := tutors["child"].(map[string]any)["children"].(map[string]any)["tutor"].(map[string]any)
	associate := map[string]any{}
	for key, value := range tutor {
		associate[key] = value
	}
	associate["label"] = "Associate"
	fields := map[string]any{
		"subscription_id": field("string", "Subscription id", false, false, nil),
		"active":          field("boolean", "Active", true, false, nil), "is_current": field("boolean", "Is current", true, false, nil),
		"is_next_year": field("boolean", "Is next year", true, false, nil), "current_year": field("string", "Current year", true, false, nil),
		"medical": field("field", "Medical", true, false, nil), "medical_expiration_date": field("field", "Medical expiration date", true, false, nil),
		"age": field("field", "Age", true, false, nil), "renewal_available": field("field", "Renewal available", true, false, nil),
		"signature_present": field("field", "Signature present", true, false, nil), "associate": associate,
		"custom_data": field("field", "Custom data", false, false, nil), "additional_fields": field("field", "Additional fields", false, false, nil),
		"type": field("choice", "Type", false, false, choices([2]any{1, "associato"}, [2]any{2, "associato e tesserato"}, [2]any{3, "tesserato"})),
		"role": field("choice", "Role", false, false, choices([2]any{1, "Socio ordinario"}, [2]any{2, "Consigliere"}, [2]any{3, "Segretario"},
			[2]any{4, "Vice Presidente"}, [2]any{5, "Presidente"}, [2]any{6, "Socio volontario"}, [2]any{7, "Socio sostenitore"},
			[2]any{8, "Tesoriere"})),
		"acceptance_date": field("date", "Acceptance date", false, false, nil), "resignation_date": field("date", "Resignation date", false, false, nil),
		"status_flag": field("choice", "Status flag", false, false, choices([2]any{1, "non firmata"}, [2]any{2, "in attesa"},
			[2]any{3, "rifiutata"}, [2]any{4, "accettata"}, [2]any{5, "ritirata"})),
		"creation_date": field("datetime", "Creation date", false, false, nil), "start_date": field("date", "Start date", false, false, nil),
		"end_date":      field("date", "End date", false, false, nil),
		"signature_url": field("string", "Signature url", false, false, map[string]any{"help_text": "S3 URL for signature image", "max_length": 1024}),
		"archived":      field("boolean", "Archived", false, false, nil), "draft": field("boolean", "Draft", false, false, nil),
		"trial": field("boolean", "Trial", false, false, nil), "meta": field("field", "Meta", false, false, nil),
		"subscription_number": field("string", "Subscription number", false, false, nil),
		"subscription_type":   field("string", "Subscription type", false, false, map[string]any{"max_length": 255}),
		"deleted":             field("boolean", "Deleted", false, false, nil), "competitive": field("boolean", "Competitive", false, false, nil),
		"notes": field("string", "Notes", false, false, nil), "sport_association": field("field", "Sport association", false, true, nil),
		"supplier": field("field", "Supplier", false, false, nil), "instructor": field("field", "Instructor", false, false, nil),
		"user": field("field", "User", false, true, nil), "document_pdf": field("field", "Document pdf", false, false, nil),
		"payment": field("field", "Payment", false, false, nil), "group": field("field", "Group", false, false, nil),
		"tags": field("field", "Tags", false, false, nil),
	}
	return fields
}
