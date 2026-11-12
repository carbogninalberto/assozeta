package service

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
)

// InstanceUpdatesRunner is updater_client.call_runner.
type InstanceUpdatesRunner interface {
	Call(ctx context.Context, method, path string) (*updaterrunner.Reply, error)
	CallPayload(ctx context.Context, method, path string, payload []byte) (*updaterrunner.Reply, error)
}

// InstanceReleaseFetcher is release_catalog.fetch_release.
type InstanceReleaseFetcher interface {
	FetchRelease(ctx context.Context, id int64) (*instanceprobe.Release, error)
}

// WithUpdates returns the service with the production update and restart
// paths (the update runner and the release catalog) enabled.
func (s *InstanceAdmin) WithUpdates(runner InstanceUpdatesRunner, releases InstanceReleaseFetcher) *InstanceAdmin {
	copied := *s
	copied.runner, copied.releases = runner, releases
	return &copied
}

const (
	instanceUpdatesCompatible = "Il servizio di aggiornamento richiede una versione compatibile."
	instanceUpdatesDefault    = "Aggiornamento non disponibile."
)

// instanceRunnerRejected is UpdaterRejected(message, status).
type instanceRunnerRejected struct {
	message string
	status  int
}

func (e *instanceRunnerRejected) Error() string { return e.message }

// errInstanceUpdatesUnhandled is an exception the views let escape (a 500 page).
var errInstanceUpdatesUnhandled = errors.New("instance updates unhandled exception")

// callRunner is call_runner(method, path, payload): the decoded JSON value,
// updaterrunner.Unavailable, or *instanceRunnerRejected for an answer >= 400.
func (s *InstanceAdmin) callRunner(ctx context.Context, method, path string, payload any) (any, error) {
	var reply *updaterrunner.Reply
	var err error
	if payload == nil {
		reply, err = s.runner.Call(ctx, method, path)
	} else {
		reply, err = s.runner.CallPayload(ctx, method, path, dto.PythonJSONDumps(payload))
	}
	if err != nil {
		return nil, err
	}
	value, decoded, err := instanceBackupsJSON(reply.Body)
	if errors.Is(err, ErrInstanceBackupsNotPorted) {
		return nil, domain.ErrInstanceAdminRefused
	}
	if err != nil {
		return nil, err
	}
	if !decoded {
		return nil, &updaterrunner.Unavailable{Message: updaterrunner.UnreachableText}
	}
	if reply.Status >= 400 {
		body, ok := value.(domain.RequestObject)
		if !ok {
			return nil, errInstanceUpdatesUnhandled // body.get: AttributeError
		}
		message, found := body.Lookup("error")
		if !found {
			message = instanceUpdatesDefault
		}
		return nil, &instanceRunnerRejected{message: dto.PythonStr(message), status: reply.Status}
	}
	return value, nil
}

// runnerFailure maps the exceptions the views catch: UpdaterUnavailable as
// unavailable(message), UpdaterRejected as {'error': message} with its status.
func runnerFailure(err error, unavailable func(string) error) error {
	if message, ok := updaterrunner.IsUnavailable(err); ok {
		return unavailable(message)
	}
	var rejected *instanceRunnerRejected
	if errors.As(err, &rejected) {
		if rejected.status > 599 {
			return errInstanceUpdatesUnhandled // HttpResponse: status out of range
		}
		return &InstanceAdminResponse{Status: rejected.status, Body: map[string]any{"error": rejected.message}}
	}
	return err
}

func instanceUpdatesError(message string) error {
	return &InstanceAdminResponse{Status: 503, Body: map[string]any{"error": message}}
}

// relayed is Response(value, status) for a decoded runner value.
func relayed(status int, value any) error {
	return &InstanceAdminResponse{Status: status, Body: json.RawMessage(dto.PythonJSONDumpsCompact(value))}
}

// UpdatesStatus is InstanceUpdatesView.get.
func (s *InstanceAdmin) UpdatesStatus(ctx context.Context) (any, error) {
	if !s.production() || s.runner == nil {
		return s.Updates()
	}
	value, err := s.callRunner(ctx, "GET", "/status", nil)
	if err != nil {
		return nil, runnerFailure(err, func(message string) error {
			return &InstanceAdminResponse{Status: 200, Body: map[string]any{"available": false, "reason": message, "active": nil,
				"history": []any{}}}
		})
	}
	status, ok := value.(domain.RequestObject)
	if !ok {
		return nil, errInstanceUpdatesUnhandled // result.get: AttributeError
	}
	protocol, _ := status.Lookup("protocol")
	if !dto.PythonEqual(protocol, json.Number("1")) {
		return map[string]any{"available": false, "reason": instanceUpdatesCompatible, "active": nil, "history": []any{}}, nil
	}
	return json.RawMessage(dto.PythonJSONDumpsCompact(value)), nil
}

// textOr is `value or default` for the runner's reason texts.
func textOr(value any, fallback string) (string, error) {
	if !dto.PythonTruthy(value) {
		return fallback, nil
	}
	text, ok := value.(string)
	if !ok {
		return "", domain.ErrInstanceAdminRefused // str() of another JSON value
	}
	return text, nil
}

