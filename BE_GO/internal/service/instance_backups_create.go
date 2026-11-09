package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
)

// instanceBackupsOutdatedAction is relay()'s answer when the runner does not
// know '/backups' or '/backups/schedule' (404).
const instanceBackupsOutdatedAction = "Il servizio di aggiornamento richiede una versione più recente."

// InstanceBackupsRelayed is relay(method, path, payload, success): Reply is
// the response, and Value the runner's decoded body when the call succeeded
// (Reply.Status is then the success status).
type InstanceBackupsRelayed struct {
	Reply *InstanceBackupsReply
	Value any
}

// Relay is backups.relay(): call_runner(method, path, payload) answered with
// the success status, or the runner's refusal as {error} (404 on '/backups'
// and '/backups/schedule' is 409, other 4xx keep their status, the rest 503).
func (s *InstanceBackups) Relay(ctx context.Context, method, path string, payload domain.RequestObject, success int) (*InstanceBackupsRelayed, error) {
	var body []byte
	if payload != nil {
		body = dto.PythonJSONDumps(payload)
	}
	reply, err := s.runner.CallPayload(ctx, method, path, body)
	if message, ok := updaterrunner.IsUnavailable(err); ok {
		return &InstanceBackupsRelayed{Reply: instanceBackupsError(503, message)}, nil
	}
	if err != nil {
		return nil, err
	}
	value, decoded, err := instanceBackupsJSON(reply.Body)
	if err != nil {
		return nil, err
	}
	if !decoded {
		return &InstanceBackupsRelayed{Reply: instanceBackupsError(503, updaterrunner.UnreachableText)}, nil
	}
	if reply.Status >= 400 {
		refusal, ok := value.(domain.RequestObject)
		if !ok {
			return nil, errInstanceBackupsUnhandled // body.get: AttributeError
		}
		message, found := refusal.Lookup("error")
		if !found {
			message = "Aggiornamento non disponibile."
		}
		switch {
		case reply.Status == 404 && (path == "/backups" || path == "/backups/schedule"):
			return &InstanceBackupsRelayed{Reply: instanceBackupsError(409, instanceBackupsOutdatedAction)}, nil
		case reply.Status < 500:
			return &InstanceBackupsRelayed{Reply: instanceBackupsError(reply.Status, dto.PythonStr(message))}, nil
		}
		return &InstanceBackupsRelayed{Reply: instanceBackupsError(503, dto.PythonStr(message))}, nil
	}
	return &InstanceBackupsRelayed{Reply: &InstanceBackupsReply{Status: success, Data: value, Relayed: true}, Value: value}, nil
}

// Create is BackupOverviewView.post: relay a manual backup, then on 202
// audit(actor, 'backup_create', response.data['operation'].get('id', empty string)).
// The entry is written before the response is rendered.
func (s *InstanceBackups) Create(ctx context.Context, actor string, audit domain.LoginAuditContext) (*InstanceBackupsReply, error) {
	payload := domain.RequestObject{{Name: "request_id", Value: instanceBackupsUUID(s.newID())}, {Name: "actor_id", Value: actor}}
	relayed, err := s.Relay(ctx, "POST", "/backups", payload, 202)
	if err != nil {
		return nil, err
	}
	if relayed.Reply.Status != 202 {
		return relayed.Reply, nil
	}
	data, ok := relayed.Value.(domain.RequestObject)
	if !ok {
		return nil, errInstanceBackupsUnhandled // response.data['operation'] on a non-dict: TypeError
	}
	operationValue, found := data.Lookup("operation")
	if !found {
		return nil, errInstanceBackupsUnhandled // KeyError
	}
	operation, ok := operationValue.(domain.RequestObject)
	if !ok {
		return nil, errInstanceBackupsUnhandled // .get on a non-dict: AttributeError
	}
	id, found := operation.Lookup("id")
	if !found {
		id = ""
	}
	if err := s.instanceBackupsAudit(ctx, actor, "backup_create", dto.PythonStr(id), nil, audit); err != nil {
		return nil, err
	}
	if !dto.AuditFiniteJSON(relayed.Value) {
		return nil, errInstanceBackupsUnhandled // strict JSONRenderer: ValueError after the entry is written
	}
	return relayed.Reply, nil
}

// instanceBackupsAudit is backups.audit(actor, action, value, **extra): an
// UPDATE entry on InstanceConfiguration.get_config().
func (s *InstanceBackups) instanceBackupsAudit(ctx context.Context, actor, change, value string, extra map[string]any,
	audit domain.LoginAuditContext) error {
	target, err := s.store.LogTarget(ctx)
	if err != nil {
		return err
	}
	if target == nil {
		return errInstanceBackupsUnhandled // log_create(None): no content type
	}
	return s.store.AppendAudit(ctx, repository.InstanceBackupsAuditEntry{Target: *target, Action: 1, Change: change, Value: value,
		ActorID: actor, Extra: extra, Audit: audit, At: s.now()})
}

// instanceBackupsUUID is str(uuid4()) from the 32 hex digits newID gives.
func instanceBackupsUUID(hex string) string {
	if len(hex) != 32 {
		return hex
	}
	return hex[:8] + "-" + hex[8:12] + "-" + hex[12:16] + "-" + hex[16:20] + "-" + hex[20:]
}
