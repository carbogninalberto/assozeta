// Package celery publishes Celery task messages that the existing Django
// workers execute. It reproduces what Django's Task.delay()/apply_async()
// writes to the Redis broker with the pinned Celery 5.6 / kombu 5.6 (task
// protocol 2, JSON serializer, base64 body, the anonymous direct exchange
// to the default "celery" queue) and stamps the assozeta_published_at
// header that BE/instance/restore/task_base.py adds before every publish,
// so CoordinatedTask's restore fencing applies to Go-published work too.
// Django remains the owner and executor of every task. See
// docs/architecture/go-migration/celery-producer.md.
package celery

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// DefaultQueue is Celery's task_default_queue; BE/core/settings.py defines
// no queue or route settings, so every Django task is published there.
const DefaultQueue = "celery"

// reprMaxSize is Celery's argsrepr_maxsize / kwargsrepr_maxsize.
const reprMaxSize = 1024

// Task is one Task.apply_async() call.
type Task struct {
	// Name is the registered task name (@shared_task(name=...)).
	Name string
	// Args and Kwargs are the call's positional and keyword arguments; see
	// python.go for the supported value types.
	Args   []any
	Kwargs Dict
	// ArgsList marks apply_async(args=[...]) with a list: Celery's argsrepr
	// is then the list's repr ('[...]') instead of the tuple's.
	ArgsList bool
	// ETA is the absolute execution time Celery derives from countdown
	// (app.now() + countdown, UTC); nil publishes for immediate execution.
	ETA *time.Time
	// Expires is the absolute expiry Celery derives from expires (UTC).
	Expires *time.Time
	// Queue overrides DefaultQueue (a direct queue with the same routing key).
	Queue string
	// ID is apply_async(task_id=...): the message id (and root_id and
	// correlation_id) instead of a fresh uuid4.
	ID string
	// TimeLimit and SoftTimeLimit are the task's time_limit/soft_time_limit
	// options (@shared_task(time_limit=..., soft_time_limit=...)), which
	// Celery copies into the timelimit header.
	TimeLimit, SoftTimeLimit *int64
	// RootID and ParentID are what send_task/apply_async take from the
	// running worker task (current_worker_task): root_id is the request's
	// root_id or id, parent_id the request's id. Empty outside a task: the
	// root is the message's own id and the parent None.
	RootID, ParentID string
}

// Envelope holds the per-publish random and clock values of one message.
type Envelope struct {
	ID          string  // task id (uuid4); also correlation_id and root_id
	DeliveryTag string  // kombu's delivery_tag (uuid4)
	ReplyTo     string  // app.thread_oid
	Origin      string  // gen<pid>@<hostname>
	PublishedAt float64 // assozeta_published_at (time.time())
	// ExpirationMillis is kombu's "expiration" property: int(expires_s * 1000).
	ExpirationMillis *int64
}

// Encode returns the exact Redis list payload kombu's Redis transport pushes
// for the task: the JSON envelope with the protocol 2 headers, properties
// and the base64-encoded JSON body, in Celery's key order and separators.
func Encode(task Task, envelope Envelope) ([]byte, error) {
	if task.Name == "" {
		return nil, errors.New("celery: task name required")
	}
	queue := task.Queue
	if queue == "" {
		queue = DefaultQueue
	}
	args := tuple(task.Args)
	if args == nil {
		args = tuple{}
	}
	kwargs := task.Kwargs
	if kwargs == nil {
		kwargs = Dict{}
	}
	var shown any = args
	if task.ArgsList {
		shown = []any(args)
	}
	argsrepr, err := saferepr(shown, reprMaxSize)
	if err != nil {
		return nil, err
	}
	kwargsrepr, err := saferepr(kwargs, reprMaxSize)
	if err != nil {
		return nil, err
	}
	body, err := appendJSON(nil, []any{[]any(args), kwargs, Dict{{"callbacks", nil}, {"errbacks", nil}, {"chain", nil}, {"chord", nil}}})
	if err != nil {
		return nil, err
	}
	optionalTime := func(value *time.Time) any {
		if value == nil {
			return nil
		}
		return pythonISOFormat(*value)
	}
	var rootID, parentID any = envelope.ID, nil
	if task.RootID != "" {
		rootID = task.RootID
	}
	if task.ParentID != "" {
		parentID = task.ParentID
	}
	headers := Dict{
		{"lang", "py"}, {"task", task.Name}, {"id", envelope.ID}, {"shadow", nil}, {"eta", optionalTime(task.ETA)},
		{"expires", optionalTime(task.Expires)}, {"group", nil}, {"group_index", nil}, {"retries", 0}, {"timelimit", []any{optionalSeconds(task.TimeLimit), optionalSeconds(task.SoftTimeLimit)}},
		{"root_id", rootID}, {"parent_id", parentID}, {"argsrepr", argsrepr}, {"kwargsrepr", kwargsrepr}, {"origin", envelope.Origin},
		{"ignore_result", false}, {"replaced_task_nesting", 0}, {"stamped_headers", nil}, {"stamps", Dict{}},
	}
	properties := Dict{{"correlation_id", envelope.ID}, {"reply_to", envelope.ReplyTo}, {"delivery_mode", 2}}
	if envelope.ExpirationMillis != nil {
		properties = append(properties, Item{"expiration", strconv.FormatInt(*envelope.ExpirationMillis, 10)})
	}
	properties = append(properties, Item{"delivery_info", Dict{{"exchange", ""}, {"routing_key", queue}}}, Item{"priority", 0},
		Item{"body_encoding", "base64"}, Item{"delivery_tag", envelope.DeliveryTag})
	out := []byte(`{"body": "`)
	out = append(out, base64.StdEncoding.EncodeToString(body)...)
	out = append(out, `", "content-encoding": "utf-8", "content-type": "application/json", "headers": `...)
	if out, err = appendJSON(out, headers); err != nil {
		return nil, err
	}
	// before_task_publish: stamp_published_task's setdefault adds the last header.
	out = append(out[:len(out)-1], `, "assozeta_published_at": `...)
	out = append(out, pythonFloat(envelope.PublishedAt)...)
	out = append(out, `}, "properties": `...)
	if out, err = appendJSON(out, properties); err != nil {
		return nil, err
	}
	return append(out, '}'), nil
}

