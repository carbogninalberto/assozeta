package repository

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type Personas struct{ db *sqlx.DB }

func NewPersonas(db *sqlx.DB) *Personas { return &Personas{db: db} }

var _ domain.PersonasStore = (*Personas)(nil)

func (r *Personas) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	// User.sport_association: SportAssociation.objects (live) by user, never scoped.
	return paymentCategoryAssociation(ctx, r.db, owner, nil, false)
}

func personaColumns(alias string) string {
	columns := []string{"associate_id", "user_id", "sport_association_id", "picture_path", "first_name", "last_name", "sex",
		"tax_code", "born_date", "born_city", "address_city", "address", "address_cap", "nationality", "nationality_residence",
		"email", "phone", "phone_2", "phone_3", "phone_4", "phone_label", "phone_2_label", "phone_3_label", "phone_4_label",
		"is_minor", "disabled", "draft", "group_id", "deleted", "family_id", "family_role", "id_number", "creation_date",
		"migrated", "notes"}
	for i, column := range columns {
		columns[i] = alias + "." + column
	}
	return strings.Join(columns, ", ")
}

// personaSearchFields are get_controlled_searchable_fields(Associate): every
// concrete non-relation field, then tutors__{first_name,last_name,tax_code,
// email,phone}, as Django's icontains operands (`field::text`; a timestamptz
// renders in the connection's UTC zone).
var personaSearchFields = []string{"a.associate_id::text", "a.picture_path::text", "a.first_name::text", "a.last_name::text",
	"a.sex::text", "a.tax_code::text", "a.born_date::text", "a.born_city::text", "a.address_city::text", "a.address::text",
	"a.address_cap::text", "a.nationality::text", "a.nationality_residence::text", "a.email::text", "a.phone::text",
	"a.phone_2::text", "a.phone_3::text", "a.phone_4::text", "a.phone_label::text", "a.phone_2_label::text",
	"a.phone_3_label::text", "a.phone_4_label::text", "a.is_minor::text", "a.disabled::text", "a.draft::text",
	"a.deleted::text", "a.family_role::text", "a.id_number::text", personaCreationText, "a.migrated::text", "a.notes::text",
	"t5.first_name::text", "t5.last_name::text", "t5.tax_code::text", "t5.email::text", "t5.phone::text"}

const personaCreationText = "((a.creation_date AT TIME ZONE 'UTC')::text || '+00')"

// Fields whose name contains 'date' or 'created' take the date variations.
var personaDateFields = []string{"a.born_date::text", personaCreationText}

// Fields whose name contains 'name', 'address', 'city' or 'notes' take the permutations.
var personaPermutationFields = []string{"a.first_name::text", "a.last_name::text", "a.born_city::text", "a.address_city::text",
	"a.address::text", "a.address_cap::text", "a.notes::text", "t5.first_name::text", "t5.last_name::text"}

// personaOrderColumns resolve domain.PersonaOrderFields to columns or annotations.
var personaOrderColumns = func() map[string]string {
	columns := map[string]string{"pk": "a.associate_id", "is_tutor_annotated": personaIsTutor, "tutors_count": "COUNT(r.id)"}
	for _, name := range domain.PersonaOrderFields {
		if _, ok := columns[name]; ok {
			continue
		}
		switch name {
		case "user", "sport_association", "group", "family":
			columns[name] = "a." + name + "_id"
		default:
			columns[name] = "a." + name
		}
	}
	return columns
}()

const personaIsTutor = `EXISTS(SELECT 1 FROM application_associatetutorrelation u WHERE u.tutor_id=a.associate_id)`

type personaArgs []any

func (a *personaArgs) add(value any) string {
	*a = append(*a, value)
	return fmt.Sprintf("$%d", len(*a))
}

func personaLike(field, parameter string) string {
	return "UPPER(" + field + ") LIKE UPPER(" + parameter + ")"
}

