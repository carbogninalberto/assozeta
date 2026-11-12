package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// RouterStockDestroy is DestroyModelMixin.perform_destroy (instance.delete())
// for the router's detail routes whose model has no delete() override.
type RouterStockDestroy struct {
	store domain.RouterStockDeleteStore
}

func NewRouterStockDestroy(store domain.RouterStockDeleteStore) *RouterStockDestroy {
	return &RouterStockDestroy{store: store}
}

// Delete deletes the row get_object() returned; X-Group-Id selects what the
// group-aware managers of the delete signals see.
func (s *RouterStockDestroy) Delete(ctx context.Context, identity domain.ResolvedIdentity, label, key, groupHeader string,
	audit domain.LoginAuditContext) error {
	return s.store.DeleteModel(ctx, label, key, paymentCategoryAuditContext(identity, audit), supplierGroup(groupHeader))
}