func optionalSeconds(value *int64) any {
	if value == nil {
		return nil
	}
	return *value
}

// Broker stores one encoded message on a queue.
type Broker interface {
	Push(ctx context.Context, queue string, payload []byte) error
}

// RedisBroker is kombu's Redis transport on the broker database (DB 0 of
// CELERY_BROKER_URL): declaring the queue adds its binding to
// _kombu.binding.<queue>, then the message is LPUSHed on the queue key.
type RedisBroker struct{ Client redis.Cmdable }

// bindingSeparator is kombu.transport.redis's sep ("\x06\x16").
const bindingSeparator = "\x06\x16"

func (b RedisBroker) Push(ctx context.Context, queue string, payload []byte) error {
	_, err := b.Client.TxPipelined(ctx, func(pipe redis.Pipeliner) error {
		pipe.SAdd(ctx, "_kombu.binding."+queue, queue+bindingSeparator+bindingSeparator+queue)
		pipe.LPush(ctx, queue, payload)
		return nil
	})
	return err
}

// Producer publishes tasks with Celery's default publish retry policy
// (task_publish_retry_policy: up to 3 retries after 0s, 0.2s and 0.4s).
type Producer struct {
	broker  Broker
	now     func() time.Time
	newID   func() string
	sleep   func(context.Context, time.Duration) error
	origin  string
	replyTo string
	routes  map[string]string
}

// NewProducer returns a producer for broker with the process's origin and
// reply_to values.
func NewProducer(broker Broker) *Producer {
	host, err := os.Hostname()
	if err != nil || host == "" {
		host = "localhost"
	}
	pid := os.Getpid()
	return &Producer{broker: broker, now: time.Now, newID: uuid.NewString, sleep: sleepContext,
		origin:  "gen" + strconv.Itoa(pid) + "@" + host,
		replyTo: uuid.NewMD5(uuid.NameSpaceOID, fmt.Appendf(nil, "%s-%x-go", host, pid)).String()}
}

// PublishError is a publish that failed after the retry policy.
type PublishError struct{ Err error }

func (e *PublishError) Error() string { return "celery: publish failed: " + e.Err.Error() }
func (e *PublishError) Unwrap() error { return e.Err }

var retryDelays = []time.Duration{0, 200 * time.Millisecond, 400 * time.Millisecond}

// Publish encodes and pushes the task and returns its id. The publish stamp
// is taken once, before the first attempt, like Celery's retries.
func (p *Producer) Publish(ctx context.Context, task Task) (string, error) {
	now := p.now()
	id := task.ID
	if id == "" {
		id = p.newID()
	}
	envelope := Envelope{ID: id, DeliveryTag: p.newID(), ReplyTo: p.replyTo, Origin: p.origin,
		PublishedAt: float64(now.UnixNano()) / 1e9}
	queue := p.queueFor(task)
	task.Queue = queue
	if task.Expires != nil {
		seconds := task.Expires.Sub(now).Seconds()
		millis := int64(0)
		if seconds > 0 {
			millis = int64(seconds * 1000)
		}
		envelope.ExpirationMillis = &millis
	}
	payload, err := Encode(task, envelope)
	if err != nil {
		return "", err
	}
	err = p.broker.Push(ctx, queue, payload)
	for _, delay := range retryDelays {
		if err == nil || !retryable(ctx, err) {
			break
		}
		if sleepErr := p.sleep(ctx, delay); sleepErr != nil {
			break
		}
		err = p.broker.Push(ctx, queue, payload)
	}
	if err != nil {
		return "", &PublishError{Err: err}
	}
	return envelope.ID, nil
}

// retryable: kombu's ensure() retries recoverable connection errors, not
// error replies from the server; a cancelled request is not retried.
func retryable(ctx context.Context, err error) bool {
	if ctx.Err() != nil || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false
	}
	var reply redis.Error
	return !errors.As(err, &reply)
}

func sleepContext(ctx context.Context, delay time.Duration) error {
	if delay <= 0 {
		return ctx.Err()
	}
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// Publisher is the interface services depend on.
type Publisher interface {
	Publish(ctx context.Context, task Task) (string, error)
}

// Recorder is an in-memory Publisher for unit tests.
type Recorder struct {
	mu    sync.Mutex
	Tasks []Task
	Err   error
}

func (r *Recorder) Publish(_ context.Context, task Task) (string, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.Err != nil {
		return "", r.Err
	}
	r.Tasks = append(r.Tasks, task)
	return uuid.NewString(), nil
}