// personaQuery builds FROM ... GROUP BY for get_queryset, plus the relevance
// expression when searched and ordered by it (its parameters are bound only
// then: PostgreSQL cannot type unused parameters). The search joins the tutors
// a second time (a new multi-valued join after the Count annotation), as
// Django does.
func personaQuery(query domain.PersonaQuery, ordered bool) (string, string, personaArgs) {
	args := personaArgs{}
	from := ` FROM application_associate a
 LEFT JOIN application_associatetutorrelation r ON r.associate_id=a.associate_id`
	association, group := args.add(query.AssociationID), args.add(query.GroupID)
	where := []string{"NOT a.deleted", "a.sport_association_id=" + association, "(" + group + "::uuid IS NULL OR a.group_id=" + group + "::uuid)"}
	relevance := ""
	if search := query.Search; search != nil {
		if len(search.Words) > 0 {
			from += `
 LEFT JOIN application_associatetutorrelation t4 ON t4.associate_id=a.associate_id
 LEFT JOIN application_associate t5 ON t5.associate_id=t4.tutor_id`
			words := make([]string, len(search.Words))
			for i, word := range search.Words {
				parameter := args.add(word)
				terms := make([]string, 0, len(personaSearchFields))
				for _, field := range personaSearchFields {
					terms = append(terms, personaLike(field, parameter))
				}
				for _, variation := range search.Dates[i] {
					variationParameter := args.add(variation)
					for _, field := range personaDateFields {
						terms = append(terms, personaLike(field, variationParameter))
					}
				}
				words[i] = "(" + strings.Join(terms, " OR ") + ")"
			}
			condition := "(" + strings.Join(words, " AND ") + ")"
			if len(search.Permutations) > 0 {
				terms := []string{}
				for _, permutation := range search.Permutations {
					parameter := args.add(permutation)
					for _, field := range personaPermutationFields {
						terms = append(terms, personaLike(field, parameter))
					}
				}
				condition = "(" + condition + " OR " + strings.Join(terms, " OR ") + ")"
			}
			where = append(where, condition)
		}
	}
	if search := query.Search; search != nil && ordered && personaOrdersByRelevance(query.Order) {
		term, prefix, contains := args.add(search.Term), args.add(search.Prefix), args.add(search.Contains)
		cases := []string{}
		for _, field := range []string{"a.first_name::text", "a.last_name::text", "a.tax_code::text"} {
			cases = append(cases, "WHEN UPPER("+field+") = UPPER("+term+") THEN 10.0",
				"WHEN "+personaLike(field, prefix)+" THEN 7.0", "WHEN "+personaLike(field, contains)+" THEN 4.0")
		}
		relevance = "(CASE " + strings.Join(cases, " ") + " ELSE 1.0 END)"
	}
	for _, tutor := range query.IsTutor {
		if tutor {
			where = append(where, personaIsTutor)
		} else {
			where = append(where, "NOT "+personaIsTutor)
		}
	}
	if query.BornOnOrBefore != nil {
		where = append(where, "a.born_date <= "+args.add(query.BornOnOrBefore.Format("2006-01-02"))+"::date")
	}
	if query.BornOnOrAfter != nil {
		where = append(where, "a.born_date >= "+args.add(query.BornOnOrAfter.Format("2006-01-02"))+"::date")
	}
	if query.ID != nil {
		where = append(where, "a.associate_id="+args.add(*query.ID)+"::uuid")
	}
	return from + "\n WHERE " + strings.Join(where, " AND ") + "\n GROUP BY a.associate_id", relevance, args
}

func (r *Personas) CountPersonas(ctx context.Context, query domain.PersonaQuery) (int64, error) {
	body, _, args := personaQuery(query, false)
	var count int64
	err := r.db.GetContext(ctx, &count, "SELECT COUNT(*) FROM (SELECT a.associate_id"+body+") s", args...)
	return count, err
}

