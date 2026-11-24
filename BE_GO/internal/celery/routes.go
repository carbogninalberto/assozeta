package celery

import (
	"bytes"
	"cmp"
	"encoding/json"
	"errors"
	"strings"
)

// GoQueue is the dedicated queue of the Go consumer (BE/core/task_routing.py
// GO_TASK_QUEUE).
const GoQueue = "assozeta-go"

// ParseRoutes reads GO_TASK_QUEUE_TASKS like core.task_routing.go_task_routes:
// comma-separated task names, blanks ignored. Every listed name goes to
// GoQueue; an empty value returns nil (no routes, Celery's default queue).
func ParseRoutes(value string) map[string]string {
	var routes map[string]string
	for _, name := range strings.Split(value, ",") {
		name = strings.TrimSpace(name)
		if name == "" {
			continue
		}
		if routes == nil {
			routes = map[string]string{}
		}
		routes[name] = GoQueue
	}
	return routes
}

// WithRoutes makes the producer send a task without an explicit Queue to the
// queue its name is routed to (Celery's task_routes {'queue': name}: the
// anonymous exchange with the queue as routing key). Without a legacy route
// the requested queue defaults to celery. Admission chooses the managed queue.
func (p *Producer) WithRoutes(routes map[string]string) *Producer {
	p.routes = routes
	return p
}

// queueFor selects the requested transport route. An admitted publication on
// a managed queue follows the effective runtime before it reaches Redis.
func (p *Producer) queueFor(task Task) string {
	return cmp.Or(task.Queue, p.routes[task.Name], DefaultQueue)
}

func ownedEmailTask(name string) bool {
	switch name {
	case "core.tasks.send_mail_async", "send_email_text", "send_email_template", "send_email_html":
		return true
	default:
		return false
	}
}

func ownedWorkflowTask(name string) bool {
	switch name {
	case "check_workflows_trigger", "check_workflows_trigger_cron", "workflow_block_execute":
		return true
	default:
		return false
	}
}

func ownedStorageTask(name string) bool {
	return name == "save_to_storage" || name == "export_invoices_to_zip"
}

func ownedPrintTask(name string) bool {
	switch name {
	case "print_document_subscription", "print_document_invoice", "print_document_customer_invoice",
		"print_document_compensation", "print_document_medical_appointment":
		return true
	default:
		return false
	}
}

func ownedAssociationDataTask(name string) bool {
	switch name {
	case "export_association_data", "import_association_data", "instance.restore.execute":
		return true
	default:
		return false
	}
}

// routeEnvelope replaces only the routing key. Protocol headers may contain
// Python surrogate escapes that the consumer's DecodeJSON cannot represent.
func routeEnvelope(raw []byte, queue string) ([]byte, error) {
	start, end := 0, len(raw)
	for _, key := range []string{"properties", "delivery_info", "routing_key"} {
		first, last, err := memberSpan(raw[start:end], key)
		if err != nil {
			return nil, err
		}
		start, end = start+first, start+last
	}
	routed, err := appendJSONString(bytes.Clone(raw[:start]), queue)
	if err != nil {
		return nil, err
	}
	return append(routed, raw[end:]...), nil
}

// memberSpan returns the byte offsets of an object member, leaving its value
// opaque. Like encoding/json, a repeated member name takes its last value.
func memberSpan(object []byte, key string) (int, int, error) {
	decoder := json.NewDecoder(bytes.NewReader(object))
	open, err := decoder.Token()
	if err != nil || open != json.Delim('{') {
		return 0, 0, errors.New("invalid task delivery info")
	}
	var value json.RawMessage
	var start, end int
	for decoder.More() {
		name, err := decoder.Token()
		if err != nil {
			return 0, 0, err
		}
		if err := decoder.Decode(&value); err != nil {
			return 0, 0, err
		}
		if name == key {
			end = int(decoder.InputOffset())
			start = end - len(value)
		}
	}
	if end == 0 {
		return 0, 0, errors.New("missing task delivery info")
	}
	return start, end, nil
}
