package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"reflect"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/agentllm"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
)

// ErrAgentConfigUnavailable is an exception from effective_integration('ai')
// (an undecryptable credential, a database error).
var ErrAgentConfigUnavailable = errors.New("AI configuration unavailable")

// AgentManualAvailability is manual.availability.manual_available().
type AgentManualAvailability interface {
	Available(ctx context.Context) (bool, error)
}

// AgentRuntime composes what AgentConsumer and run_agent read.
type AgentRuntime struct {
	Admin        *InstanceAdmin
	Manual       AgentManualAvailability
	ManualTools  *ManualTools
	Store        *repository.AgentStore
	Business     BusinessMCPStore
	Coordination AgentCoordination
	Environment  AgentEnvironment
	// DefaultIterations and DefaultHistoryCap are MCP_AGENT_MAX_ITERATIONS and MCP_AGENT_HISTORY_CAP.
	DefaultIterations, DefaultHistoryCap int64
	ToolSchemas                          []any
	Now                                  func() time.Time
	// Transport replaces the model endpoint transport (tests).
	Transport func(*agentllm.Config)
}

// AIConfig is effective_integration('ai') (decrypted) with 'source' and 'revision'.
func (r *AgentRuntime) AIConfig(ctx context.Context) (map[string]any, error) {
	row, err := r.Admin.store.Read(ctx)
	if err != nil {
		return nil, ErrAgentConfigUnavailable
	}
	if row == nil {
		row = &domain.InstanceAdminRow{}
	}
	current, err := r.Admin.effective("ai", row, true)
	if errors.Is(err, errInstanceAdminCredential) {
		return nil, ErrAgentConfigUnavailable
	}
	if err != nil {
		return nil, ErrAgentNotPorted
	}
	value := instanceAdminCopy(current.value)
	value["source"] = map[bool]string{true: "instance", false: "environment"}[current.instance]
	value["revision"] = current.revision
	return value, nil
}

// AgentConfigEqual is dict equality of two configurations.
func AgentConfigEqual(left, right map[string]any) bool {
	return reflect.DeepEqual(agentComparable(left), agentComparable(right))
}

func agentComparable(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		out := make(map[string]any, len(typed))
		for key, item := range typed {
			out[key] = agentComparable(item)
		}
		return out
	case []any:
		out := make([]any, len(typed))
		for i, item := range typed {
			out[i] = agentComparable(item)
		}
		return out
	case json.Number:
		if rat, ok := new(big.Rat).SetString(string(typed)); ok {
			return "n:" + rat.RatString()
		}
	case int64:
		return "n:" + big.NewRat(typed, 1).RatString()
	case bool:
		if typed {
			return "n:1"
		}
		return "n:0"
	}
	return value
}

// ManualAvailable is manual_available() (its own errors are False).
func (r *AgentRuntime) ManualAvailable(ctx context.Context) bool {
	available, err := r.Manual.Available(ctx)
	return err == nil && available
}

// Association is User.sport_association: ok=false for the exceptions
// (PermissionDenied for athletes, DoesNotExist, MultipleObjectsReturned).
func (r *AgentRuntime) Association(ctx context.Context, user *domain.Account) (repository.AgentAssociation, bool, error) {
	owner := user.ID
	switch user.Role {
	case domain.RoleAthlete:
		return repository.AgentAssociation{}, false, nil
	case domain.RoleCollaborator:
		if user.ConnectedUserID == nil {
			return repository.AgentAssociation{}, false, ErrAgentNotPorted
		}
		owner = *user.ConnectedUserID
	}
	rows, err := r.Store.Associations(ctx, owner)
	if err != nil {
		return repository.AgentAssociation{}, false, err
	}
	if len(rows) != 1 {
		return repository.AgentAssociation{}, false, nil
	}
	return rows[0], true, nil
}

// Provider is AIProvider(...) when the configuration is enabled, else None.
func (r *AgentRuntime) Provider(config map[string]any) (AgentProvider, error) {
	if !dto.PythonTruthy(config["enabled"]) {
		return nil, nil
	}
	for _, key := range []string{"api_key", "model", "base_url"} {
		if _, present := config[key]; !present {
			return nil, ErrAgentNotPorted // KeyError
		}
	}
	provider, err := NewAgentAIProvider(r.Environment, config["api_key"], config["model"], config["base_url"], r.Transport)
	if errors.Is(err, errAgentProviderKey) {
		return nil, ErrAgentConfigUnavailable
	}
	if err != nil {
		return nil, err
	}
	return provider, nil
}

// BusinessTools are the tool functions bound to one association.
func (r *AgentRuntime) BusinessTools(association string) *BusinessMCPTools {
	now := r.Now
	if now == nil {
		now = time.Now
	}
	return NewBusinessMCPTools(r.Business, association, now)
}

// NewAgent is Agent(...) for one association and user ("" for None).
func (r *AgentRuntime) NewAgent(provider AgentProvider, callback AgentCallback, association repository.AgentAssociation, user string,
	config map[string]any, manualAvailable bool) *Agent {
	tools := &AgentTools{BusinessTools: r.BusinessTools(association.ID), ManualTools: r.ManualTools, Coordinator: r.Coordination}
	return NewAgent(provider, callback, AgentOptions{AssociationID: association.ID, AssociationName: association.Denomination, UserID: user,
		Config: config, DefaultIterations: r.DefaultIterations, DefaultHistoryCap: r.DefaultHistoryCap, ManualAvailable: manualAvailable,
		Tools: tools, ToolSchemas: r.ToolSchemas})
}
