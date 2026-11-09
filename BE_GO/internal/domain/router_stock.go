package domain

import "context"

// RouterStockDeleteStore is Model.delete() for the router's stock destroy:
// django.db.models.deletion.Collector over the generated model graph.
type RouterStockDeleteStore interface {
	// DeleteModel deletes the row of label ('app.Model') with primary key key,
	// with its cascade, signals and auditlog entries, in one transaction.
	DeleteModel(ctx context.Context, label, key string, audit LoginAuditContext, group *string) error
}
