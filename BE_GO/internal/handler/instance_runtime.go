package handler

import (
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

func (h *InstanceBackupActionsHandler) Runtime(c echo.Context) error {
	c.Response().Header().Set("Cache-Control", "no-store")
	actor, err := h.backupActionsBegin(c, "GET, POST, HEAD, OPTIONS", "instance_runtime")
	if actor == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Instance Runtime", "")
	case http.MethodGet, http.MethodHead:
		reply, err := h.actions.RuntimeStatus(c.Request().Context())
		return h.backupActionsReply(c, reply, err)
	case http.MethodPost:
		return h.serializerWrite(c, actor, func(request service.BackupActionsRequest, body any) (*service.BackupActionsReply, error) {
			return h.actions.RuntimeSwitch(c.Request().Context(), request, body)
		})
	}
	return h.notAllowed(c)
}
