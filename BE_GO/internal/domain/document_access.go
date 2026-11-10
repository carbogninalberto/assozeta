package domain

import "context"

// MemberDocumentStore answers docmanager.access for a member account: whether
// the account is the instance owner and whether a subscription, an invoice or
// a document is among the member's own records (those of the user, of the
// associates of the user's subscriptions and of associates sharing a tax code
// with them).
type MemberDocumentStore interface {
	InstanceOwner(ctx context.Context, user string) (bool, error)
	MemberSubscription(ctx context.Context, user, subscription string) (bool, error)
	MemberInvoice(ctx context.Context, user, invoice string) (bool, error)
	MemberDocument(ctx context.Context, user, document string) (bool, error)
}
