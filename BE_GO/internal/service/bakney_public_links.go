package service

import (
	"context"
	"errors"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// BakneyPublicLinkTransaction is the transaction.atomic block of
// instance.sso.public_links.source_token.
type BakneyPublicLinkTransaction interface {
	// LockFirstPairing is BakneyPairing.objects.select_for_update().select_related('config').first().
	LockFirstPairing(context.Context) (*domain.BakneyPairing, *domain.BakneyConfiguration, error)
	// PairedAssociation reports the pairing's live association with an active association user.
	PairedAssociation(ctx context.Context, association string) (bool, error)
	// AssociationSubscription is Subscription.objects.filter(pk=..., sport_association=...).exists().
	AssociationSubscription(ctx context.Context, subscription, association string, group *string) (bool, error)
	Commit() error
	Rollback() error
}

type BakneyPublicLinkStore interface {
	BeginPublicLink(context.Context) (BakneyPublicLinkTransaction, error)
}

// BakneyPublicToken is the unsaved capability source_token returns: a
// SubscriptionToken's expiration, or a cumulative link's association and gym name.
type BakneyPublicToken struct {
	Expires       time.Time
	AssociationID string
	GymName       string
}

// BakneyPublicLinks ports instance.sso.public_links.
type BakneyPublicLinks struct {
	sso   *BakneySSO
	store BakneyPublicLinkStore
}

func NewBakneyPublicLinks(sso *BakneySSO, store BakneyPublicLinkStore) *BakneyPublicLinks {
	return &BakneyPublicLinks{sso: sso, store: store}
}

// ErrBakneyPublicLinkNotPorted: an expires_at whose instant Go's time does not hold.
var ErrBakneyPublicLinkNotPorted = errors.New("bakney public link value is not ported")

// SourceToken is source_token(kind, token, resource_id): nil when the paired
// server does not vouch for the token (or cannot be asked). kind is "card"
// (resourceID a subscription id) or "cumulative".
func (l *BakneyPublicLinks) SourceToken(ctx context.Context, kind, token, resourceID string, group *string) (*BakneyPublicToken, error) {
	if !canonicalUUID(token) || (kind == "card" && !canonicalUUID(resourceID)) {
		return nil, nil
	}
	tx, err := l.store.BeginPublicLink(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	granted, err := l.source(ctx, tx, kind, token, resourceID, group)
	var refused *BakneySSOError
	if errors.As(err, &refused) {
		granted, err = nil, nil // except (SSOError, ValueError, TypeError): return None
	}
	if err != nil {
		return nil, err
	}
	return granted, tx.Commit()
}

func (l *BakneyPublicLinks) source(ctx context.Context, tx BakneyPublicLinkTransaction, kind, token, resourceID string, group *string) (
	*BakneyPublicToken, error) {
	pairing, config, err := tx.LockFirstPairing(ctx)
	if err != nil {
		return nil, err
	}
	if pairing == nil || !config.SelfHosted || (pairing.State != "pending" && pairing.State != "paired") || pairing.RemotePairingID == nil {
		return nil, nil
	}
	state := &bakneyState{config: config, pairing: pairing}
	if err = l.sso.checkBinding(state); err != nil {
		return nil, err
	}
	if pairing.AssociationID == nil {
		return nil, nil
	}
	found, err := tx.PairedAssociation(ctx, *pairing.AssociationID)
	if err != nil || !found {
		return nil, err
	}
	payload := [][2]string{{"kind", kind}, {"token", token}}
	switch kind {
	case "card":
		exists, err := tx.AssociationSubscription(ctx, resourceID, *pairing.AssociationID, group)
		if err != nil || !exists {
			return nil, err
		}
		payload = append(payload, [2]string{"resource_id", resourceID})
	case "cumulative":
	default:
		return nil, nil
	}
	data, err := l.sso.upstream(ctx, pairing, "public-token", payload)
	if err != nil {
		return nil, err
	}
	expected := state.binding()
	for _, item := range [][2]string{{"instance_id", expected.instanceID}, {"pairing_id", expected.pairingID},
		{"association_id", expected.associationID}, {"origin", expected.origin}, {"callback_uri", expected.callbackURI}, {"kind", kind}} {
		if differs(data, item[0], textPointer(item[1])) {
			return nil, nil
		}
	}
	if generation, _ := data.Lookup("generation"); !canonicalUUID(generation) {
		return nil, nil
	}
	var text any = ""
	if value, present := data.Lookup("expires_at"); present {
		text = value
	}
	raw, ok := text.(string)
	if !ok {
		return nil, nil // fromisoformat on a non-str: TypeError
	}
	expires, aware, outcome := dto.DjangoParseDateTime(raw)
	switch outcome {
	case dto.DjangoDateTimeNotPorted:
		return nil, ErrBakneyPublicLinkNotPorted
	case dto.DjangoDateTimeParsed:
	default:
		return nil, nil
	}
	if !aware || !expires.After(l.sso.now()) {
		return nil, nil
	}
	if kind == "card" {
		if differs(data, "resource_id", textPointer(resourceID)) {
			return nil, nil
		}
		return &BakneyPublicToken{Expires: expires}, nil
	}
	gym, _ := data.Lookup("gym_name")
	name, ok := gym.(string)
	if !ok || utf8.RuneCountInString(name) > 255 {
		return nil, nil
	}
	return &BakneyPublicToken{Expires: expires, AssociationID: *pairing.AssociationID, GymName: name}, nil
}
