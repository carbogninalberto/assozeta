package service

import (
	"encoding/json"
	"os"
	"testing"
)

// testdata/agent_routes.json holds route_message results and the
// _FOLLOW_UP_ANSWER matches computed by CPython 3.13.16 on BE/application.
func TestAgentRoutingMatchesPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/agent_routes.json")
	if err != nil {
		t.Fatal(err)
	}
	var data struct {
		Routes    [][]any `json:"routes"`
		Followups [][]any `json:"followups"`
	}
	if err := json.Unmarshal(raw, &data); err != nil {
		t.Fatal(err)
	}
	for _, item := range data.Routes {
		route := agentRouteMessage(item[0].(string), item[1].(bool))
		if string(route.intent) != item[2].(string) || route.query != item[3].(string) {
			t.Errorf("%q pending=%v: got %s %q, want %s %q", item[0], item[1], route.intent, route.query, item[2], item[3])
		}
	}
	for _, item := range data.Followups {
		if got := agentRequiresFollowUp(item[0].(string)); got != item[1].(bool) {
			t.Errorf("follow-up %q: got %v", item[0], got)
		}
	}
}
