package handler

import (
	"net/http"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/labstack/echo/v4"
)

type instanceHandoffTotals struct {
	HandedOff   uint64 `json:"handedOff"`
	Interrupted uint64 `json:"interrupted"`
	Skipped     uint64 `json:"skipped"`
	Disabled    uint64 `json:"disabled"`
}

type instanceHandoffRoute struct {
	Method  string          `json:"method"`
	Route   string          `json:"route"`
	Reason  string          `json:"reason"`
	Outcome handoff.Outcome `json:"outcome"`
	Cause   string          `json:"cause"`
	Count   uint64          `json:"count"`
	LastAt  string          `json:"lastAt"`
}

type instanceHandoffStats struct {
	Beta           bool                   `json:"beta"`
	HandoffEnabled bool                   `json:"handoffEnabled"`
	Since          string                 `json:"since"`
	Totals         instanceHandoffTotals  `json:"totals"`
	Dropped        uint64                 `json:"dropped"`
	Routes         []instanceHandoffRoute `json:"routes"`
}

func instanceHandoffStatsBody(s handoff.StatsSnapshot, enabled bool) any {
	routes := make([]instanceHandoffRoute, 0, len(s.Rows))
	for _, row := range s.Rows {
		routes = append(routes, instanceHandoffRoute{
			Method:  row.Method,
			Route:   row.Route,
			Reason:  row.Reason,
			Outcome: row.Outcome,
			Cause:   row.Cause,
			Count:   row.Count,
			LastAt:  row.Last.UTC().Format(time.RFC3339),
		})
	}
	return instanceHandoffStats{
		Beta:           true,
		HandoffEnabled: enabled,
		Since:          s.Since.UTC().Format(time.RFC3339),
		Totals: instanceHandoffTotals{
			HandedOff:   s.Totals[handoff.OutcomeHandedOff],
			Interrupted: s.Totals[handoff.OutcomeInterrupted],
			Skipped:     s.Totals[handoff.OutcomeSkipped],
			Disabled:    s.Totals[handoff.OutcomeDisabled],
		},
		Dropped: s.Dropped,
		Routes:  routes,
	}
}

// SetHandoff supplies the server's handoff configuration for the report.
func (h *InstanceAdminHandler) SetHandoff(hf *handoff.Handoff) {
	h.handoff = hf
}

// Handoff serves the owner-only beta refusal report.
func (h *InstanceAdminHandler) Handoff(c echo.Context) error {
	if ok, err := h.begin(c, "GET, HEAD, OPTIONS", "instance_handoff", true); !ok {
		return err
	}
	method := c.Request().Method
	switch {
	case method == http.MethodOptions:
		return supplierOptions(c, "Instance Handoff", "")
	case isRead(method):
		return c.JSON(http.StatusOK, instanceHandoffStatsBody(handoff.Stats(), h.handoff.Enabled()))
	}
	return h.notAllowed(c)
}
