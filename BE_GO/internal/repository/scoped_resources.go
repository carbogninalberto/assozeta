package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// MaxResourceBatch bounds placeholders, returned IDs and caller memory. Services
// must chunk larger reference sets explicitly, never authorize omitted entries.
const MaxResourceBatch = 512

type ScopedResources struct{ db sqlx.QueryerContext }

// QueryerContext accepts a database or the transaction owning a business write.
// A successful read alone does not lock resources or authorize a later mutation.
func NewScopedResources(db sqlx.QueryerContext) *ScopedResources { return &ScopedResources{db: db} }

type resourceQuery struct {
	from, id, predicate, family string
}

// All identifiers and joins are compile-time constants, never caller input.
// FK joins deliberately do not apply related models' default managers. For
// example an installment may refer to a deleted course subscription; the scoped
// query constrains its parent subscription but does not hide that FK implicitly.
var scopedResourceQueries = map[string]resourceQuery{
	"sport_association":               {"application_sportassociation r", "r.sport_association_id", "r.sport_association_id=$1 AND NOT r.deleted", ""},
	"subscription":                    {"application_subscription r", "r.subscription_id", "r.sport_association_id=$1 AND ($3::uuid IS NULL OR r.group_id=$3)", "r.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)"},
	"associate":                       {"application_associate r", "r.associate_id", "r.sport_association_id=$1 AND NOT r.deleted AND ($3::uuid IS NULL OR r.group_id=$3)", "r.associate_id IN (SELECT associate_id FROM visible_members)"},
	"payment":                         {"application_payment r", "r.payment_id", "r.sport_association_id=$1 AND NOT r.deleted AND ($3::uuid IS NULL OR r.group_id=$3)", "r.payment_id IN (SELECT payment_id FROM visible_payments)"},
	"invoice":                         {"application_invoice r", "r.invoice_id", "r.sport_association_id=$1 AND NOT r.deleted AND ($3::uuid IS NULL OR r.group_id=$3)", "r.invoice_id IN (SELECT invoice_id FROM visible_payments)"},
	"course":                          {"application_course r", "r.course_id", "r.sport_association_id=$1 AND NOT r.deleted AND ($3::uuid IS NULL OR r.group_id=$3)", ""},
	"course_subscription":             {"application_coursesubscription r JOIN application_subscription s ON s.subscription_id=r.subscription_id JOIN application_course c ON c.course_id=r.course_id", "r.course_subscription_id", "s.sport_association_id=$1 AND c.sport_association_id=$1 AND NOT r.deleted", "s.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)"},
	"course_subscription_installment": {"application_coursesubscriptioninstallment r JOIN application_coursesubscription c ON c.course_subscription_id=r.course_subscription_id JOIN application_subscription s ON s.subscription_id=c.subscription_id JOIN application_course course ON course.course_id=c.course_id", "r.course_subscription_installment_id", "s.sport_association_id=$1 AND course.sport_association_id=$1 AND NOT r.deleted AND ($3::uuid IS NULL OR r.group_id=$3)", "s.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)"},
	"carnet":                          {"application_carnet r", "r.carnet_id", "r.sport_association_id=$1", ""},
	"carnet_subscription":             {"application_carnetsubscription r JOIN application_subscription s ON s.subscription_id=r.subscription_id JOIN application_carnet c ON c.carnet_id=r.carnet_id_id", "r.carnet_subscription_id", "s.sport_association_id=$1 AND c.sport_association_id=$1", "s.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)"},
	"attendance_registry":             {"application_attendanceregistry r JOIN application_course c ON c.course_id=r.course_id", "r.attendance_registry_id", "c.sport_association_id=$1", ""},
	"attendance_day":                  {"application_attendanceday r JOIN application_attendanceregistry a ON a.attendance_registry_id=r.attendance_registry_id JOIN application_course c ON c.course_id=a.course_id", "r.attendance_day_id", "c.sport_association_id=$1", ""},
	"group":                           {"application_group r", "r.group_id", "r.sport_association_id=$1", ""},
	"tag":                             {"application_tags r", "r.tag_id", "r.sport_association_id=$1", ""},
	"subscription_file":               {"application_subscriptionfile r JOIN application_subscription s ON s.subscription_id=r.subscription_id", "r.subscription_file_id", "s.sport_association_id=$1", "s.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)"},
	"medical":                         {"application_subscription r", "r.medical_id", "r.sport_association_id=$1 AND ($3::uuid IS NULL OR r.group_id=$3)", "r.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)"},
}

