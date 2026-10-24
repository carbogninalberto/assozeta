package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// Production supplies the ordinary DB, preserving Django's separately committed
// audit/cache steps. Caller-owned transactions are useful for read-only tests;
// this repository never opens a hidden transaction around Redis work.
type ImpersonationManagement struct{ db sqlx.ExtContext }

func NewImpersonationManagement(db sqlx.ExtContext) *ImpersonationManagement {
	return &ImpersonationManagement{db}
}

var _ domain.ImpersonationDirectory = (*ImpersonationManagement)(nil)

func (r *ImpersonationManagement) ImpersonationAssociation(ctx context.Context, actor string) (domain.ImpersonationAssociation, error) {
	var row domain.ImpersonationAssociation
	err := sqlx.GetContext(ctx, r.db, &row, `SELECT sport_association_id AS association_id,denomination AS association_name FROM application_sportassociation WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, actor)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrAssociationNotFound
	}
	return row, err
}

const managementPersonColumns = `u.user_id,u.username,u.email,u.first_name,u.last_name,u.last_login,u.role,u.deleted,
 CASE WHEN u.role=3 THEN ca.sport_association_id ELSE a.sport_association_id END AS association_id,
 CASE WHEN u.role=3 THEN ca.denomination ELSE a.denomination END AS association_name`
const managementPersonFrom = ` FROM bakney_user u LEFT JOIN application_sportassociation a ON a.user_id=u.user_id
 LEFT JOIN bakney_user owner ON owner.user_id=u.connected_user_id
 LEFT JOIN application_sportassociation ca ON ca.user_id=owner.user_id`
const managementEligible = ` WHERE NOT u.deleted AND u.is_active AND NOT u.is_superuser AND (
 ($2::uuid IS NULL AND (u.role=2 OR (u.role=1 AND a.sport_association_id IS NOT NULL AND NOT a.deleted)
  OR (u.role=3 AND owner.is_active AND NOT owner.deleted AND owner.role=1 AND ca.sport_association_id IS NOT NULL AND NOT ca.deleted)))
 OR ($2::uuid IS NOT NULL AND u.user_id<>$1::uuid AND ((u.role=2 AND EXISTS(SELECT 1 FROM application_associate member WHERE member.user_id=u.user_id AND member.sport_association_id=$2::uuid AND NOT member.deleted)) OR (u.role=3 AND u.connected_user_id=$1::uuid))))`

func managementScope(association string) any {
	if association == "" {
		return nil
	}
	return association
}

func (r *ImpersonationManagement) EligibleImpersonationPerson(ctx context.Context, actor, association, target string) (domain.ImpersonationPerson, error) {
	var row domain.ImpersonationPerson
	err := sqlx.GetContext(ctx, r.db, &row, `SELECT `+managementPersonColumns+managementPersonFrom+managementEligible+` AND u.user_id=$3::uuid`, actor, managementScope(association), target)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrAccountNotFound
	}
	return row, err
}
func (r *ImpersonationManagement) ImpersonationPersonIncludingDeleted(ctx context.Context, id string) (domain.ImpersonationPerson, error) {
	var row domain.ImpersonationPerson
	err := sqlx.GetContext(ctx, r.db, &row, `SELECT `+managementPersonColumns+managementPersonFrom+` WHERE u.user_id=$1::uuid`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrAccountNotFound
	}
	return row, err
}

func (r *ImpersonationManagement) ListImpersonationPeople(ctx context.Context, actor, association string, filter domain.ImpersonationListFilter) ([]domain.ImpersonationPerson, int64, error) {
	order := map[string]string{"username": "u.username", "email": "u.email", "role": "u.role", "first_name": "u.first_name", "last_login": "u.last_login", "association.name": "a.denomination"}[filter.Sort]
	if order == "" || filter.Limit < 1 || filter.Limit > 100 {
		return nil, 0, domain.ErrInvalidScopedRequest
	}
	if filter.Descending {
		order += " DESC"
	}
	where := managementEligible
	args := []any{actor, managementScope(association)}
	if filter.Role != 0 {
		args = append(args, filter.Role)
		where += fmt.Sprintf(" AND u.role=$%d", len(args))
	}
	if filter.Search != "" {
		pattern := "%" + strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(filter.Search) + "%"
		args = append(args, pattern)
		var expressions []string
		for _, column := range []string{"u.username", "u.email", "u.first_name", "u.last_name", "a.denomination", "ca.denomination"} {
			expressions = append(expressions, fmt.Sprintf("UPPER(%s::text) LIKE UPPER($%d::text)", column, len(args)))
		}
		where += " AND (" + strings.Join(expressions, " OR ") + ")"
	}
	var total int64
	if err := sqlx.GetContext(ctx, r.db, &total, "SELECT count(*)"+managementPersonFrom+where, args...); err != nil {
		return nil, 0, err
	}
	args = append(args, filter.Limit, filter.Offset)
	query := "SELECT " + managementPersonColumns + managementPersonFrom + where + " ORDER BY " + order + fmt.Sprintf(",u.user_id LIMIT $%d OFFSET $%d::text::bigint", len(args)-1, len(args))
	rows := []domain.ImpersonationPerson{}
	err := sqlx.SelectContext(ctx, r.db, &rows, query, args...)
	return rows, total, err
}

func (r *ImpersonationManagement) AppendImpersonationTransition(ctx context.Context, event domain.ImpersonationTransition) error {
	var administrator *string
	if event.Actor.Superuser && event.Actor.Active && !event.Actor.Deleted {
		administrator = &event.Actor.ID
	}
	additional, err := json.Marshal(map[string]any{"impersonation_session": event.SessionID, "actor_id": event.Actor.ID, "administrator_id": administrator, "association_id": event.ScopeAssociationID, "target_user_id": event.Target.ID, "target_role": event.Target.Role})
	if err != nil {
		return err
	}
	changes, err := json.Marshal(map[string][2]string{"impersonation": {"", event.Action}})
	if err != nil {
		return err
	}
	var remotePort any
	if event.Audit.RemotePort != nil {
		remotePort = string(*event.Audit.RemotePort)
	}
	var id int64
	err = sqlx.GetContext(ctx, r.db, &id, `INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT id,$1,NULL,$2,NULL,3,'',$3::jsonb,$4::uuid,$5,$6::inet,$7::text::integer,$8,$9::jsonb,NULL FROM django_content_type WHERE app_label='application' AND model='user' RETURNING id`,
		event.Target.ID, "name: "+event.Target.FirstName+" "+event.Target.LastName, string(changes), event.Actor.ID, event.Audit.CorrelationID, event.Audit.RemoteAddress, remotePort, event.At, string(additional))
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("audit model content type missing")
	}
	if err != nil {
		return err
	}
	// Django's signal performs this guard before its best-effort resolver and
	// INSERT. A failed lookup propagates after the already committed ACCESS row;
	// an existing index prevents any resolver or insertion attempt.
	var indexed bool
	if err = sqlx.GetContext(ctx, r.db, &indexed, `SELECT EXISTS(SELECT 1 FROM application_auditlogindex WHERE log_entry_id=$1)`, id); err != nil {
		return err
	}
	if indexed {
		return nil
	}
	// This view does not enter Django's atomic_audit_mutation context. The
	// registered user resolver, actor fallback and index INSERT are best effort;
	// actual HTTP fault tests require the ACCESS row to survive index failure.
	var index struct {
		ID   string `db:"sport_association_id"`
		Path string `db:"resolution_path"`
	}
	err = sqlx.GetContext(ctx, r.db, &index, `SELECT a.sport_association_id,
 CASE WHEN a.user_id=$1::uuid THEN 'user.sport_association' ELSE 'actor.sport_association' END AS resolution_path
 FROM application_sportassociation a JOIN bakney_user u ON u.user_id=a.user_id
 WHERE NOT a.deleted AND (a.user_id=$2::uuid OR (a.user_id=$1::uuid AND NOT u.deleted))
 ORDER BY CASE WHEN a.user_id=$1::uuid THEN 0 ELSE 1 END,a.sport_association_id LIMIT 1`, event.Target.ID, event.Actor.ID)
	if err == nil {
		_, _ = r.db.ExecContext(ctx, `INSERT INTO application_auditlogindex(audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at) VALUES($1,$2,$3,$4,$5)`, uuid.NewString(), id, index.ID, index.Path, event.At)
	}
	return nil
}
