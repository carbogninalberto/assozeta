package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// SubscriptionCard ports subscription_card (subscriptions_views.py): GET reads
// the member card with a subscription token, POST mints a token.
type SubscriptionCard struct {
	store domain.MiscRoutesStore
	now   func() time.Time
	ids   func() string
	// links asks the paired server about unknown tokens (nil: such lookups are refused).
	links PublicTokenSource
}

// PublicTokenSource is instance.sso.public_links.source_token.
type PublicTokenSource interface {
	SourceToken(ctx context.Context, kind, token, resourceID string, group *string) (*BakneyPublicToken, error)
}

// WithPublicLinks sets the paired server lookup for unknown tokens.
func (s *SubscriptionCard) WithPublicLinks(links PublicTokenSource) *SubscriptionCard {
	s.links = links
	return s
}

func NewSubscriptionCard(store domain.MiscRoutesStore, now func() time.Time, ids func() string) *SubscriptionCard {
	return &SubscriptionCard{store: store, now: now, ids: ids}
}

var subscriptionCardExpired = domain.RequestObject{{Name: "msg", Value: "il token è scaduto"}}

func (s *SubscriptionCard) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// Read follows the GET branch. token is the token parameter when present.
func (s *SubscriptionCard) Read(ctx context.Context, identity domain.ResolvedIdentity, raw string, token *string, groupHeader string,
	audit domain.LoginAuditContext) (any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	if token == nil || *token == "" {
		return nil, miscRoutesBadRequest(subscriptionCardExpired)
	}
	tokenID, err := paymentInfoParseUUID(*token)
	if err != nil {
		return nil, domain.ErrMiscRoutesServer // the UUID field rejects the lookup value
	}
	now := s.at()
	tx, err := s.store.BeginMiscRoutes(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	stored, err := tx.CardToken(ctx, tokenID.String(), id.String())
	if err != nil {
		return nil, err
	}
	if stored == nil {
		// source_token: only canonical token and subscription ids reach the
		// pairing; a qualifying pairing would ask the paired authority.
		if subscriptionMiscCanonical(*token) && subscriptionMiscCanonical(raw) {
			if s.links == nil {
				lookup, err := tx.PairingLookup(ctx)
				if err != nil {
					return nil, err
				}
				if lookup {
					return nil, domain.ErrMiscRoutesNotPorted
				}
			} else {
				granted, err := s.links.SourceToken(ctx, "card", *token, raw, supplierGroup(groupHeader))
				if err != nil {
					return nil, err
				}
				if granted != nil {
					// An unsaved SubscriptionToken: nothing to delete, never expired here.
					stored = &domain.SubscriptionCardToken{Expiration: granted.Expires}
				}
			}
		}
		if stored == nil {
			return nil, miscRoutesBadRequest(subscriptionCardExpired)
		}
	}
	if stored.Expiration.Before(now) {
		var actorAssociation *string
		if identity.Actor != nil {
			if actorAssociation, err = tx.ActorAssociation(ctx, identity.Actor.ID); err != nil {
				return nil, err
			}
		}
		if err = tx.DeleteCardToken(ctx, stored.ID, actorAssociation, subscriptionMiscAudit(identity, audit), now); err != nil {
			return nil, err
		}
		if err = tx.Commit(); err != nil {
			return nil, err
		}
		return nil, miscRoutesBadRequest(subscriptionCardExpired)
	}
	member, err := tx.CardMember(ctx, id.String(), supplierGroup(groupHeader), paymentReadsScope(identity))
	if err != nil {
		return nil, err
	}
	if member == nil {
		return nil, domain.ErrMiscRoutesServer // None.sport_association
	}
	configuration, err := tx.CardConfiguration(ctx, member.AssociationID)
	if err != nil {
		return nil, err
	}
	if configuration == nil {
		if err = tx.CreateCardConfiguration(ctx, s.ids(), member.AssociationID, subscriptionMiscAudit(identity, audit), now); err != nil {
			return nil, err
		}
		configuration = &domain.SubscriptionCardConfiguration{}
	}
	// The configuration save is committed before the card renders.
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	if !member.HasAssociate {
		return nil, domain.ErrMiscRoutesServer // None.first_name
	}
	var template any = domain.RequestObject{}
	if configuration.Template != nil {
		parsed, err := dto.ParseProfileTablesJSON([]byte(*configuration.Template))
		if err != nil {
			return nil, err
		}
		// `customized_template if ... is not None else {}`: a stored JSON null is None.
		if parsed != nil {
			template = parsed
		}
	}
	var end any
	if member.EndDate != nil {
		end = member.EndDate.Format("2006-01-02")
	}
	nullable := func(value *string) any {
		if value == nil {
			return nil
		}
		return *value
	}
	card := domain.RequestObject{
		{Name: "subscription_number", Value: nullable(member.Number)},
		{Name: "subscription_type", Value: nullable(member.Type)},
		{Name: "subscription_end_date", Value: end},
		{Name: "associate", Value: domain.RequestObject{
			{Name: "first_name", Value: nullable(member.FirstName)},
			{Name: "last_name", Value: nullable(member.LastName)},
			{Name: "avatar", Value: nil},
		}},
		{Name: "sport_association", Value: domain.RequestObject{
			{Name: "denomination", Value: nullable(member.Denomination)},
			{Name: "user", Value: domain.RequestObject{{Name: "avatar_image", Value: nullable(member.AvatarImage)}}},
			{Name: "membership_card_configuration", Value: domain.RequestObject{
				{Name: "emit_only_on_approval", Value: configuration.EmitOnlyOnApproval},
				{Name: "customized_template", Value: template},
			}},
		}},
	}
	return domain.RequestObject{{Name: "member", Value: card}}, nil
}

// Mint follows the POST branch.
func (s *SubscriptionCard) Mint(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader string, audit domain.LoginAuditContext) (any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	if identity.ScopeUser == nil || identity.ScopeUser.Role != domain.RoleAssociation {
		return nil, &domain.MiscRoutesAnswer{Status: 403, Body: domain.RequestObject{{Name: "msg", Value: "User not allowed."}}}
	}
	tx, err := s.store.BeginMiscRoutes(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, domain.ErrMiscRoutesServer
	}
	if err != nil {
		return nil, err
	}
	subscription, err := tx.ScopedSubscription(ctx, id.String(), association, supplierGroup(groupHeader), paymentReadsScope(identity))
	if err != nil {
		return nil, err
	}
	if subscription == nil {
		return nil, &domain.MiscRoutesAnswer{Status: 404, Body: domain.RequestObject{{Name: "msg", Value: "Subscription not found."}}}
	}
	now := s.at()
	expires := now.Add(3 * 24 * time.Hour)
	token := s.ids()
	if err = tx.CreateCardToken(ctx, s.ids(), *subscription, association, token, now, expires, paymentCategoryAuditContext(identity, audit),
		now); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return domain.RequestObject{{Name: "token", Value: token}}, nil
}