// RequestUpdateFor is InstanceUpdatesView.post for the authenticated actor.
func (s *InstanceAdmin) RequestUpdateFor(ctx context.Context, actor string, body any) error {
	if !s.production() || s.runner == nil {
		return s.RequestUpdate(body)
	}
	update, err := dto.InstanceAdminUpdateValues(body)
	if err != nil {
		return err
	}
	if !update.ReleaseID.IsInt64() {
		return domain.ErrInstanceAdminRefused
	}
	releaseID := update.ReleaseID.Int64()
	failure := func(err error) error { return runnerFailure(err, instanceUpdatesError) }
	value, err := s.callRunner(ctx, "GET", "/status", nil)
	if err != nil {
		return failure(err)
	}
	status, ok := value.(domain.RequestObject)
	if !ok {
		return errInstanceUpdatesUnhandled
	}
	protocol, _ := status.Lookup("protocol")
	if !dto.PythonEqual(protocol, json.Number("1")) {
		return instanceUpdatesError(instanceUpdatesCompatible)
	}
	var records []any
	if active, _ := status.Lookup("active"); dto.PythonTruthy(active) {
		records = append(records, active)
	}
	history, present := status.Lookup("history")
	if present {
		list, ok := history.([]any)
		if !ok {
			return errInstanceUpdatesUnhandled // list + non-list: TypeError
		}
		records = append(records, list...)
	}
	for _, record := range records {
		operation, ok := record.(domain.RequestObject)
		if !ok {
			return errInstanceUpdatesUnhandled // operation.get: AttributeError
		}
		requestID, _ := operation.Lookup("request_id")
		if !dto.PythonEqual(requestID, update.RequestID) {
			continue
		}
		actorID, _ := operation.Lookup("actor_id")
		release, _ := operation.Lookup("release_id")
		tag, _ := operation.Lookup("tag")
		if !dto.PythonEqual(actorID, actor) || !dto.PythonEqual(release, json.Number(update.ReleaseID.String())) ||
			!dto.PythonEqual(tag, update.Tag) {
			return failure(&instanceRunnerRejected{message: "Questo identificativo è già stato usato per un altro aggiornamento.", status: 409})
		}
		return relayed(202, domain.RequestObject{{Name: "operation", Value: operation}})
	}
	available, _ := status.Lookup("available")
	if !dto.PythonTruthy(available) {
		reason, _ := status.Lookup("reason")
		message, err := textOr(reason, "Il servizio di aggiornamento non è disponibile.")
		if err != nil {
			return err
		}
		return instanceUpdatesError(message)
	}
	if canUpdate, _ := status.Lookup("can_update"); canUpdate == false {
		reason, _ := status.Lookup("reason")
		message, err := textOr(reason, "La transazione precedente richiede un ripristino.")
		if err != nil {
			return err
		}
		return failure(&instanceRunnerRejected{message: message, status: 409})
	}
	release, err := s.releases.FetchRelease(ctx, releaseID)
	var releaseError *instanceprobe.ReleaseError
	switch {
	case errors.As(err, &releaseError):
		return instanceUpdatesError(releaseError.Message)
	case errors.Is(err, instanceprobe.ErrReleaseNotPorted), errors.Is(err, instanceprobe.ErrNotPorted):
		return domain.ErrInstanceAdminRefused
	case err != nil:
		return err
	}
	if release.Tag != update.Tag || !release.ArtifactsReady {
		return failure(&instanceRunnerRejected{message: "La release selezionata non è pronta per l’aggiornamento.", status: 409})
	}
	if atMost, ok := instanceprobe.VersionAtMost(release.Tag, s.env.Version); !ok || atMost {
		return failure(&instanceRunnerRejected{message: "La versione installata non consente questo aggiornamento.", status: 409})
	}
	result, err := s.callRunner(ctx, "POST", "/updates", domain.RequestObject{
		{Name: "release_id", Value: json.Number(update.ReleaseID.String())}, {Name: "tag", Value: release.Tag},
		{Name: "request_id", Value: update.RequestID}, {Name: "actor_id", Value: actor}, {Name: "source_version", Value: s.env.Version}})
	if err != nil {
		return failure(err)
	}
	return relayed(202, result)
}

// RequestRestartFor is InstanceRestartsView.post for the authenticated actor.
func (s *InstanceAdmin) RequestRestartFor(ctx context.Context, actor string, body any) error {
	if !s.production() || s.runner == nil {
		return s.RequestRestart(body)
	}
	only, err := dto.InstanceAdminKeysEqual(body, "request_id")
	if err != nil {
		return err
	}
	if !only {
		return &dto.InstanceAdminInvalid{Body: []string{"È richiesto soltanto l’identificativo della richiesta."}}
	}
	requestID, err := dto.InstanceAdminRestartValue(body)
	if err != nil {
		return err
	}
	result, err := s.callRunner(ctx, "POST", "/restarts", domain.RequestObject{{Name: "request_id", Value: requestID},
		{Name: "actor_id", Value: actor}})
	if err != nil {
		return runnerFailure(err, instanceUpdatesError)
	}
	return relayed(202, result)
}
