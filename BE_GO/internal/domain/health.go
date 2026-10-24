package domain

// DependencyStatus contains no infrastructure error messages or credentials.
type DependencyStatus struct {
	Database bool `json:"database"`
	Redis    bool `json:"redis"`
}
type Readiness struct {
	Status string           `json:"status"`
	Checks DependencyStatus `json:"checks"`
}

func (r Readiness) Ready() bool { return r.Checks.Database && r.Checks.Redis }
