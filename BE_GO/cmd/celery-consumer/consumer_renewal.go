package main

import (
	"context"
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
)

func renewalDeliveryTask(jobs *repository.Jobs) celeryworker.Task {
	return celeryworker.Task{
		ToNative: func(ctx context.Context, message *celery.Message) (bool, error) {
			id, err := uuid.Parse(message.ID)
			if err != nil || id == uuid.Nil {
				return false, nil
			}
			payload := renewalDeliveryPayload(message)
			if payload == nil {
				return false, nil
			}
			transfer, cancel := context.WithTimeout(ctx, 5*time.Second)
			defer cancel()
			return true, jobs.PromoteRenewalDelivery(transfer, message, payload)
		},
		Run: func(context.Context, *celeryworker.Request) (any, error) {
			return nil, &celeryworker.Exception{Type: "TypeError", Module: "builtins", Args: []any{"invalid renewal delivery"}}
		},
	}
}

func renewalDeliveryPayload(message *celery.Message) json.RawMessage {
	names := [...]string{"attempt", "max_attempts"}
	if len(message.Args) > len(names) {
		return nil
	}
	kwargs := append(celery.Dict{}, message.Kwargs...)
	for i, value := range message.Args {
		if _, duplicate := kwargs.Get(names[i]); duplicate {
			return nil
		}
		kwargs = kwargs.Set(names[i], value)
	}
	encoded, err := celery.EncodeJSON(kwargs)
	if err != nil {
		return nil
	}
	var payload map[string]json.RawMessage
	if json.Unmarshal(encoded, &payload) != nil {
		return nil
	}
	if _, _, err = service.RenewalAttempts(payload); err != nil {
		return nil
	}
	return encoded
}