func (r *Personas) Personas(ctx context.Context, query domain.PersonaQuery, limit, offset int64) ([]domain.PersonaListed, error) {
	body, relevance, args := personaQuery(query, true)
	sql := "SELECT " + personaColumns("a") + ", " + personaIsTutor + " AS is_tutor_annotated" + body
	switch order := query.Order; {
	case order != nil && order.Random:
		sql += " ORDER BY RANDOM()"
	case order != nil:
		expression := relevance
		if order.Key != "relevance" {
			expression = personaOrderColumns[order.Key]
		}
		if expression == "" {
			return nil, fmt.Errorf("persona ordering %q is not resolvable", order.Key)
		}
		direction := " ASC"
		if order.Descending {
			direction = " DESC"
		}
		sql += " ORDER BY " + expression + direction
	case relevance != "":
		sql += " ORDER BY " + relevance + " DESC"
	}
	if limit >= 0 {
		sql += " LIMIT " + args.add(limit) + " OFFSET " + args.add(offset)
	}
	rows := []domain.PersonaListed{}
	err := r.db.SelectContext(ctx, &rows, sql, args...)
	return rows, err
}

// personaOrdersByRelevance: a search orders by -relevance unless sort[...] replaced it.
func personaOrdersByRelevance(order *domain.PersonaOrder) bool {
	return order == nil || (!order.Random && order.Key == "relevance")
}

const personaRelationsSelect = `SELECT r.associate_id AS relation_associate_id, r.is_primary AS relation_is_primary, `

func (r *Personas) Relations(ctx context.Context, associates []string) ([]domain.PersonaRelation, error) {
	rows := []domain.PersonaRelation{}
	if len(associates) == 0 {
		return rows, nil
	}
	err := r.db.SelectContext(ctx, &rows, personaRelationsSelect+personaColumns("t")+`
 FROM application_associatetutorrelation r JOIN application_associate t ON t.associate_id=r.tutor_id
 WHERE r.associate_id = ANY(string_to_array($1, ',')::uuid[])`, strings.Join(associates, ","))
	return rows, err
}

func (r *Personas) Tutors(ctx context.Context, associates []string, group *string) ([]domain.PersonaRelation, error) {
	rows := []domain.PersonaRelation{}
	if len(associates) == 0 {
		return rows, nil
	}
	err := r.db.SelectContext(ctx, &rows, personaRelationsSelect+personaColumns("t")+`
 FROM application_associatetutorrelation r JOIN application_associate t ON t.associate_id=r.tutor_id
 WHERE r.associate_id = ANY(string_to_array($1, ',')::uuid[]) AND NOT t.deleted AND ($2::uuid IS NULL OR t.group_id=$2::uuid)`,
		strings.Join(associates, ","), group)
	return rows, err
}

func (r *Personas) FamilyMembers(ctx context.Context, family, self string, group *string) ([]string, error) {
	rows := []string{}
	err := r.db.SelectContext(ctx, &rows, `SELECT associate_id FROM application_associate
 WHERE family_id=$1 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid) AND associate_id<>$2`, family, self, group)
	return rows, err
}

func (r *Personas) PersonaNames(ctx context.Context, association string, group *string) ([]domain.PersonaName, error) {
	rows := []domain.PersonaName{}
	err := r.db.SelectContext(ctx, &rows, `SELECT associate_id, first_name, last_name FROM application_associate
 WHERE sport_association_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)`, association, group)
	return rows, err
}

func (r *Personas) CurrentSubscriptions(ctx context.Context, association string, group *string, today time.Time) ([]domain.PersonaSubscription, error) {
	rows := []domain.PersonaSubscription{}
	err := r.db.SelectContext(ctx, &rows, `SELECT s.subscription_id, s.status_flag, s.type, s.associate_id, a.first_name, a.last_name,
 a.email, a.tax_code
 FROM application_subscription s JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE NOT s.archived AND NOT a.deleted AND s.associate_id IS NOT NULL AND NOT s.deleted AND s.end_date >= $3::date
  AND s.sport_association_id=$1 AND s.start_date <= $3::date AND ($2::uuid IS NULL OR s.group_id=$2::uuid)`,
		association, group, today.Format("2006-01-02"))
	return rows, err
}
