package celery

import (
	"encoding/base64"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Message is one task message as kombu's Redis transport delivers it
// (protocol 2, JSON serializer, base64 body), decoded the way Celery's
// worker builds its Request.
type Message struct {
	// Raw is the exact list payload; Envelope its decoded form (kombu
	// re-serializes that form when it stores or restores the message).
	Raw        []byte
	Envelope   Dict
	Headers    Dict
	Properties Dict

	Task, ID     string
	Retries      int64
	ETA, Expires *time.Time
	// RootID, ParentID and Origin are the header values ("" for None).
	RootID, ParentID, Origin string
	ReplyTo                  string
	TimeLimit                []any
	// PublishedAt is the assozeta_published_at stamp (nil when absent).
	PublishedAt *float64

	Args   []any
	Kwargs Dict

	DeliveryTag          string
	Exchange, RoutingKey string
	Priority             int64
}

// standardHeaders are the protocol 2 headers as_task_v2 writes, in order.
var standardHeaders = []string{"lang", "task", "id", "shadow", "eta", "expires", "group", "group_index", "retries", "timelimit",
	"root_id", "parent_id", "argsrepr", "kwargsrepr", "origin", "ignore_result", "replaced_task_nesting", "stamped_headers", "stamps"}

// contextKeys are the keys Context._get_custom_headers drops: the Context
// class attributes plus lang, task, argsrepr, kwargsrepr and compression.
var contextKeys = map[string]bool{}

func init() {
	for _, key := range strings.Fields(`_children _protected args callbacks called_directly chain chord correlation_id
		delivery_info errbacks eta expires group group_index headers hostname id ignore_result is_eager kwargs logfile
		loglevel origin parent_id properties retries reply_to replaced_task_nesting root_id shadow taskset timelimit utc
		stamped_headers stamps __module__ __doc__ __init__ _get_custom_headers update clear get __repr__ __dict__
		__weakref__ __firstlineno__ __static_attributes__ lang task argsrepr kwargsrepr compression`) {
		contextKeys[key] = true
	}
}

func optionalString(value any) (string, bool) {
	switch v := value.(type) {
	case nil:
		return "", true
	case string:
		return v, true
	}
	return "", false
}

func integer(value any) (int64, bool) {
	n, ok := value.(Number)
	if !ok {
		return 0, false
	}
	v, err := strconv.ParseInt(string(n), 10, 64)
	return v, err == nil
}

// parseISO accepts the aware isoformat() Celery writes for eta/expires.
func parseISO(value any) (*time.Time, error) {
	text, ok := value.(string)
	if !ok {
		return nil, fmt.Errorf("%w: timestamp", ErrUndecodable)
	}
	for _, layout := range []string{"2006-01-02T15:04:05.999999-07:00", "2006-01-02T15:04:05-07:00"} {
		if t, err := time.Parse(layout, text); err == nil {
			return &t, nil
		}
	}
	return nil, fmt.Errorf("%w: timestamp %q", ErrUndecodable, text)
}

// ParseMessage decodes a Redis list payload. ErrUndecodable marks payloads
// outside the subset (other serializers or encodings, protocol 1, kombu
// types other than uuid, naive timestamps, canvas options).
func ParseMessage(raw []byte) (*Message, error) {
	value, err := DecodeJSON(raw)
	if err != nil {
		return nil, err
	}
	envelope, ok := value.(Dict)
	if !ok {
		return nil, fmt.Errorf("%w: envelope", ErrUndecodable)
	}
	m := &Message{Raw: raw, Envelope: envelope}
	get := func(d Dict, key string) any { v, _ := d.Get(key); return v }
	if get(envelope, "content-type") != "application/json" || get(envelope, "content-encoding") != "utf-8" {
		return nil, fmt.Errorf("%w: serializer", ErrUndecodable)
	}
	m.Headers, _ = get(envelope, "headers").(Dict)
	m.Properties, _ = get(envelope, "properties").(Dict)
	if m.Headers == nil || m.Properties == nil || get(m.Properties, "body_encoding") != "base64" || get(m.Headers, "lang") != "py" {
		return nil, fmt.Errorf("%w: protocol", ErrUndecodable)
	}
	if m.Task, ok = get(m.Headers, "task").(string); !ok || m.Task == "" {
		return nil, fmt.Errorf("%w: task name", ErrUndecodable)
	}
	if m.ID, ok = get(m.Headers, "id").(string); !ok || m.ID == "" {
		return nil, fmt.Errorf("%w: task id", ErrUndecodable)
	}
	if m.Retries, ok = integer(get(m.Headers, "retries")); !ok {
		return nil, fmt.Errorf("%w: retries", ErrUndecodable)
	}
	for key, target := range map[string]**time.Time{"eta": &m.ETA, "expires": &m.Expires} {
		if v := get(m.Headers, key); v != nil {
			if *target, err = parseISO(v); err != nil {
				return nil, err
			}
		}
	}
	var okRoot, okParent, okOrigin bool
	m.RootID, okRoot = optionalString(get(m.Headers, "root_id"))
	m.ParentID, okParent = optionalString(get(m.Headers, "parent_id"))
	m.Origin, okOrigin = optionalString(get(m.Headers, "origin"))
	if !okRoot || !okParent || !okOrigin {
		return nil, fmt.Errorf("%w: ids", ErrUndecodable)
	}
	if m.TimeLimit, ok = get(m.Headers, "timelimit").([]any); !ok || len(m.TimeLimit) != 2 {
		return nil, fmt.Errorf("%w: timelimit", ErrUndecodable)
	}
	if stamp, present := m.Headers.Get("assozeta_published_at"); present && stamp != nil {
		n, ok := stamp.(Number)
		if !ok {
			return nil, fmt.Errorf("%w: publish stamp", ErrUndecodable)
		}
		f, err := strconv.ParseFloat(string(n), 64)
		if err != nil {
			return nil, fmt.Errorf("%w: publish stamp", ErrUndecodable)
		}
		m.PublishedAt = &f
	}
	if m.ReplyTo, ok = optionalString(get(m.Properties, "reply_to")); !ok {
		return nil, fmt.Errorf("%w: reply_to", ErrUndecodable)
	}
	if m.DeliveryTag, ok = get(m.Properties, "delivery_tag").(string); !ok || m.DeliveryTag == "" {
		return nil, fmt.Errorf("%w: delivery tag", ErrUndecodable)
	}
	info, _ := get(m.Properties, "delivery_info").(Dict)
	m.Exchange, _ = get(info, "exchange").(string)
	if m.RoutingKey, ok = get(info, "routing_key").(string); !ok {
		return nil, fmt.Errorf("%w: delivery info", ErrUndecodable)
	}
	if p := get(m.Properties, "priority"); p != nil {
		if m.Priority, ok = integer(p); !ok {
			return nil, fmt.Errorf("%w: priority", ErrUndecodable)
		}
	}
	text, _ := get(envelope, "body").(string)
	body, err := base64.StdEncoding.DecodeString(text)
	if err != nil {
		return nil, fmt.Errorf("%w: body encoding", ErrUndecodable)
	}
	decoded, err := DecodeJSON(body)
	if err != nil {
		return nil, err
	}
	if decoded, err = KombuValue(decoded); err != nil {
		return nil, err
	}
	parts, ok := decoded.([]any)
	if !ok || len(parts) != 3 {
		return nil, fmt.Errorf("%w: body", ErrUndecodable)
	}
	args, ok1 := parts[0].([]any)
	kwargs, ok2 := parts[1].(Dict)
	embed, ok3 := parts[2].(Dict)
	m.Args, m.Kwargs = args, kwargs
	if !ok1 || !ok2 || !ok3 {
		return nil, fmt.Errorf("%w: body", ErrUndecodable)
	}
	for _, key := range []string{"callbacks", "errbacks", "chain", "chord"} {
		if get(embed, key) != nil {
			return nil, fmt.Errorf("%w: canvas", ErrUndecodable)
		}
	}
	return m, nil
}

// Plain reports a message without the options this package does not
// republish: group, shadow, stamping, expiry, time limits and a priority.
func (m *Message) Plain() bool { return m.PlainWith(false) }

// PlainWith is Plain, also accepting time limits when timeLimits is true
// (tasks the worker runs in a prefork child, which applies them).
func (m *Message) PlainWith(timeLimits bool) bool {
	if !m.plainOptions() {
		return false
	}
	if timeLimits {
		return true
	}
	return m.TimeLimit[0] == nil && m.TimeLimit[1] == nil
}

// Redelivered is delivery_info["redelivered"], set by kombu when it restores
// an unacknowledged or rejected message.
func (m *Message) Redelivered() bool {
	info, _ := m.Properties.Get("delivery_info")
	d, _ := info.(Dict)
	value, _ := d.Get("redelivered")
	flag, _ := value.(bool)
	return flag
}

func (m *Message) plainOptions() bool {
	for _, key := range []string{"shadow", "group", "group_index", "stamped_headers", "expires"} {
		if v, _ := m.Headers.Get(key); v != nil {
			return false
		}
	}
	stamps, _ := m.Headers.Get("stamps")
	if d, ok := stamps.(Dict); !ok || len(d) != 0 {
		return false
	}
	nesting, _ := integer(func() any { v, _ := m.Headers.Get("replaced_task_nesting"); return v }())
	return m.Priority == 0 && nesting == 0 && m.Exchange == ""
}

// Republish is a message the running task publishes for itself
// (Task.retry, or the maintenance deferral of CoordinatedTask):
// signature_from_request(request, countdown=..., retries=...).apply_async().
type Republish struct {
	Retries     int64
	ETA         time.Time
	DeliveryTag string
	// Origin is the publisher's nodename, used when the request has none.
	Origin string
}

// EncodeRepublish returns the payload Celery publishes for a request that
// PlainWith(true) accepts: the same id, args, kwargs, time limits (the
// request's timelimit, as_execution_options), reply_to and custom headers
// (the restore stamp is kept), root_id defaulting to the task id, parent_id
// to the running task, the request's origin in the headers and properties,
// and the queue the message came from (the anonymous exchange).
func EncodeRepublish(m *Message, r Republish) ([]byte, error) {
	if !m.PlainWith(true) {
		return nil, fmt.Errorf("%w: republish options", ErrUnsupportedValue)
	}
	// signature_from_request passes request.args, a tuple: argsrepr is the
	// tuple's repr whatever the original message's argsrepr was.
	shown := tuple(m.Args)
	if shown == nil {
		shown = tuple{}
	}
	argsrepr, err := saferepr(shown, reprMaxSize)
	if err != nil {
		return nil, err
	}
	kwargsrepr, err := saferepr(m.Kwargs, reprMaxSize)
	if err != nil {
		return nil, err
	}
	args := m.Args
	if args == nil {
		args = []any{}
	}
	body, err := appendJSON(nil, []any{args, m.Kwargs, Dict{{"callbacks", nil}, {"errbacks", nil}, {"chain", nil}, {"chord", nil}}})
	if err != nil {
		return nil, err
	}
	root, parent, origin := m.RootID, m.ParentID, m.Origin
	if root == "" {
		root = m.ID
	}
	if parent == "" {
		parent = m.ID
	}
	if origin == "" {
		origin = r.Origin
	}
	headers := Dict{
		{"lang", "py"}, {"task", m.Task}, {"id", m.ID}, {"shadow", nil}, {"eta", pythonISOFormat(r.ETA)},
		{"expires", nil}, {"group", nil}, {"group_index", nil}, {"retries", r.Retries}, {"timelimit", []any{m.TimeLimit[0], m.TimeLimit[1]}},
		{"root_id", root}, {"parent_id", parent}, {"argsrepr", argsrepr}, {"kwargsrepr", kwargsrepr}, {"origin", origin},
		{"ignore_result", false}, {"replaced_task_nesting", 0}, {"stamped_headers", nil}, {"stamps", Dict{}},
	}
	for _, item := range m.Headers {
		if !contextKeys[item.Key] {
			headers = append(headers, item)
		}
	}
	replyTo := m.ReplyTo
	properties := Dict{{"correlation_id", m.ID}, {"reply_to", replyTo}, {"origin", origin}, {"delivery_mode", 2},
		{"delivery_info", Dict{{"exchange", ""}, {"routing_key", m.RoutingKey}}}, {"priority", 0},
		{"body_encoding", "base64"}, {"delivery_tag", r.DeliveryTag}}
	out := []byte(`{"body": "`)
	out = append(out, base64.StdEncoding.EncodeToString(body)...)
	out = append(out, `", "content-encoding": "utf-8", "content-type": "application/json", "headers": `...)
	if out, err = appendJSON(out, headers); err != nil {
		return nil, err
	}
	out = append(out, `, "properties": `...)
	if out, err = appendJSON(out, properties); err != nil {
		return nil, err
	}
	return append(out, '}'), nil
}

// RepublishQueue is the queue signature_from_request selects: the routing
// key of a message sent to the anonymous exchange.
func (m *Message) RepublishQueue() string { return m.RoutingKey }
