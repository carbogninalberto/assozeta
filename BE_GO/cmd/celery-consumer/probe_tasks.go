//go:build consumerprobe

// Probe tasks for the prefork capability differential
// (TESTS/adapters/go/celery_consumer_capabilities_process.py), mirroring
// TESTS/adapters/go/consumer_restore_probe_tasks.py. Only builds with
// -tags consumerprobe register them.
package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
)

func init() {
	extraTasks = append(extraTasks, func(tasks map[string]celeryworker.Task) {
		tasks["consumer_restore.probe"] = probeTask(celeryworker.Task{MaxRetries: 2, AcksLate: true, RejectOnWorkerLost: true})
		tasks["consumer_restore.probe_lost"] = probeTask(celeryworker.Task{MaxRetries: 3, AcksLate: true})
		tasks["consumer_restore.probe_early"] = probeTask(celeryworker.Task{MaxRetries: 3})
	})
}

type probeInput struct {
	mode  string
	value any
}

func probeTask(task celeryworker.Task) celeryworker.Task {
	task.Prefork, task.SoftTimeLimit, task.TimeLimit = true, "1", "3"
	task.Plan = func(_ context.Context, r *celeryworker.Request) error {
		if len(r.Args) != 1 || len(r.Kwargs) > 1 {
			return celeryworker.ErrHandoff
		}
		mode, ok := r.Args[0].(string)
		if !ok {
			return celeryworker.ErrHandoff
		}
		input := probeInput{mode: mode}
		for _, item := range r.Kwargs {
			if item.Key != "value" {
				return celeryworker.ErrHandoff
			}
			input.value = item.Value
		}
		r.State = input
		return nil
	}
	task.Run = func(ctx context.Context, r *celeryworker.Request) (any, error) {
		input := r.State.(probeInput)
		sleep := func(d time.Duration, c context.Context) error {
			select {
			case <-time.After(d):
				return nil
			case <-c.Done():
				return context.Cause(c)
			}
		}
		switch input.mode {
		case "gate":
			name, _ := input.value.(string)
			dir := os.Getenv("CONSUMER_PROBE_DIR")
			if dir == "" || name == "" {
				return nil, errors.New("probe gate without a directory")
			}
			if err := os.WriteFile(filepath.Join(dir, "started-"+name), []byte(strconv.Itoa(os.Getpid())), 0o600); err != nil {
				return nil, err
			}
			deadline := time.Now().Add(30 * time.Second)
			for {
				if _, err := os.Stat(filepath.Join(dir, "release-"+name)); err == nil {
					return name, nil
				}
				if time.Now().After(deadline) {
					return nil, &celeryworker.Exception{Type: "RuntimeError", Module: "builtins", Args: []any{"probe gate timed out"}}
				}
				time.Sleep(50 * time.Millisecond)
			}
		case "value":
			return input.value, nil
		case "fail":
			return nil, &celeryworker.Exception{Type: "ValueError", Module: "builtins", Args: []any{"probe failure", input.value}}
		case "retry":
			return nil, &celeryworker.Retry{Exc: &celeryworker.Exception{Type: "ValueError", Module: "builtins",
				Args: []any{"probe retry"}}, Countdown: 600}
		case "kill":
			_ = syscall.Kill(os.Getpid(), syscall.SIGKILL)
			time.Sleep(time.Hour)
		case "kill-once":
			if !r.Redelivered() {
				_ = syscall.Kill(os.Getpid(), syscall.SIGKILL)
				time.Sleep(time.Hour)
			}
			return "redelivered", nil
		case "soft-catch":
			if err := sleep(20*time.Second, ctx); err != nil {
				return "soft", nil
			}
			return "slept", nil
		case "soft-raise":
			if err := sleep(20*time.Second, ctx); err != nil {
				return nil, err
			}
			return "slept", nil
		case "hard":
			if err := sleep(20*time.Second, ctx); err != nil {
				_ = sleep(20*time.Second, context.Background())
			}
			return "slept", nil
		}
		return nil, &celeryworker.Exception{Type: "KeyError", Module: "builtins", Args: []any{input.mode}}
	}
	return task
}
