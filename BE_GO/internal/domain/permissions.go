package domain

// PermissionSubject is the trusted effective person's collaborator state after
// identity and owner resolution. Never construct this from request headers, JWT
// role claims or the initiating administrator's grants during impersonation.
// It describes only the route permission gate, not resource access or identity.
type PermissionSubject struct {
	Collaborator     bool
	CollaboratorRole int
	Grants           []string
}

const CollaboratorFull = 1

type PermissionDenied struct {
	Detail               string
	ImpersonationInvalid bool
}

func (e *PermissionDenied) Error() string { return e.Detail }
