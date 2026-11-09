// Package celeryworker executes Celery task messages from the dedicated Go
// queue the way a Django Celery worker (celery 5.6, kombu 5.6 Redis
// transport, django_celery_results 2.5 with result_extended off) does:
//
//   - kombu ack emulation: BRPOP, the unacked hash and index, early ack when
//     the task starts, restore of messages past the visibility timeout under
//     unacked_mutex, and restore (RPUSH, redelivered) of held messages at
//     warm shutdown;
//   - Request handling: an expired message is stored REVOKED
//     (TaskRevokedError('expired')) and acknowledged; an eta in the future is
//     held unacknowledged until due;
//   - CoordinatedTask (BE/instance/restore/task_base.py): the shared
//     operation lock and pending-restore check defer the message by 15 s
//     (republished with the same retries, RETRY Busy), and a data restore
//     completed after the assozeta_published_at stamp fails it
//     (StaleDataTask);
//   - Task.retry: the same id republished with retries+1 and eta, parent and
//     root ids from the request, the stamp kept; past max_retries the
//     original exception fails the task;
//   - django-db results: one TaskResult row per task id (status, kombu-JSON
//     result, meta {"children": [...]}; task name, args, worker and
//     traceback stay NULL as result_extended is off);
//   - prefork tasks (prefork.go): a child process per execution with
//     acks_late, reject_on_worker_lost and soft/hard time limits applied
//     as billiard's AsynPool applies them.
//
// Messages the Go side does not run (an unregistered name, a payload or
// option outside the decoded subset, or an input the task's Plan refuses)
// are handed off unchanged to Django's "celery" queue before any effect.
package celeryworker

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// Exception is a Python exception as the result backend stores it
// (Backend.prepare_exception for the json serializer).
type Exception struct {
	Type, Module string
	Args         []any
}

func (e *Exception) Error() string { return "celery task exception: " + e.Module + "." + e.Type }

// Encode is the result column: {"exc_type", "exc_message", "exc_module"}.
func (e *Exception) Encode() (string, error) {
	args := e.Args
	if args == nil {
		args = []any{}
	}
	raw, err := celery.EncodeJSON(celery.Dict{{Key: "exc_type", Value: e.Type}, {Key: "exc_message", Value: args},
		{Key: "exc_module", Value: e.Module}})
	return string(raw), err
}

// Retry is Task.retry(exc=Exc, countdown=Countdown) raised by the task.
type Retry struct {
	Exc       *Exception
	Countdown int64
	// Maintenance is task_base.defer_for_maintenance: the same command is
	// republished with its retries unchanged (retried until the broker
	// accepts it), outside the task's max_retries.
	Maintenance bool
}

func (r *Retry) Error() string { return "celery task retry: " + r.Exc.Error() }

// ErrHandoff from Task.Plan hands the message to Django's queue.
var ErrHandoff = errors.New("celeryworker: handed off to the Django worker")

// Request is the running task's request (Task.request).
type Request struct {
	*celery.Message
	// Hostname is the worker nodename.
	Hostname string
	// State carries what the task's Plan prepared for Run.
	State    any
	children []string
}

// AddChild records a message published by the task (apply_async adds it to
// request.children, stored in the result meta).
func (r *Request) AddChild(id string) { r.children = append(r.children, id) }

// Children are the ids recorded with AddChild, in order.
func (r *Request) Children() []string { return append([]string(nil), r.children...) }

// Task is one registered task implementation.
type Task struct {
	// MaxRetries is the task's max_retries; negative means None.
	MaxRetries int64
	// Plan runs before the acknowledgement and must have no effect; it
	// returns ErrHandoff for inputs outside the ported subset.
	Plan func(context.Context, *Request) error
	// Run is the task body. It returns the task's return value, a *Retry,
	// an *Exception, or another error (stored as a database failure).
	Run func(context.Context, *Request) (any, error)

	// Cleanup, when set, runs after the message was handled (handed off,
	// deferred, failed or completed) to release what Plan prepared.
	Cleanup func(*Request)

	// Prefork runs the task in a child process, as Celery's prefork pool
	// does, which is what time limits and worker-loss handling need (see
	// prefork.go). The options below require it.
	Prefork bool
	// AcksLate acknowledges the message after the outcome is stored.
	AcksLate bool
	// RejectOnWorkerLost requeues an acks_late message whose child died.
	RejectOnWorkerLost bool
	// TimeLimit and SoftTimeLimit are the task's time_limit and
	// soft_time_limit in seconds ("" for None); the message's timelimit
	// header wins when truthy.
	TimeLimit, SoftTimeLimit celery.Number
}

