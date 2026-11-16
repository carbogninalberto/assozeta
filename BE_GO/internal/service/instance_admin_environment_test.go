package service

import (
	"errors"
	"testing"
)

// int(settings.EMAIL_PORT) raising ValueError is an unhandled exception (the 500 page), not a request Go hands off.
func TestEnvironmentEmailWithInvalidPortIsAnUnhandledError(t *testing.T) {
	admin := &InstanceAdmin{env: InstanceAdminEnvironment{EmailPortInvalid: true}}
	_, err := admin.environmentEmail(0)
	if !errors.Is(err, errInstanceAdminEnvironmentPort) {
		t.Fatalf("got %v", err)
	}
}
