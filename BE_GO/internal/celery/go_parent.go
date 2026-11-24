package celery

import "context"

type goParentKey struct{}
type goParent struct{ id, root string }

// WithGoParent routes the active Go job's child publications to its runtime
// and supplies their protocol-2 lineage.
func WithGoParent(ctx context.Context, id, root string) context.Context {
	return context.WithValue(ctx, goParentKey{}, goParent{id: id, root: root})
}
