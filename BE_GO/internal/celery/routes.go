package celery

import "strings"

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
