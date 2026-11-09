package celeryworker

// Prefork execution: what Celery 5.6's prefork pool (billiard AsynPool) does
// for a task, with one child process per task execution.
//
//   - The parent keeps the broker bookkeeping. The child runs Plan, the
//     CoordinatedTask admission, the body, and stores the result, as the pool
//     child does in trace_task, then reports "done".
//   - acks_late off: the message is acknowledged when the child accepts it
//     (Request.on_accepted); on: after the outcome (on_success, on_retry and
//     on_failure with acks_on_failure_or_timeout).
//   - Soft time limit: SIGUSR1 to the child (billiard's SIG_SOFT_TIMEOUT); the
//     child cancels the body's context with ErrSoftTimeLimit and an unhandled
//     cancellation is stored as SoftTimeLimitExceeded(). The hard limit fires
//     hard - soft seconds after the soft one (AsynPool._on_soft_timeout), or
//     at hard without a soft limit; Python's `or` applies the task defaults
//     when the header value is falsy.
//   - Hard time limit: the parent stores TimeLimitExceeded(hard), acknowledges
//     an acks_late message, then terminates the child (SIGTERM, then SIGKILL
//     after 0.1 s, billiard's _trywaitkill).
//   - Worker loss (the child exits without reporting): reject_on_worker_lost
//     with acks_late requeues the message (QoS.reject requeue: restored at the
//     far end of the queue, redelivered) and stores nothing; otherwise an
//     acks_late message is acknowledged and WorkerLostError('Worker exited
//     prematurely: <status> Job: <n>.') is stored, n being billiard's job
//     counter.
//
// The child is this executable started by Options.Child; it receives the
// message on stdin and writes its events to file descriptor 3.

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"sync"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"golang.org/x/sys/unix"
)

// ErrSoftTimeLimit is the cause of a prefork task context cancelled by the
// soft time limit.
var ErrSoftTimeLimit = errors.New("celeryworker: soft time limit exceeded")

var softTimeLimitExceeded = &Exception{Type: "SoftTimeLimitExceeded", Module: "billiard.exceptions"}

// Child events (one per line on file descriptor 3).
const (
	eventAccepted = "accepted"
	eventHandoff  = "handoff"
	eventDone     = "done"
)

type childInput struct {
	Raw []byte `json:"raw"`
}

// validTimeLimits accepts None or a JSON number for both header values.
func validTimeLimits(limits []any) bool {
	for _, value := range limits {
		if value == nil {
			continue
		}
		n, ok := value.(celery.Number)
		if !ok {
			return false
		}
		if _, err := strconv.ParseFloat(string(n), 64); err != nil {
			return false
		}
	}
	return true
}

// effectiveLimit is `header or default`: the header value when truthy.
func effectiveLimit(header any, fallback celery.Number) (celery.Number, float64) {
	if n, ok := header.(celery.Number); ok {
		if v, _ := strconv.ParseFloat(string(n), 64); v != 0 {
			return n, v
		}
	}
	if fallback == "" {
		return "", 0
	}
	v, _ := strconv.ParseFloat(string(fallback), 64)
	if v == 0 {
		return "", 0
	}
	return fallback, v
}

func seconds(v float64) time.Duration { return time.Duration(v * float64(time.Second)) }

// humanStatus is billiard.common.human_status of the child's exit.
func humanStatus(state *os.ProcessState) string {
	if state != nil {
		if ws, ok := state.Sys().(syscall.WaitStatus); ok && ws.Signaled() {
			if name := unix.SignalName(ws.Signal()); name != "" {
				return fmt.Sprintf("signal %d (%s)", int(ws.Signal()), name)
			}
			return fmt.Sprintf("signal %d", int(ws.Signal()))
		}
		return fmt.Sprintf("exitcode %d", state.ExitCode())
	}
	return "exitcode None"
}

// preforkJob is the parent's view of one child execution.
type preforkJob struct {
	mu    sync.Mutex
	ready bool
}

// settle marks the job ready once; it reports whether this caller did.
func (j *preforkJob) settle() bool {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.ready {
		return false
	}
	j.ready = true
	return true
}

