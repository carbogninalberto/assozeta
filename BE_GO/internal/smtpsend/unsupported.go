package smtpsend

import "github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"

// ErrUnsupported marks input outside the subset proven against Python; it is
// returned before any connection is made. Only Django's EmailMessage path
// (django_message.go, backend.go) reports it now: send_email's own path ports
// every value a Python str can hold, raises where Python raises (ErrRaises) and
// treats text a str cannot hold as an application state.
var ErrUnsupported = refusal.New("smtpsend: input outside the supported subset")
