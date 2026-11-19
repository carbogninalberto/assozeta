package celeryworker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/redis/go-redis/v9"
)

// The worker uses Redis transactions for ack, retry and handoff. Build the
// commands in memory and observe which queue actually received a message.
type taskLogBroker struct {
	redis.UniversalClient
	queues []string
}

func (b *taskLogBroker) TxPipelined(ctx context.Context, fn func(redis.Pipeliner) error) ([]redis.Cmder, error) {
	client := redis.NewClient(&redis.Options{})
	defer client.Close()
	pipe := client.Pipeline()
	if err := fn(pipe); err != nil {
		return nil, err
	}
	for _, cmd := range pipe.Cmds() {
		if cmd.Name() == "lpush" {
			b.queues = append(b.queues, cmd.Args()[1].(string))
		}
	}
	return nil, nil
}

type taskLogResults struct {
	statuses []string
	err      error
}

func (r *taskLogResults) StoreResult(_ context.Context, _, status, _, _ string) error {
	r.statuses = append(r.statuses, status)
	return r.err
}

func TestTaskCompletionLog(t *testing.T) {
	exc := &Exception{Type: "ValueError", Module: "builtins", Args: []any{"exception-canary"}}
	for _, tc := range []struct {
		name, outcome, status, queue string
		runErr                       error
	}{
		{"ok", "success", "SUCCESS", "", nil},
		{"store-error", "failure", "SUCCESS", "", nil},
		{"send_email_text", "handed_off", "", celery.DefaultQueue, nil},
		{"fails", "failure", "FAILURE", "", exc},
		{"again", "retry", "RETRY", celery.GoQueue, &Retry{Exc: exc, Countdown: 1}},
		{"unregistered", "handed_off", "", celery.DefaultQueue, nil},
		{"refused", "handed_off", "", celery.DefaultQueue, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var output bytes.Buffer
			broker := &taskLogBroker{}
			results := &taskLogResults{}
			if tc.name == "store-error" {
				results.err = errors.New("result-backend-canary")
			}
			tasks := map[string]Task{}
			if tc.name != "unregistered" {
				task := Task{MaxRetries: 1, Run: func(context.Context, *Request) (any, error) {
					time.Sleep(3 * time.Millisecond)
					return "result-canary", tc.runErr
				}}
				if tc.name == "refused" {
					task.Plan = func(context.Context, *Request) error { return ErrHandoff }
				}
				tasks[tc.name] = task
			}
			transport := &celery.Transport{Queue: celery.GoQueue, Client: broker}
			w, err := New(transport, tasks, results, nil, Options{Concurrency: 1, HandoffQueue: celery.DefaultQueue},
				slog.New(slog.NewJSONHandler(&output, nil)))
			if err != nil {
				t.Fatal(err)
			}
			id := tc.name + "-id"
			kwargs := celery.Dict{{Key: "option", Value: "keyword-canary"}}
			if tc.name == "send_email_text" {
				// A protocol 2 envelope with a Kombu datetime still identifies the task.
				kwargs[0].Value = celery.Dict{{Key: "__type__", Value: "datetime"},
					{Key: "__value__", Value: "2026-01-02T03:04:00+00:00"}}
			}
			raw, err := celery.Encode(celery.Task{Name: tc.name, Queue: celery.GoQueue,
				Args: []any{"argument-canary"}, Kwargs: kwargs},
				celery.Envelope{ID: id, DeliveryTag: "delivery-tag", ReplyTo: "reply", Origin: "gen1@localhost"})
			if err != nil {
				t.Fatal(err)
			}
			msg, err := celery.ParseMessage(raw)
			if tc.name == "send_email_text" {
				// Exercise transport decode failure independently of the decoder's supported types.
				msg, err = nil, celery.ErrUndecodable
			} else if err != nil {
				t.Fatal(err)
			}
			d := &Delivery{Raw: raw, Message: msg, Err: err}
			if w.receive(t.Context(), d) {
				w.execute(t.Context(), d)
			}
			if strings.Join(results.statuses, ",") != tc.status || strings.Join(broker.queues, ",") != tc.queue {
				t.Fatalf("stored %v and published to %v", results.statuses, broker.queues)
			}
			if strings.Contains(output.String(), "-canary") {
				t.Fatal("task data in log")
			}
			var done []map[string]any
			for _, line := range strings.Split(strings.TrimSpace(output.String()), "\n") {
				if line == "" {
					continue
				}
				var record map[string]any
				if err := json.Unmarshal([]byte(line), &record); err != nil {
					t.Fatal(err)
				}
				if record["msg"] == "celery task done" {
					done = append(done, record)
				}
			}
			if len(done) != 1 {
				t.Fatalf("got %d completion records, want 1", len(done))
			}
			record := done[0]
			duration, ok := record["duration_ms"].(float64)
			if len(record) != 7 || record["time"] == nil || record["level"] != "INFO" ||
				record["task"] != tc.name || record["id"] != id || record["outcome"] != tc.outcome ||
				!ok || duration < 0 || (tc.status != "" && duration < 2) {
				t.Errorf("unexpected completion record: %v", record)
			}
		})
	}
}
