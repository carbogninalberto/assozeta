package domain

import "github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"

// ErrServerComposition is a service dependency or transaction kind the server
// always wires (cmd/server: the With* options and the paid-flow store), so
// no request reaches it in the running server. It is an application-state
// error: the 500 page, never a handoff.
var ErrServerComposition = refusal.AppState("server composition: dependency not wired")
