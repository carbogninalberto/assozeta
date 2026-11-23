package celery

import (
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
// anonymous exchange with the queue as routing key). nil keeps every task on
// DefaultQueue, as before.
func (p *Producer) WithRoutes(routes map[string]string) *Producer {
	p.routes = routes
	return p
}

func (p *Producer) queueFor(task Task) string {
	if task.Queue != "" {
		return task.Queue
	}
	if queue, ok := p.routes[task.Name]; ok {
		return queue
	}
	return DefaultQueue
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

func routeEnvelope(raw []byte, queue string) ([]byte, error) {
	value, err := DecodeJSON(raw)
	if err != nil {
		return nil, errors.New("invalid task envelope")
	}
	envelope, _ := value.(Dict)
	properties, _ := envelope.Get("properties")
	fields, _ := properties.(Dict)
	delivery, _ := fields.Get("delivery_info")
	info, ok := delivery.(Dict)
	if !ok {
		return nil, errors.New("invalid task delivery info")
	}
	fields.Set("delivery_info", info.Set("routing_key", queue))
	return EncodeJSON(envelope)
}