// Subscription.objects uses GroupAwareManager, not SoftDeleteGroupAwareManager:
// historical deleted subscriptions still seed family tax-code visibility. Tax
// codes are compared exactly, including case, as in the existing PostgreSQL ORM
// query. Related Associate deletion/group do not filter the tax-code seed join.
const familyScopeCTE = `,
 family_tax_codes AS (
  SELECT a.tax_code FROM application_subscription s JOIN application_associate a ON a.associate_id=s.associate_id
  WHERE s.sport_association_id=$1 AND s.user_id=$2 AND ($3::uuid IS NULL OR s.group_id=$3)
   AND a.tax_code IS NOT NULL AND a.tax_code<>''
 ),
 visible_members AS (
  SELECT a.associate_id FROM application_associate a
  WHERE a.sport_association_id=$1 AND NOT a.deleted AND ($3::uuid IS NULL OR a.group_id=$3)
   AND (a.user_id=$2 OR a.tax_code IN (SELECT tax_code FROM family_tax_codes))
 ),
 visible_subscriptions AS (
  SELECT s.subscription_id FROM application_subscription s
  WHERE s.sport_association_id=$1 AND ($3::uuid IS NULL OR s.group_id=$3)
   AND (s.user_id=$2 OR s.associate_id IN (SELECT associate_id FROM visible_members))
 ),
 visible_payments AS (
  SELECT p.payment_id,p.invoice_id FROM application_payment p
  WHERE p.sport_association_id=$1 AND NOT p.deleted AND ($3::uuid IS NULL OR p.group_id=$3)
   AND (p.user_id=$2 OR p.associate_id IN (SELECT associate_id FROM visible_members))
 )`

// Available returns only requested, visible canonical UUIDs, without duplicates
// or ordering guarantees. It implements scoped_queryset, not endpoint permission,
// instructor restrictions, document downloads or the medical upload cache grant.
// Callers must treat every absent reference as unavailable and propagate errors.
func (r *ScopedResources) Available(ctx context.Context, scope domain.ResourceScope, kind string, ids []string) ([]string, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	canonical := func(id string) bool {
		value, err := uuid.Parse(id)
		return err == nil && value.String() == id
	}
	if !canonical(scope.AssociationID) || !canonical(scope.EffectiveID) ||
		(scope.GroupID != "" && !canonical(scope.GroupID)) || (scope.Role != domain.RoleAthlete && scope.Role != domain.RoleAssociation && scope.Role != domain.RoleCollaborator) {
		return nil, domain.ErrInvalidResourceScope
	}
	definition, ok := scopedResourceQueries[kind]
	if !ok || len(ids) > MaxResourceBatch {
		return nil, domain.ErrInvalidResourceBatch
	}
	for _, id := range ids {
		if !canonical(id) {
			return nil, domain.ErrInvalidResourceBatch
		}
	}
	if len(ids) == 0 {
		return nil, nil
	}
	// Declare all parameter types even for resource kinds which need no person
	// or group predicate. Values remain bound parameters on every query path.
	query := `WITH scope_parameters AS (SELECT $1::uuid association_id, $2::uuid effective_id, $3::uuid group_id)`
	predicate := definition.predicate
	if scope.Role == domain.RoleAthlete && definition.family != "" {
		query += familyScopeCTE
		predicate += " AND " + definition.family
	}
	query += " SELECT DISTINCT " + definition.id + " FROM " + definition.from + " WHERE " + predicate + " AND " + definition.id + "=ANY($4::uuid[])"
	var group any
	if scope.GroupID != "" {
		group = scope.GroupID
	}
	var result []string
	if err := sqlx.SelectContext(ctx, r.db, &result, query, scope.AssociationID, scope.EffectiveID, group, ids); err != nil {
		return nil, err
	}
	return result, nil
}
