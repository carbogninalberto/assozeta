package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
)

// ConsumerBackupFailuresTask is instance.tasks.notify_backup_failures'
// Celery name (Beat entry notify-backup-failures, every 300 s).
const ConsumerBackupFailuresTask = "instance.backups.notify_failures"

// consumerBackupFailuresWindow is FAILURE_WINDOW_HOURS.
const consumerBackupFailuresWindow = 48 * time.Hour

// ConsumerBackupFailuresStore is the database and Django cache side of
// instance.backups.notify_failures.
type ConsumerBackupFailuresStore interface {
	// BackupFailureOwner is InstanceConfiguration.objects
	// .select_related('primary_association__user').first(); nil without a row.
	BackupFailureOwner(ctx context.Context) (*domain.ConsumerBackupFailureOwner, error)
	// AddNotified is cache.add(FAILURE_NOTIFIED.format(key), True, timeout=7 days).
	AddNotified(ctx context.Context, key string) (bool, error)
	// DeleteNotified is cache.delete(FAILURE_NOTIFIED.format(key)).
	DeleteNotified(ctx context.Context, key string) error
}

// ConsumerBackupFailuresRunner is call_runner without a payload.
type ConsumerBackupFailuresRunner interface {
	Call(ctx context.Context, method, path string) (*updaterrunner.Reply, error)
}

// ConsumerBackupFailuresBarrier is the restore barrier CoordinatedTask
// checks before the body (repository.RestoreStates).
type ConsumerBackupFailuresBarrier interface {
	JobBarrier(ctx context.Context) (bool, *time.Time, error)
}

// ConsumerBackupFailures runs BE/instance/backups.py notify_failures: in
// production, read the runner's /status and /backups, then e-mail the
// primary association's owner once per failed backup, failed scheduled
// start and failed remote copy of the last 48 hours (the cache key marks a
// notice; a failed send releases it). The runner answers are read and checked
// in Plan: an answer Python would read differently, or raise on, is handed to
// the Django worker before any effect.
type ConsumerBackupFailures struct {
	store       ConsumerBackupFailuresStore
	runner      ConsumerBackupFailuresRunner
	barrier     ConsumerBackupFailuresBarrier
	mail        *SendMailAsync
	production  bool
	coordinated bool
	from        string
	now         func() time.Time
	logger      *slog.Logger
}

func NewConsumerBackupFailures(store ConsumerBackupFailuresStore, runner ConsumerBackupFailuresRunner,
	barrier ConsumerBackupFailuresBarrier, mail *SendMailAsync, production, coordinated bool, defaultFromEmail string,
	now func() time.Time, logger *slog.Logger) *ConsumerBackupFailures {
	if logger == nil {
		logger = slog.New(slog.DiscardHandler)
	}
	return &ConsumerBackupFailures{store: store, runner: runner, barrier: barrier, mail: mail, production: production,
		coordinated: coordinated, from: defaultFromEmail, now: now, logger: logger}
}

// Task registers the implementation (a plain shared_task: max_retries 3,
// no retry policy of its own).
func (s *ConsumerBackupFailures) Task() celeryworker.Task {
	return celeryworker.Task{MaxRetries: 3, Plan: s.plan, Run: s.run}
}

// backupFailure is one item _failures yields: (key, when, reason, name). The
// reason (the runner's message, any JSON value) is only logged; name is the
// remote failure's archive name when it is one.
type backupFailure struct {
	key, name string
	reason    any
	when      time.Time
}

// backupFailuresCall is what the plan prepared.
type backupFailuresCall struct {
	// done is a run that returns 0 before the loop (development mode, an
	// unreachable or refusing runner, no owner e-mail).
	done bool
	// deferred is a run the restore barrier refuses: the runner was not read.
	deferred bool
	email    string
	link     string
	failures []backupFailure
	source   sendMailAsyncSource
}

var errConsumerBackupFailuresDatabase = &celeryworker.Exception{Type: "OperationalError", Module: "django.db.utils",
	Args: []any{"database error"}}