// Results stores TaskResult rows (DatabaseBackend._store_result).
type Results interface {
	StoreResult(ctx context.Context, taskID, status, result, meta string) error
}

// Admission is CoordinatedTask's operation lock and restore barrier.
type Admission interface {
	Acquire(ctx context.Context) (io.Closer, *time.Time, error)
}

// Options bound the worker.
type Options struct {
	Concurrency int
	// HandoffQueue is Django's queue ("celery").
	HandoffQueue string
	// Origin is the worker's nodename (gen<pid>@<host>), used for republished
	// messages whose request has no origin.
	Origin       string
	Hostname     string
	RestoreEvery time.Duration
	DrainTimeout time.Duration
	// Coordinated mirrors a configured DATA_RESTORE_LOCK_PATH.
	Coordinated bool
	// Child builds the command of a prefork child (ChildCommand); required
	// when a task is Prefork.
	Child func() *exec.Cmd
}

// Worker consumes one queue.
type Worker struct {
	transport *celery.Transport
	tasks     map[string]Task
	results   Results
	admission Admission
	options   Options
	logger    *slog.Logger
	now       func() time.Time
	newID     func() string

	mu    sync.Mutex
	held  map[string]*heldMessage
	ready []*Delivery
	// jobs is billiard's job counter: every task sent to the pool.
	jobs atomic.Int64
}

type heldMessage struct {
	timer    *time.Timer
	delivery *Delivery
}

func New(transport *celery.Transport, tasks map[string]Task, results Results, admission Admission, options Options, logger *slog.Logger) (*Worker, error) {
	if options.Concurrency < 1 || options.Concurrency > 32 || options.HandoffQueue == "" || options.HandoffQueue == transport.Queue {
		return nil, errors.New("celeryworker: invalid options")
	}
	if options.RestoreEvery <= 0 {
		options.RestoreEvery = celery.DefaultRestoreEvery
	}
	if options.DrainTimeout <= 0 {
		options.DrainTimeout = 30 * time.Second
	}
	for _, task := range tasks {
		if !task.Prefork && (task.AcksLate || task.RejectOnWorkerLost || task.TimeLimit != "" || task.SoftTimeLimit != "") {
			return nil, errors.New("celeryworker: acks_late, reject_on_worker_lost and time limits need a prefork task")
		}
		if task.Prefork && options.Child == nil {
			return nil, errors.New("celeryworker: prefork tasks need a child launcher")
		}
	}
	return &Worker{transport: transport, tasks: tasks, results: results, admission: admission, options: options,
		logger: logger, now: time.Now, newID: uuid.NewString, held: map[string]*heldMessage{}}, nil
}

