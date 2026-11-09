package service

import (
	"context"
	"errors"
	"io"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// AgentRestoreLock is the restore OS lock (repository.RestoreFileLock).
type AgentRestoreLock interface {
	Acquire(ctx context.Context, exclusive, required bool) (io.Closer, error)
}

// AgentRestoreStates is restore_pending().
type AgentRestoreStates interface {
	Pending(ctx context.Context) (bool, error)
}

// AgentCoordination is instance.restore.locking's coordinated_sync and
// coordinated_async for the agent: a shared lease, then restore_pending()
// when DATA_RESTORE_LOCK_PATH is configured.
type AgentCoordination struct {
	Lock       AgentRestoreLock
	States     AgentRestoreStates
	Configured bool
}

const agentRestoreBusy = "Ripristino dei dati in corso. Riprova al termine."

// Acquire returns the lease's release, or the Busy message.
func (c AgentCoordination) Acquire(ctx context.Context) (func(), string, error) {
	lease, err := c.Lock.Acquire(ctx, false, false)
	switch {
	case errors.Is(err, domain.ErrRestoreBusy), errors.Is(err, domain.ErrRestoreVolumeUnavailable):
		return nil, err.Error(), nil
	case err != nil:
		return nil, "", err
	}
	release := func() { _ = lease.Close() }
	if c.Configured {
		pending, err := c.States.Pending(ctx)
		if err != nil {
			release()
			return nil, "", err
		}
		if pending {
			release()
			return nil, agentRestoreBusy, nil
		}
	}
	return release, "", nil
}

// AgentTools runs the tool functions of one association for the agent.
type AgentTools struct {
	BusinessTools *BusinessMCPTools
	ManualTools   *ManualTools
	Coordinator   AgentCoordination
}

func (t *AgentTools) Coordinate(ctx context.Context) (func(), string, error) {
	return t.Coordinator.Acquire(ctx)
}

func (t *AgentTools) Business(ctx context.Context, name string, arguments domain.RequestObject) (any, bool, error) {
	raised := false
	value, err := t.BusinessTools.Execute(WithMCPRaised(ctx, &raised), name, arguments)
	if errors.Is(err, ErrBusinessMCPNotPorted) {
		return nil, false, ErrAgentNotPorted
	}
	return value, raised, err
}

func (t *AgentTools) Manual(ctx context.Context, name string, arguments domain.RequestObject) (any, error) {
	plain := map[string]any{}
	for _, field := range arguments {
		plain[field.Name] = agentPlain(field.Value)
	}
	// _context(sport_association_id, user_id): the tool's own user_id argument
	// is the agent's identity (None for the CLI agent).
	user, _ := plain["user_id"].(string)
	association, _ := plain["sport_association_id"].(string)
	result, err := t.ManualTools.ExecuteForUser(ctx, user, association, name, plain)
	if err != nil {
		return nil, err
	}
	return agentManualOrdered(name, result)
}

// agentPlain converts ordered values to the plain JSON values the manual
// package reads.
func agentPlain(value any) any {
	switch typed := value.(type) {
	case domain.RequestObject:
		result := make(map[string]any, len(typed))
		for _, field := range typed {
			result[field.Name] = agentPlain(field.Value)
		}
		return result
	case []any:
		result := make([]any, len(typed))
		for i, item := range typed {
			result[i] = agentPlain(item)
		}
		return result
	}
	return value
}

// AgentUUID is UUIDField.to_python for a string: the canonical text, or
// false where Django raises ValidationError.
func AgentUUID(value string) (string, bool) {
	id, message, err := userUUID(value)
	return id, err == nil && message == ""
}