func (s *ConsumerBackupFailures) plan(ctx context.Context, request *celeryworker.Request) error {
	if len(request.Args) != 0 || len(request.Kwargs) != 0 {
		return celeryworker.ErrHandoff // TypeError in Django
	}
	call := &backupFailuresCall{}
	if !s.production {
		call.done = true
		request.State = call
		return nil
	}
	if s.coordinated && s.barrier != nil {
		// CoordinatedTask defers or refuses the message before the body:
		// the runner is not read for a message that will not run.
		pending, completed, err := s.barrier.JobBarrier(ctx)
		if err == nil && (pending || completed != nil && consumerBackupFailuresStale(request, *completed)) {
			call.deferred = true
			request.State = call
			return nil
		}
	}
	if err := s.prepare(ctx, call); err != nil {
		return err
	}
	request.State = call
	return nil
}

// consumerBackupFailuresStale is the worker's StaleDataTask condition.
func consumerBackupFailuresStale(request *celeryworker.Request, completed time.Time) bool {
	cutoff := float64(completed.UnixMicro()) / 1e6
	return request.PublishedAt == nil || *request.PublishedAt < cutoff
}

// prepare reads the runner and the owner, checks every answer against the
// ported subset and renders the candidate notices once.
func (s *ConsumerBackupFailures) prepare(ctx context.Context, call *backupFailuresCall) error {
	status, ok, err := s.call(ctx, "/status")
	if err != nil || !ok {
		call.done = !ok && err == nil
		return err
	}
	inventory, ok, err := s.call(ctx, "/backups")
	if err != nil || !ok {
		call.done = !ok && err == nil
		return err
	}
	owner, err := s.store.BackupFailureOwner(ctx)
	if err != nil {
		return err
	}
	if owner == nil || owner.Email == nil || *owner.Email == "" {
		call.done = true
		return nil
	}
	if call.failures, err = consumerBackupFailuresList(status, inventory); err != nil {
		return err
	}
	call.email = *owner.Email
	if owner.Domain != "" {
		call.link = "https://" + owner.Domain + "/#/profile?page=self-instance&tab=backups"
	}
	mail := &sendMailAsyncCall{}
	if err := s.mail.planSource(ctx, mail); err != nil {
		return err
	}
	call.source = mail.source
	for _, failure := range call.failures {
		subject, body := consumerBackupFailuresNotice(failure, call.link)
		if _, err := smtpsend.RenderDjangoMessage(smtpsend.DjangoMessage{Subject: subject, Body: body, From: s.mailFrom(call),
			To: []string{call.email}, Date: time.Unix(0, 0), MessageID: "<0@localhost>", Boundary: "0"}); err != nil {
			return celeryworker.ErrHandoff
		}
	}
	if _, err := smtpsend.DjangoEnvelope(s.mailFrom(call)); err != nil {
		return celeryworker.ErrHandoff
	}
	return nil
}

func (s *ConsumerBackupFailures) mailFrom(call *backupFailuresCall) string {
	if call.source.instance {
		return call.source.from
	}
	return s.from
}

// call is call_runner('GET', path) as notify_failures reads it: ok is false
// for UpdaterUnavailable and UpdaterRejected (the task returns 0); an answer
// Python decodes differently, or reads with an AttributeError, is a handoff.
func (s *ConsumerBackupFailures) call(ctx context.Context, path string) (any, bool, error) {
	reply, err := s.runner.Call(ctx, "GET", path)
	if err != nil {
		if _, unavailable := updaterrunner.IsUnavailable(err); unavailable {
			return nil, false, nil
		}
		return nil, false, celeryworker.ErrHandoff
	}
	value, err := celery.DecodeJSON(reply.Body)
	if err != nil {
		// json.loads raises ValueError (UpdaterUnavailable) where Go's
		// decoder fails too, unless Python accepts what Go refuses (NaN,
		// Infinity, unpaired surrogates, a byte order mark).
		if json.Valid(reply.Body) || bytes.Contains(reply.Body, []byte("NaN")) || bytes.Contains(reply.Body, []byte("Infinity")) ||
			bytes.HasPrefix(reply.Body, []byte("\xef\xbb\xbf")) {
			return nil, false, celeryworker.ErrHandoff
		}
		return nil, false, nil
	}
	if reply.Status >= 400 {
		if _, isDict := value.(celery.Dict); !isDict {
			return nil, false, celeryworker.ErrHandoff // body.get raises AttributeError
		}
		return nil, false, nil // UpdaterRejected
	}
	return value, true, nil
}

// backupFailuresGet is dict.get: the last value of a duplicated JSON key.
func backupFailuresGet(object celery.Dict, key string) (any, bool) {
	var value any
	found := false
	for _, item := range object {
		if item.Key == key {
			value, found = item.Value, true
		}
	}
	return value, found
}

