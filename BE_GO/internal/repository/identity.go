package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type IdentityRelations struct{ db *sqlx.DB }

func NewIdentityRelations(db *sqlx.DB) *IdentityRelations { return &IdentityRelations{db: db} }

func (r *IdentityRelations) AssociationOwnedBy(ctx context.Context, owner string) (string, error) {
	var id string
	err := r.db.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return "", domain.ErrAssociationNotFound
	}
	return id, err
}

// EligibleTarget reproduces eligible_users' owner/admin branches without a
// per-row lookup. Owner membership lookup deliberately ignores selected group,
// as Django Associate.objects.all_objects() does, while excluding deleted members.
func (r *IdentityRelations) EligibleTarget(ctx context.Context, actor, association, target string) (domain.Account, error) {
	predicate := `AND (
  role=2 OR
  (role=1 AND EXISTS(SELECT 1 FROM application_sportassociation a WHERE a.user_id=u.user_id AND NOT a.deleted)) OR
  (role=3 AND EXISTS(SELECT 1 FROM bakney_user owner JOIN application_sportassociation a ON a.user_id=owner.user_id
   WHERE owner.user_id=u.connected_user_id AND owner.is_active AND NOT owner.deleted AND owner.role=1 AND NOT a.deleted))
 )`
	args := []any{target}
	if association != "" {
		predicate = `AND u.user_id<>$3 AND (
   (role=2 AND EXISTS(SELECT 1 FROM application_associate member WHERE member.user_id=u.user_id AND member.sport_association_id=$2 AND NOT member.deleted)) OR
   (role=3 AND connected_user_id=$3)
  )`
		args = append(args, association, actor)
	}
	var result domain.Account
	err := r.db.GetContext(ctx, &result, `SELECT `+accountColumns+` FROM bakney_user u WHERE u.user_id=$1 AND NOT u.deleted AND u.is_active AND NOT u.is_superuser `+predicate, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.Account{}, domain.ErrAccountNotFound
	}
	return result, err
}

// PrimaryExportOwner follows the first instance configuration and its FK owner.
// Django's related-object access does not apply the active/deleted user manager
// here. This explicit admin-export scope is distinct from normal account lookup.
func (r *IdentityRelations) PrimaryExportOwner(ctx context.Context) (domain.Account, error) {
	var result domain.Account
	err := r.db.GetContext(ctx, &result, `SELECT `+accountColumns+` FROM bakney_user WHERE user_id=(
  SELECT a.user_id FROM instance_instanceconfiguration c JOIN application_sportassociation a ON a.sport_association_id=c.primary_association_id
  WHERE c.id=(SELECT id FROM instance_instanceconfiguration ORDER BY id LIMIT 1) AND c.self_hosted
 )`)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.Account{}, domain.ErrAssociationNotFound
	}
	return result, err
}