// Run consumes until ctx is cancelled, then waits for running tasks and
// restores held messages (Celery's warm shutdown).
func (w *Worker) Run(ctx context.Context) error {
	background := context.WithoutCancel(ctx)
	if err := w.transport.Declare(ctx); err != nil {
		return fmt.Errorf("declare queue: %w", err)
	}
	if err := w.transport.RestoreVisible(ctx); err != nil {
		return fmt.Errorf("restore visible: %w", err)
	}
	slots := make(chan struct{}, w.options.Concurrency)
	var running sync.WaitGroup
	lastRestore := w.now()
	w.logger.Info("celery consumer ready", "queue", w.transport.Queue, "concurrency", w.options.Concurrency)
	var loopErr error
	for ctx.Err() == nil {
		if w.now().Sub(lastRestore) >= w.options.RestoreEvery {
			lastRestore = w.now()
			if err := w.transport.RestoreVisible(ctx); err != nil && ctx.Err() == nil {
				w.logger.Warn("celery restore visible failed", "code", "celery_restore_failed")
			}
		}
		select {
		case slots <- struct{}{}:
		case <-ctx.Done():
			continue
		}
		if delivery := w.nextReady(); delivery != nil {
			running.Add(1)
			go func() {
				defer running.Done()
				defer func() { <-slots }()
				w.execute(background, delivery)
			}()
			continue
		}
		delivery, err := w.transport.Get(ctx, time.Second)
		if err != nil || delivery == nil {
			<-slots
			if err != nil && ctx.Err() == nil {
				w.logger.Warn("celery broker read failed", "code", "celery_broker_failed")
				time.Sleep(time.Second)
			}
			continue
		}
		if !w.receive(background, delivery) {
			<-slots
			continue
		}
		running.Add(1)
		go func() {
			defer running.Done()
			defer func() { <-slots }()
			w.execute(background, delivery)
		}()
	}
	done := make(chan struct{})
	go func() { running.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(w.options.DrainTimeout):
		loopErr = domain.ErrJobDrainTimeout
	}
	w.mu.Lock()
	tags := make([]string, 0, len(w.held)+len(w.ready))
	for tag, held := range w.held {
		held.timer.Stop()
		tags = append(tags, tag)
	}
	for _, d := range w.ready {
		tags = append(tags, d.Message.DeliveryTag)
	}
	w.held, w.ready = map[string]*heldMessage{}, nil
	w.mu.Unlock()
	if len(tags) > 0 {
		w.logger.Info("restoring held messages", "count", len(tags))
	}
	for _, tag := range tags {
		if err := w.transport.RestoreByTag(background, tag, false); err != nil {
			loopErr = errors.Join(loopErr, fmt.Errorf("restore held message: %w", err))
		}
	}
	return loopErr
}

// receive is the worker strategy: it returns true when the message must be
// executed now; otherwise it was handed off, revoked or held.
func (w *Worker) receive(ctx context.Context, d *Delivery) bool {
	m := d.Message
	if d.Err != nil || m == nil {
		w.handoff(ctx, d, "undecodable")
		return false
	}
	task, ok := w.tasks[m.Task]
	if !ok {
		w.handoff(ctx, d, "unregistered")
		return false
	}
	if m.Expires != nil && w.now().After(*m.Expires) {
		w.revoke(ctx, d)
		return false
	}
	if !m.PlainWith(task.Prefork) || (task.Prefork && !validTimeLimits(m.TimeLimit)) {
		w.handoff(ctx, d, "options")
		return false
	}
	if m.ETA != nil {
		if wait := m.ETA.Sub(w.now()); wait > 0 {
			w.hold(ctx, d, wait)
			return false
		}
	}
	return true
}

// Delivery is celery.Delivery.
type Delivery = celery.Delivery

func (w *Worker) handoff(ctx context.Context, d *Delivery, reason string) {
	if err := w.transport.Handoff(ctx, d, w.options.HandoffQueue); err != nil {
		w.logger.Error("celery handoff failed", "code", "celery_handoff_failed", "reason", reason)
		return
	}
	w.logger.Info("celery message handed off", "queue", w.options.HandoffQueue, "reason", reason)
}

// revoke is Request._announce_revoked('expired'): REVOKED, then ack.
func (w *Worker) revoke(ctx context.Context, d *Delivery) {
	exc := &Exception{Type: "TaskRevokedError", Module: "celery.exceptions", Args: []any{"expired"}}
	w.store(ctx, d.Message.ID, "REVOKED", exc, nil)
	w.ack(ctx, d)
}

