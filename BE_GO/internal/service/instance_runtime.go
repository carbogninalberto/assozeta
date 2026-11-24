package service

import (
	"context"
	"encoding/json"
	"math/big"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
)

// Runtime responses retain the runner status and body, including conflict details.
func (s *InstanceBackupActions) runtimeRelay(ctx context.Context, method, path string, payload []byte) (*BackupActionsReply, error) {
	raw, err := s.runner.CallJSON(ctx, method, path, payload, backupActionsSlow)
	if message, ok := updaterrunner.IsUnavailable(err); ok {
		return backupActionsError(503, message), nil
	}
	if err != nil {
		return nil, err
	}
	value, decoded, err := instanceBackupsJSON(raw.Body)
	if err != nil {
		return nil, err
	}
	if !decoded {
		return backupActionsError(503, updaterrunner.UnreachableText), nil
	}
	return backupActionsRendered(&BackupActionsReply{Status: raw.Status, Body: value})
}

func (s *InstanceBackupActions) RuntimeStatus(ctx context.Context) (*BackupActionsReply, error) {
	return s.runtimeRelay(ctx, http.MethodGet, "/runtime", nil)
}

func (s *InstanceBackupActions) RuntimeSwitch(ctx context.Context, request BackupActionsRequest, body any) (*BackupActionsReply, error) {
	object, ok := body.(domain.RequestObject)
	if !ok || len(object) != 4 {
		return backupActionsError(400, backupActionsBadRequest), nil
	}
	targetValue, _ := object.Lookup("target")
	target, _ := targetValue.(string)
	keyValue, _ := object.Lookup("idempotency_key")
	keyText, _ := keyValue.(string)
	key, validKey := dto.PythonUUIDLookup(keyText)
	generationValue, _ := object.Lookup("expected_generation")
	generation, _ := generationValue.(json.Number)
	number, integer := new(big.Int).SetString(string(generation), 10)
	_, hasPassword := object.Lookup("password")
	if (target != "django" && target != "go") || !validKey || !integer || number.Sign() <= 0 || !hasPassword {
		return backupActionsError(400, backupActionsBadRequest), nil
	}
	if refusal, err := s.passwordConfirmation(ctx, request.Actor, body); refusal != nil || err != nil {
		return refusal, err
	}
	payload := domain.RequestObject{{Name: "request_id", Value: key}, {Name: "actor_id", Value: request.Actor},
		{Name: "target", Value: target}, {Name: "expected_generation", Value: generation}}
	reply, err := s.runtimeRelay(ctx, http.MethodPost, "/runtime-switches", dto.PythonJSONDumps(payload))
	if err != nil || reply.Status != http.StatusAccepted {
		return reply, err
	}
	response, ok := reply.Body.(domain.RequestObject)
	if !ok {
		return nil, errInstanceBackupsUnhandled
	}
	replayed, _ := response.Lookup("replayed")
	if replayed == false {
		value, _ := response.Lookup("operation")
		operation, _ := value.(domain.RequestObject)
		previousValue, _ := operation.Lookup("previous_runtime")
		previous, valid := previousValue.(string)
		id, found := operation.Lookup("id")
		if !valid || !found {
			return nil, errInstanceBackupsUnhandled
		}
		err = s.auditChange(ctx, request, "runtime_switch", previous, target, domain.RequestObject{
			{Name: "operation", Value: id}, {Name: "expected_generation", Value: generation}})
	}
	return reply, err
}