// backupFailuresTruth is bool() of a decoded JSON value.
func backupFailuresTruth(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case celery.Number:
		number, err := strconv.ParseFloat(string(v), 64)
		return err != nil || number != 0
	case []any:
		return len(v) != 0
	case celery.Dict:
		return len(v) != 0
	}
	return true
}

// backupFailuresOr is `a or b` for an object field.
func backupFailuresOr(object celery.Dict, key string, fallback any) any {
	if value, _ := backupFailuresGet(object, key); backupFailuresTruth(value) {
		return value
	}
	return fallback
}

// backupFailuresObject is `value or {}` read with .get afterwards: a truthy
// value that is not a dict raises AttributeError in Python (handoff).
func backupFailuresObject(value any) (celery.Dict, error) {
	if !backupFailuresTruth(value) {
		return nil, nil
	}
	object, ok := value.(celery.Dict)
	if !ok {
		return nil, celeryworker.ErrHandoff
	}
	return object, nil
}

// backupFailuresAware is the subset of parse_datetime this task accepts: the
// runner's datetime.isoformat() of an aware value ('T', seconds, up to six
// fraction digits, 'Z' or a ±HH:MM offset). Other text is a handoff.
var backupFailuresAware = regexp.MustCompile(`^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d{1,6})?(Z|[+-]\d\d:\d\d)$`)

// backupFailuresParse is parse_datetime(value) for `x.get(...) or ”`: nil
// for the empty string, an aware time for the subset.
func backupFailuresParse(value any) (*time.Time, error) {
	text, ok := value.(string)
	if !ok {
		return nil, celeryworker.ErrHandoff // fromisoformat raises TypeError
	}
	if text == "" {
		return nil, nil
	}
	match := backupFailuresAware.FindStringSubmatch(text)
	if match == nil {
		return nil, celeryworker.ErrHandoff
	}
	layout := "2006-01-02T15:04:05"
	if match[7] != "" {
		layout += "." + strings.Repeat("0", len(match[7])-1)
	}
	zone := match[8]
	if zone == "Z" {
		zone = "+00:00"
	} else if zone[1:3] > "23" || zone[4:] > "59" {
		return nil, celeryworker.ErrHandoff
	}
	parsed, err := time.Parse(layout+"-07:00", strings.TrimSuffix(text, match[8])+zone)
	if err != nil || parsed.Year() < 1000 {
		return nil, celeryworker.ErrHandoff
	}
	return &parsed, nil
}

// backupFailuresReason is `x.get('error') or ”` (a string in the subset).
func backupFailuresReason(object celery.Dict) any {
	value := backupFailuresOr(object, "error", "")
	if !backupFailuresTruth(value) {
		return ""
	}
	return value
}