// hold keeps an eta message unacknowledged until due (Celery's timer);
// a due message waits for the next free slot.
func (w *Worker) hold(_ context.Context, d *Delivery, wait time.Duration) {
	tag := d.Message.DeliveryTag
	w.mu.Lock()
	defer w.mu.Unlock()
	w.held[tag] = &heldMessage{delivery: d, timer: time.AfterFunc(wait, func() {
		w.mu.Lock()
		defer w.mu.Unlock()
		if held, still := w.held[tag]; still {
			delete(w.held, tag)
			w.ready = append(w.ready, held.delivery)
		}
	})}
}

func (w *Worker) nextReady() *Delivery {
	w.mu.Lock()
	defer w.mu.Unlock()
	if len(w.ready) == 0 {
		return nil
	}
	d := w.ready[0]
	w.ready = w.ready[1:]
	return d
}

func (w *Worker) ack(ctx context.Context, d *Delivery) {
	if err := w.transport.Ack(ctx, d.Message.DeliveryTag); err != nil {
		w.logger.Error("celery ack failed", "code", "celery_ack_failed")
	}
}

func meta(children []string) string {
	list := make([]any, len(children))
	for i, id := range children {
		list[i] = []any{[]any{id, nil}, nil}
	}
	raw, _ := celery.EncodeJSON(celery.Dict{{Key: "children", Value: list}})
	return string(raw)
}

func (w *Worker) store(ctx context.Context, id, status string, value any, children []string) {
	var result string
	switch v := value.(type) {
	case *Exception:
		var err error
		if result, err = v.Encode(); err != nil {
			result = `{"exc_type": "EncodeError", "exc_message": [], "exc_module": "kombu.exceptions"}`
		}
	default:
		raw, err := celery.EncodeJSON(v)
		if err != nil {
			raw = []byte("null")
		}
		result = string(raw)
	}
	if err := w.results.StoreResult(ctx, id, status, result, meta(children)); err != nil {
		w.logger.Error("celery result store failed", "code", "celery_result_failed", "status", status)
	}
}

// databaseFailure stands for an infrastructure error inside the task (the
// Django code would raise a django.db error); the text is fixed so that no
// connection detail reaches the result row.
var databaseFailure = &Exception{Type: "OperationalError", Module: "django.db.utils", Args: []any{"database error"}}

// Busy messages of BE/instance/restore/locking.py.
var (
	busyPending = &Exception{Type: "Busy", Module: "instance.restore.locking"}
	busyLocked  = &Exception{Type: "Busy", Module: "instance.restore.locking",
		Args: []any{"Un’altra operazione sui dati è in corso. Riprova tra poco."}}
	busyVolume = &Exception{Type: "Busy", Module: "instance.restore.locking",
		Args: []any{"Il volume di coordinamento non è disponibile."}}
	staleData = &Exception{Type: "StaleDataTask", Module: "instance.restore.task_base",
		Args: []any{"Operazione annullata dopo il ripristino dei dati. Ripeti la richiesta."}}
)

func (w *Worker) execute(ctx context.Context, d *Delivery) {
	m := d.Message
	task := w.tasks[m.Task]
	job := w.jobs.Add(1) - 1
	if task.Prefork {
		w.executePrefork(ctx, d, task, job)
		return
	}
	request := &Request{Message: m, Hostname: w.options.Hostname}
	if task.Cleanup != nil {
		defer task.Cleanup(request)
	}
	if task.Plan != nil {
		if err := task.Plan(ctx, request); errors.Is(err, ErrHandoff) {
			w.handoff(ctx, d, "input")
			return
		}
	}
	// Request.execute_using_pool: acks_late is off, the message is
	// acknowledged when the task is accepted.
	w.ack(ctx, d)
	w.perform(ctx, ctx, request, task, false)
}

// coordinated is CoordinatedTask.__call__'s condition: a configured lock
// path, and a task name outside instance.restore.* (restore tasks hold the
// exclusive lock themselves).
func (w *Worker) coordinated(name string) bool {
	return w.options.Coordinated && !strings.HasPrefix(name, "instance.restore.")
}