func (w *Worker) executePrefork(ctx context.Context, d *Delivery, task Task, job int64) {
	m := d.Message
	cmd := w.options.Child()
	events, eventsWriter, err := os.Pipe()
	if err != nil {
		w.preforkLost(ctx, d, task, job, nil)
		return
	}
	defer events.Close()
	cmd.ExtraFiles = []*os.File{eventsWriter}
	input, err := json.Marshal(childInput{Raw: d.Raw})
	if err != nil {
		eventsWriter.Close()
		w.preforkLost(ctx, d, task, job, nil)
		return
	}
	stdin, err := cmd.StdinPipe()
	if err == nil {
		err = cmd.Start()
	}
	eventsWriter.Close()
	if err != nil {
		w.logger.Error("celery prefork child failed to start", "code", "celery_child_start_failed")
		w.preforkLost(ctx, d, task, job, nil)
		return
	}
	go func() {
		_, _ = stdin.Write(input)
		stdin.Close()
	}()
	lines := make(chan string, 4)
	go func() {
		defer close(lines)
		scanner := bufio.NewScanner(events)
		for scanner.Scan() {
			lines <- scanner.Text()
		}
	}()
	state := &preforkJob{}
	var timers []*time.Timer
	var timersMu sync.Mutex
	stopTimers := func() {
		timersMu.Lock()
		defer timersMu.Unlock()
		for _, t := range timers {
			t.Stop()
		}
	}
	addTimer := func(after time.Duration, f func()) {
		timersMu.Lock()
		defer timersMu.Unlock()
		timers = append(timers, time.AfterFunc(max(after, 0), f))
	}
	hardValue, hard := effectiveLimit(m.TimeLimit[0], task.TimeLimit)
	_, soft := effectiveLimit(m.TimeLimit[1], task.SoftTimeLimit)
	onHard := func() {
		if !state.settle() {
			return
		}
		var limit any = hardValue
		w.store(ctx, m.ID, "FAILURE", &Exception{Type: "TimeLimitExceeded", Module: "billiard.exceptions", Args: []any{limit}}, nil)
		if task.AcksLate {
			w.ack(ctx, d)
		}
		w.logger.Error("celery hard time limit exceeded", "code", "celery_hard_time_limit", "task", m.Task)
		_ = cmd.Process.Signal(syscall.SIGTERM)
		time.Sleep(100 * time.Millisecond)
		_ = cmd.Process.Signal(syscall.SIGKILL)
	}
	done, handedOff := false, false
	for line := range lines {
		switch line {
		case eventHandoff:
			handedOff = true
		case eventAccepted:
			if !task.AcksLate {
				w.ack(ctx, d)
			}
			switch {
			case soft > 0:
				addTimer(seconds(soft), func() {
					w.logger.Warn("celery soft time limit exceeded", "code", "celery_soft_time_limit", "task", m.Task)
					_ = cmd.Process.Signal(syscall.SIGUSR1)
					if hard > 0 {
						addTimer(seconds(hard-soft), onHard)
					}
				})
			case hard > 0:
				addTimer(seconds(hard), onHard)
			}
		case eventDone:
			done = true
		}
	}
	_ = cmd.Wait()
	stopTimers()
	switch {
	case handedOff:
		return
	case done:
		if state.settle() && task.AcksLate {
			w.ack(ctx, d)
		}
	default:
		if !state.settle() {
			return // the hard time limit handled the job
		}
		w.preforkLost(ctx, d, task, job, cmd.ProcessState)
	}
}

// preforkLost is Request.on_failure with WorkerLostError.
func (w *Worker) preforkLost(ctx context.Context, d *Delivery, task Task, job int64, state *os.ProcessState) {
	if task.AcksLate && task.RejectOnWorkerLost {
		if err := w.transport.Reject(ctx, d.Message.DeliveryTag, true); err != nil {
			w.logger.Error("celery reject failed", "code", "celery_reject_failed")
		}
		return
	}
	if task.AcksLate {
		w.ack(ctx, d)
	}
	w.store(ctx, d.Message.ID, "FAILURE", &Exception{Type: "WorkerLostError", Module: "billiard.exceptions",
		Args: []any{fmt.Sprintf("Worker exited prematurely: %s Job: %d.", humanStatus(state), job)}}, nil)
}

// RunChild executes one prefork job in this process: in is the message the
// parent sent, events the parent's event pipe. SIGUSR1 cancels the body's
// context with ErrSoftTimeLimit; SIGINT is ignored (the parent leads the
// shutdown), SIGTERM keeps its default (the parent's hard time limit).
func (w *Worker) RunChild(ctx context.Context, in io.Reader, events io.Writer) error {
	signal.Ignore(os.Interrupt)
	raw, err := io.ReadAll(io.LimitReader(in, 64<<20))
	if err != nil {
		return err
	}
	var input childInput
	if err = json.Unmarshal(raw, &input); err != nil || len(input.Raw) == 0 {
		return errors.New("celeryworker: invalid child input")
	}
	m, err := celery.ParseMessage(input.Raw)
	if err != nil {
		return errors.New("celeryworker: undecodable child message")
	}
	d := &Delivery{Raw: input.Raw, Message: m}
	task, ok := w.tasks[m.Task]
	if !ok || !task.Prefork {
		return errors.New("celeryworker: child task not registered")
	}
	emit := func(event string) error {
		_, err := io.WriteString(events, event+"\n")
		return err
	}
	request := &Request{Message: m, Hostname: w.options.Hostname}
	if task.Cleanup != nil {
		defer task.Cleanup(request)
	}
	if task.Plan != nil {
		if err := task.Plan(ctx, request); errors.Is(err, ErrHandoff) {
			w.handoff(ctx, d, "input")
			return emit(eventHandoff)
		}
	}
	taskCtx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	soft := make(chan os.Signal, 1)
	signal.Notify(soft, syscall.SIGUSR1)
	defer signal.Stop(soft)
	go func() {
		select {
		case <-soft:
			cancel(ErrSoftTimeLimit)
		case <-taskCtx.Done():
		}
	}()
	if err := emit(eventAccepted); err != nil {
		return err
	}
	w.perform(ctx, taskCtx, request, task, true)
	return emit(eventDone)
}

// ChildCommand is the default Options.Child: this executable with args.
func ChildCommand(args ...string) func() *exec.Cmd {
	return func() *exec.Cmd {
		executable, err := os.Executable()
		if err != nil {
			executable = os.Args[0]
		}
		cmd := exec.Command(executable, args...)
		cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
		return cmd
	}
}
