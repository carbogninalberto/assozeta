package celery

import (
	"encoding/json"
	"testing"
)

// TestEncodeParentIDs: send_task inside a worker task takes root_id from the
// running request and parent_id from its id; outside a task the root is the
// message itself and the parent None.
func TestEncodeParentIDs(t *testing.T) {
	envelope := Envelope{ID: "child", DeliveryTag: "tag", ReplyTo: "reply", Origin: "gen1@host", PublishedAt: 1.5}
	for _, c := range []struct {
		task         Task
		root, parent any
	}{
		{Task{Name: "workflow_block_execute"}, "child", nil},
		{Task{Name: "workflow_block_execute", RootID: "root", ParentID: "parent"}, "root", "parent"},
	} {
		payload, err := Encode(c.task, envelope)
		if err != nil {
			t.Fatal(err)
		}
		var message struct {
			Headers map[string]any `json:"headers"`
		}
		if err = json.Unmarshal(payload, &message); err != nil {
			t.Fatal(err)
		}
		if message.Headers["root_id"] != c.root || message.Headers["parent_id"] != c.parent {
			t.Fatalf("root %v parent %v", message.Headers["root_id"], message.Headers["parent_id"])
		}
	}
}
