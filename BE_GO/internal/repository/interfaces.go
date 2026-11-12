package repository

import "context"

// DependencyProbe checks an infrastructure dependency without exposing its
// connection details to the transport layer.
type DependencyProbe interface{ Check(context.Context) error }
