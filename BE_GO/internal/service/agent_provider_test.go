package service

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/agentllm"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// A base URL httpx rejects makes AsyncOpenAI(...) raise while the provider is
// built: the consumer answers it (close 4004, or the configuration error frame
// on a refresh), so it must not reach the refusal path.
func TestAgentProviderInvalidBaseURLIsAnswered(t *testing.T) {
	env := AgentEnvironment{}
	_, err := NewAgentAIProvider(env, "chiave", "modello", "http://127.0.0.1:abc/v1", nil)
	var invalid *agentllm.InvalidURLError
	if !errors.As(err, &invalid) || invalid.Text != "Invalid port: 'abc'" {
		t.Fatalf("expected httpx's InvalidURL, got %v", err)
	}
	if errors.Is(err, refusal.ErrNotPorted) || errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("an invalid base URL is answered by the consumer, got %v", err)
	}
	runtime := &AgentRuntime{}
	_, err = runtime.Provider(map[string]any{"enabled": true, "api_key": "chiave", "model": "modello",
		"base_url": "http://127.0.0.1:abc/v1"})
	if err == nil || errors.Is(err, refusal.ErrNotPorted) || errors.Is(err, ErrAgentConfigUnavailable) {
		t.Fatalf("Provider: expected the plain construction error, got %v", err)
	}
}
