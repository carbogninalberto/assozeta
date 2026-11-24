package smtpsend

import "context"

// EffectIntent runs immediately before SMTP DATA. A nil completion callback
// means the provider already accepted this logical message.
type EffectIntent func(context.Context) (func(accepted bool) error, error)
type intentKey struct{}

func WithEffectIntent(ctx context.Context, intent EffectIntent) context.Context {
	return context.WithValue(ctx, intentKey{}, intent)
}