// perform is CoordinatedTask admission, the task body and the stored
// outcome; the body runs with taskCtx (cancelled by a soft time limit in a
// prefork child). A retry records its own message among the request's
// children (Task.retry publishes with add_to_parent), as the Django prefork
// worker stores it.
func (w *Worker) perform(ctx, taskCtx context.Context, request *Request, task Task, child bool) {
	m := request.Message
	if w.coordinated(m.Task) {
		lease, completed, err := w.admission.Acquire(ctx)
		switch {
		case errors.Is(err, domain.ErrRestorePending):
			w.defer15(ctx, request, busyPending)
			return
		case errors.Is(err, domain.ErrRestoreBusy):
			w.defer15(ctx, request, busyLocked)
			return
		case errors.Is(err, domain.ErrRestoreVolumeUnavailable):
			w.defer15(ctx, request, busyVolume)
			return
		case err != nil:
			w.store(ctx, m.ID, "FAILURE", databaseFailure, nil)
			return
		}
		defer lease.Close()
		if completed != nil {
			cutoff := float64(completed.UnixMicro()) / 1e6
			if m.PublishedAt == nil || *m.PublishedAt < cutoff {
				w.store(ctx, m.ID, "FAILURE", staleData, nil)
				return
			}
		}
	}
	value, err := task.Run(taskCtx, request)
	if err != nil && child && errors.Is(context.Cause(taskCtx), ErrSoftTimeLimit) {
		var retry *Retry
		var exc *Exception
		if !errors.As(err, &retry) && !errors.As(err, &exc) {
			err = softTimeLimitExceeded
		}
	}
	var retry *Retry
	var exc *Exception
	switch {
	case err == nil:
		w.store(ctx, m.ID, "SUCCESS", value, request.children)
	case errors.As(err, &retry) && retry.Maintenance:
		w.republish(ctx, request, m.Retries, retry.Countdown, true)
		request.AddChild(m.ID)
		w.store(ctx, m.ID, "RETRY", retry.Exc, request.children)
	case errors.As(err, &retry):
		if task.MaxRetries >= 0 && m.Retries+1 > task.MaxRetries {
			w.store(ctx, m.ID, "FAILURE", retry.Exc, request.children)
			return
		}
		if !w.republish(ctx, request, m.Retries+1, retry.Countdown, false) {
			return // Reject(requeue=False): no result is stored
		}
		request.AddChild(m.ID)
		w.store(ctx, m.ID, "RETRY", retry.Exc, request.children)
	case errors.As(err, &exc):
		w.store(ctx, m.ID, "FAILURE", exc, request.children)
	default:
		w.logger.Error("celery task failed", "code", "celery_task_failed", "task", m.Task)
		w.store(ctx, m.ID, "FAILURE", databaseFailure, request.children)
	}
}

// defer15 is defer_for_maintenance(countdown=15): the same command is
// republished (retried until the broker accepts it) and recorded as a child,
// then the RETRY state is stored with the Busy exception.
func (w *Worker) defer15(ctx context.Context, request *Request, exc *Exception) {
	w.republish(ctx, request, request.Retries, 15, true)
	request.AddChild(request.ID)
	w.store(ctx, request.ID, "RETRY", exc, request.children)
}

func (w *Worker) republish(ctx context.Context, request *Request, retries, countdown int64, persist bool) bool {
	payload, err := celery.EncodeRepublish(request.Message, celery.Republish{Retries: retries,
		ETA: w.now().UTC().Add(time.Duration(countdown) * time.Second), DeliveryTag: w.newID(), Origin: w.options.Origin})
	if err != nil {
		w.logger.Error("celery republish encoding failed", "code", "celery_republish_failed")
		return false
	}
	delay := time.Second
	for {
		err = w.transport.Publish(ctx, request.RepublishQueue(), payload)
		if err == nil {
			return true
		}
		if !persist {
			w.logger.Error("celery retry publish failed", "code", "celery_republish_failed")
			return false
		}
		w.logger.Warn("celery maintenance publication failed", "code", "celery_republish_retry", "delay", strconv.Itoa(int(delay/time.Second)))
		time.Sleep(delay)
		delay = min(delay*2, 30*time.Second)
	}
}
