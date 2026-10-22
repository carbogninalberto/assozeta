package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type InstanceBootstrap struct{ db *sqlx.DB }

func NewInstanceBootstrap(db *sqlx.DB) *InstanceBootstrap { return &InstanceBootstrap{db: db} }
func (r *InstanceBootstrap) FirstInstance(ctx context.Context) (*domain.InstanceBootstrapSnapshot, error) {
	var value domain.InstanceBootstrapSnapshot
	if err := r.db.GetContext(ctx, &value, instanceBootstrapSQL); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	return &value, nil
}

// These are fixed schema names, never request input. Project public keys while
// retaining presence and malformed JSON types; do not fetch encrypted values
// from a normal provider object just to compute a public enabled flag.
func publicIntegrationProjection() string {
	parts := []string{}
	for _, provider := range []string{"ai", "google", "apple", "stripe"} {
		record := "c.integration_settings->'" + provider + "'"
		settings := record + "->'settings'"
		keys := "'enabled'"
		if provider == "google" || provider == "apple" {
			keys += ",'client_id'"
		}
		if provider == "stripe" {
			keys += ",'public_key'"
		}
		// dict(settings) also accepts legacy sequences of pairs. Validate their
		// shape in PostgreSQL, then select only the same public keys. Invalid
		// input keeps its failure semantics without copying private values into
		// application memory. Ordered aggregation preserves last-key-wins.
		projected := fmt.Sprintf(`CASE jsonb_typeof(%[1]s)
 WHEN 'object' THEN (SELECT COALESCE(jsonb_object_agg(key,value),'{}'::jsonb) FROM jsonb_each(%[1]s) WHERE key IN (%[2]s))
 WHEN 'array' THEN CASE WHEN EXISTS (
   SELECT 1 FROM jsonb_array_elements(%[1]s) AS pair(item)
   WHERE NOT CASE jsonb_typeof(item)
     WHEN 'array' THEN CASE WHEN jsonb_array_length(item)=2 THEN jsonb_typeof(item->0) NOT IN ('array','object') ELSE FALSE END
     WHEN 'string' THEN char_length(item #>> '{}')=2
     ELSE FALSE END
 ) THEN '"invalid-pairs"'::jsonb ELSE (
   SELECT COALESCE(jsonb_object_agg(item->>0,item->1 ORDER BY ordinal),'{}'::jsonb)
   FROM jsonb_array_elements(%[1]s) WITH ORDINALITY AS pair(item,ordinal)
   WHERE jsonb_typeof(item)='array' AND item->>0 IN (%[2]s)
 ) END
 WHEN 'null' THEN 'null'::jsonb
 ELSE CASE WHEN %[1]s IS NULL THEN NULL ELSE '"invalid-settings"'::jsonb END END`, settings, keys)
		parts = append(parts, "'"+provider+"',jsonb_build_object('present',c.integration_settings ? '"+provider+"','kind',jsonb_typeof("+record+"),'settings',"+projected+")")
	}
	return "jsonb_build_object(" + strings.Join(parts, ",") + ")"
}

var instanceBootstrapSQL = `SELECT c.name,c.abbreviation,c.primary_color,c.support_email,c.logo_path,c.updated_at,c.display_settings,
 jsonb_typeof(c.integration_settings) AS integration_kind,
 COALESCE(c.integration_settings IN ('null'::jsonb,'{}'::jsonb,'[]'::jsonb,'false'::jsonb,'0'::jsonb,'""'::jsonb),TRUE) AS integrations_empty,
 ` + publicIntegrationProjection() + ` AS public_integrations,
 c.google_client_id,c.apple_client_id,c.meta_title,c.meta_description,c.is_reseller,c.self_hosted,c.support_multiple_associations,
 c.show_course_athletes,c.show_minor_exemption,c.show_tutorial,c.setup_provenance,c.onboarding_completed_at,
 a.sport_association_id::text AS association_id,a.denomination AS association_name,a.user_id::text AS owner_id
 FROM instance_instanceconfiguration c
 LEFT JOIN application_sportassociation a ON a.sport_association_id=c.primary_association_id
 ORDER BY c.id LIMIT 1`
