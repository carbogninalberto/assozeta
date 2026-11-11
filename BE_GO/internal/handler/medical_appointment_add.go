package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type MedicalAppointmentAddService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any, form bool, authorization *string,
		audit domain.LoginAuditContext) error
}

// MedicalAppointmentAddHandler serves subscription_medical_appointments_add (POST).
type MedicalAppointmentAddHandler struct {
	medical    *SubscriptionMedicalHandler
	add        MedicalAppointmentAddService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewMedicalAppointmentAddHandler(add MedicalAppointmentAddService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *MedicalAppointmentAddHandler {
	return &MedicalAppointmentAddHandler{medical: &SubscriptionMedicalHandler{}, add: add, actors: actors, identities: identities, audit: audit,
		operations: operations}
}

func (h *MedicalAppointmentAddHandler) Add(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "subscription_medical_appointments_add")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodPost, "Subscription Medical Appointments Add"); done {
		return err
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	request := c.Request()
	var authorization *string
	if values, ok := request.Header["Authorization"]; ok && len(values) > 0 {
		authorization = &values[0]
	}
	err = h.add.Add(write.Context, *identity, c.Param("uid"), request.Header.Get("X-Group-Id"), write.Body, write.Form, authorization, write.Audit)
	var invalid *dto.MedicalAppointmentAddInvalid
	switch {
	case err == nil:
		return c.JSON(http.StatusOK, map[string]string{"data": "medical appointment created."})
	case errors.As(err, &invalid):
		english := restLanguage(request) == "en"
		fields := map[string][]string{}
		for name, codes := range invalid.Fields {
			messages := make([]string, len(codes))
			for i, code := range codes {
				messages[i] = subscriptionCreateMessage(code, english)
			}
			fields[name] = messages
		}
		return c.JSON(http.StatusBadRequest, fields)
	}
	return h.medical.failure(c, err)
}