// consumerBackupFailuresList is _failures(status, inventory) before the
// cutoff: every item that matches apart from its age, in yield order. Inputs
// on which Python would raise for some clock are handed off.
func consumerBackupFailuresList(status, inventory any) ([]backupFailure, error) {
	statusObject, ok := status.(celery.Dict)
	inventoryObject, inventoryOK := inventory.(celery.Dict)
	if !ok || !inventoryOK {
		return nil, celeryworker.ErrHandoff // .get on a value that is not a dict
	}
	var out []backupFailure
	history, found := backupFailuresGet(statusObject, "history")
	if !found {
		history = []any{}
	}
	switch v := history.(type) {
	case []any:
		for _, item := range v {
			operation, ok := item.(celery.Dict)
			if !ok {
				return nil, celeryworker.ErrHandoff
			}
			raw := backupFailuresOr(operation, "updated_at", backupFailuresOr(operation, "created_at", ""))
			when, err := backupFailuresParse(raw)
			if err != nil {
				return nil, err
			}
			kind, _ := backupFailuresGet(operation, "kind")
			state, _ := backupFailuresGet(operation, "status")
			if kind != "backup" || state != "failed" || when == nil {
				continue
			}
			id, found := backupFailuresGet(operation, "id")
			key, isText := id.(string)
			if !found || !isText {
				return nil, celeryworker.ErrHandoff // KeyError, or str() of another type
			}
			out = append(out, backupFailure{key: key, when: *when, reason: backupFailuresReason(operation)})
		}
	case string:
		if v != "" {
			return nil, celeryworker.ErrHandoff // characters have no .get
		}
	case celery.Dict:
		if len(v) != 0 {
			return nil, celeryworker.ErrHandoff // keys have no .get
		}
	default:
		return nil, celeryworker.ErrHandoff // not iterable
	}
	sections := []struct {
		section, field, prefix string
		status                 bool
	}{{"schedule", "last_run", "schedule:", true}, {"remote", "last_failure", "remote:", false}}
	for _, section := range sections {
		outer, err := backupFailuresObject(backupFailuresOr(inventoryObject, section.section, nil))
		if err != nil {
			return nil, err
		}
		entry, err := backupFailuresObject(backupFailuresOr(outer, section.field, nil))
		if err != nil {
			return nil, err
		}
		raw := backupFailuresOr(entry, "at", "")
		when, err := backupFailuresParse(raw)
		if err != nil || when == nil {
			if err != nil {
				return nil, err
			}
			continue
		}
		if section.status {
			// A scheduled run that could not start has no operation of its own.
			state, _ := backupFailuresGet(entry, "status")
			operation, _ := backupFailuresGet(entry, "operation")
			if state != "failed" || backupFailuresTruth(operation) {
				continue
			}
		}
		failure := backupFailure{key: section.prefix + raw.(string), when: *when, reason: backupFailuresReason(entry)}
		if !section.status {
			// name if isinstance(name, str) and ARCHIVE.fullmatch(name) else ''
			if name, ok := backupFailuresOr(entry, "name", nil).(string); ok && dto.InstanceBackupActionsArchive.MatchString(name) {
				failure.name = name
			}
		}
		out = append(out, failure)
	}
	return out, nil
}

// consumerBackupFailuresNotice is the subject and body of one notice: the
// time (TIME_ZONE is UTC: timezone.localtime is the UTC time), the archive
// when known and the page; the runner's message is not part of it.
func consumerBackupFailuresNotice(failure backupFailure, link string) (string, string) {
	local := failure.when.UTC().Format("02/01/2006 15:04")
	subject, body := "Backup non riuscito", "Il backup del "+local+" non è riuscito.\n\n"
	if strings.HasPrefix(failure.key, "remote:") {
		name := ""
		if failure.name != "" {
			name = " " + failure.name
		}
		subject, body = "Copia remota non riuscita", "La copia remota del backup"+name+" non è riuscita ("+local+").\n\n"
	}
	suffix := ""
	if link != "" {
		suffix = ": " + link
	}
	return subject, body + "Controlla in Istanza > Backup" + suffix + ".\n"
}

func (s *ConsumerBackupFailures) run(ctx context.Context, request *celeryworker.Request) (any, error) {
	call, ok := request.State.(*backupFailuresCall)
	if !ok {
		return nil, errConsumerBackupFailuresDatabase // the plan's reads failed: the body raises on the same read
	}
	if call.deferred {
		// The barrier cleared between the plan and the admission: read now;
		// an answer outside the subset can no longer be handed off.
		call.deferred = false
		if err := s.prepare(ctx, call); err != nil {
			if errors.Is(err, celeryworker.ErrHandoff) {
				return nil, &celeryworker.Exception{Type: "ValueError", Module: "builtins",
					Args: []any{"runner answer outside the ported subset"}}
			}
			return nil, errConsumerBackupFailuresDatabase
		}
	}
	if call.done {
		return int64(0), nil
	}
	cutoff := s.now().Add(-consumerBackupFailuresWindow)
	sent := int64(0)
	for _, failure := range call.failures {
		if failure.when.Before(cutoff) {
			continue
		}
		added, err := s.store.AddNotified(ctx, failure.key)
		if err != nil {
			return nil, err
		}
		if !added {
			continue
		}
		s.logger.Warn("Backup failure", "key", failure.key, "reason", fmt.Sprint(failure.reason))
		subject, body := consumerBackupFailuresNotice(failure, call.link)
		mail := &sendMailAsyncCall{subject: subject, message: body, from: s.from, recipients: []string{call.email}, source: call.source}
		if exc := s.mail.send(ctx, mail); exc != nil {
			if err := s.store.DeleteNotified(ctx, failure.key); err != nil {
				return nil, err
			}
			s.logger.Warn("Could not send the backup failure email", "exception", exc.Module+"."+exc.Type)
			continue
		}
		sent++
	}
	return sent, nil
}
