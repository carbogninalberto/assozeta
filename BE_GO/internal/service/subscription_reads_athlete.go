package service

import (
	"context"
	"encoding/json"
	"sort"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// subscriptionReadsStripe is stripe_direct_credentials_configured.
type subscriptionReadsStripe interface {
	DirectCredentialsConfigured(ctx context.Context) (bool, error)
}

// subscriptionReadsAthlete is what the athlete list needs beyond the shared reads.
type subscriptionReadsAthlete struct {
	store   domain.SubscriptionReadsAthleteStore
	stripe  subscriptionReadsStripe
	choices domain.RegistrationChoices
}

// WithAthleteList enables subscription_list's athlete branch (get_optimized_subscriptions).
func (s *SubscriptionReads) WithAthleteList(store domain.SubscriptionReadsAthleteStore, stripe subscriptionReadsStripe,
	choices domain.RegistrationChoices) *SubscriptionReads {
	s.athletes = &subscriptionReadsAthlete{store: store, stripe: stripe, choices: choices}
	return s
}

// WithTasks enables subscription_info's print_document_subscription.delay().
func (s *SubscriptionReads) WithTasks(tasks celery.Publisher) *SubscriptionReads {
	s.tasks = tasks
	return s
}

// subscriptionReadsFeatures is registration_features(): the enabled choices' names.
func subscriptionReadsFeatures(choices domain.RegistrationChoices) []string {
	features := []string{}
	for _, choice := range []struct {
		on   bool
		name string
	}{{choices.Family, "Iscrizione per famiglia"}, {choices.Federation, "Iscrizioni per Enti e Federazioni"},
		{choices.Multiple, "Iscrizioni multiple per federazioni"}} {
		if choice.on {
			features = append(features, choice.name)
		}
	}
	return features
}

// searchAssociation is SportAssociationSearchSerializer(obj.sport_association).
func (s *SubscriptionReads) searchAssociation(ctx context.Context, association string) (any, error) {
	row, err := s.athletes.store.SearchAssociation(ctx, association)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrSubscriptionReadsServer
	}
	// UserSerializerSearch.get_online_payments: athletes False; otherwise
	// online_payments_available(obj.sport_association) (SportAssociation.objects.get(user=...):
	// DoesNotExist is False, several rows raise).
	online := false
	if row.OwnerRole != domain.RoleAthlete {
		if row.OwnerAssociations > 1 {
			return nil, domain.ErrSubscriptionReadsServer // MultipleObjectsReturned
		}
		if row.OwnerAssociations == 1 && row.OnlinePayments {
			if online, err = s.athletes.stripe.DirectCredentialsConfigured(ctx); err != nil {
				return nil, err
			}
		}
	}
	user := map[string]any{"first_name": row.OwnerFirstName, "last_name": row.OwnerLastName, "username": row.OwnerUsername,
		"avatar_image": dto.PersonaOptional(row.OwnerAvatar), "preview_and_custom_features": subscriptionReadsFeatures(s.athletes.choices),
		"disable_account_creation": row.DisableCreation, "force_account_creation": row.ForceCreation, "online_payments": online}
	card, err := s.store.Card(ctx, association)
	if err != nil {
		return nil, err
	}
	configuration := map[string]any{"emit_only_on_approval": false, "customized_template": map[string]any{}}
	if card != nil {
		var template any
		if card.CustomizedTemplate != nil {
			template = json.RawMessage(*card.CustomizedTemplate)
		}
		configuration = map[string]any{"emit_only_on_approval": card.EmitOnlyOnApproval, "customized_template": template}
	}
	return map[string]any{"denomination": row.Denomination, "tax_code": row.TaxCode, "address": dto.PersonaOptional(row.Address),
		"address_cap": dto.PersonaOptional(row.AddressCap), "address_city": dto.PersonaOptional(row.AddressCity), "user": user,
		"sport_association_id": row.ID, "membership_card_configuration": configuration}, nil
}

// athleteList is get_optimized_subscriptions(request.user):
// SubscriptionSerializerAthleteOptimizedList of the athlete's own and
// same-tax-code subscriptions (group-aware, soft-deleted included) by
// -creation_date; an association-scoped session keeps to scoped_queryset's
// subscriptions (request.user carries the session's association).
func (s *SubscriptionReads) athleteList(ctx context.Context, identity domain.ResolvedIdentity, group *string) (any, error) {
	if s.athletes == nil {
		return nil, domain.ErrSubscriptionReadsNotPorted
	}
	ids, err := s.store.AllowedForAthlete(ctx, identity.ScopeUser.ID, group, paymentReadsScope(identity))
	if err != nil {
		return nil, err
	}
	if ids, err = s.sessionVisible(ctx, identity, group, ids); err != nil {
		return nil, err
	}
	rows, err := s.rows(ctx, ids, group)
	if err != nil {
		return nil, err
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].CreationDate.After(rows[j].CreationDate) })
	courses, err := s.athletes.store.AthleteCourses(ctx, ids)
	if err != nil {
		return nil, err
	}
	bySubscription := map[string][]any{}
	for _, course := range courses {
		bySubscription[course.SubscriptionID] = append(bySubscription[course.SubscriptionID], map[string]any{
			"course_subscription_id": course.ID, "paid": course.Paid, "creation_date": dto.DRFUTCDateTime(course.CreationDate),
			"course__title": dto.PersonaOptional(course.CourseTitle), "course__course_id": course.CourseID,
			"course__one_fee_payment": course.CourseOneFee, "course__one_fee": dto.SubscriptionReadsFloat(course.CourseFee),
			"one_fee_payment": course.OneFeePayment})
	}
	carnets, err := s.athletes.store.Carnets(ctx, ids)
	if err != nil {
		return nil, err
	}
	associations, owners := map[string]any{}, map[string]*string{}
	now := s.now()
	items := make([]any, 0, len(rows))
	for _, row := range rows {
		item, err := dto.SubscriptionReadsAthleteItem(row, now)
		if err != nil {
			return nil, err
		}
		if row.Associate != nil {
			associate := row.Associate
			var owner any
			if associate.UserID != nil {
				owner = *associate.UserID
			} else if associate.AssociationID != nil {
				found, cached := owners[*associate.AssociationID]
				if !cached {
					if found, err = s.athletes.store.AssociationOwner(ctx, *associate.AssociationID); err != nil {
						return nil, err
					}
					owners[*associate.AssociationID] = found
				}
				if found == nil {
					return nil, domain.ErrSubscriptionReadsServer
				}
				owner = *found
			}
			item["associate"] = dto.SubscriptionReadsAthleteAssociate(*associate, owner)
		}
		block, cached := associations[row.AssociationID]
		if !cached {
			if block, err = s.searchAssociation(ctx, row.AssociationID); err != nil {
				return nil, err
			}
			associations[row.AssociationID] = block
		}
		item["sport_association"] = block
		item["carnets"] = carnets[row.ID]
		listed := bySubscription[row.ID]
		if listed == nil {
			listed = []any{}
		}
		item["courses"] = listed
		renewal, err := s.futureRenewal(ctx, row, group)
		if err != nil {
			return nil, err
		}
		item["renewal_available"] = !dto.SubscriptionReadsCurrent(row, now) && !renewal
		items = append(items, item)
	}
	return domain.RequestObject{{Name: "data", Value: subscriptionReadsIndexed(items)}, {Name: "meta", Value: map[string]any{}}}, nil
}
