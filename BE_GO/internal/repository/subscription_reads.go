package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// SubscriptionReads reads subscriptions for subscription_list,
// subscription_list_all, subscription_list_archived, subscription_info and
// subscription_calendar. Subscription.objects is group-aware and keeps
// soft-deleted rows.
type SubscriptionReads struct {
	db       *sqlx.DB
	personas *Personas
}

func NewSubscriptionReads(db *sqlx.DB) *SubscriptionReads {
	return &SubscriptionReads{db: db, personas: NewPersonas(db)}
}

var _ domain.SubscriptionReadsStore = (*SubscriptionReads)(nil)

func (r *SubscriptionReads) ScopedAssociation(ctx context.Context, user string, scope *string) (*string, error) {
	id, err := paymentCategoryAssociation(ctx, r.db, user, scope, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (r *SubscriptionReads) LiveAssociation(ctx context.Context, id string) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1 AND NOT deleted)`, id)
	return found, err
}

// subscriptionReadsSearchField is one icontains operand of sqlite_search over Subscription.
type subscriptionReadsSearchField struct {
	column string
	// date: the field name contains 'date' or 'created'; permuted: 'name', 'address', 'city' or 'notes'.
	date, permuted bool
}

// subscriptionReadsSearchFields are get_controlled_searchable_fields(Subscription):
// the concrete non-relation fields, then the associate's and its tutors' listed fields.
var subscriptionReadsSearchFields = func() []subscriptionReadsSearchField {
	fields := []subscriptionReadsSearchField{}
	for _, name := range []string{"subscription_id", "custom_data", "additional_fields", "type", "role", "acceptance_date", "resignation_date",
		"status_flag", "creation_date", "start_date", "end_date", "signature_url", "signature_storage_key", "archived", "draft", "trial", "meta",
		"subscription_number", "subscription_type", "deleted", "competitive", "notes"} {
		fields = append(fields, subscriptionReadsSearchField{column: paymentReadsText("s."+name, name == "creation_date"),
			date: strings.Contains(name, "date"), permuted: name == "notes"})
	}
	for _, alias := range []string{"sa", "st"} {
		for _, field := range paymentReadsPerson(alias) {
			fields = append(fields, subscriptionReadsSearchField{column: field.column + "::text", date: field.date, permuted: field.permuted})
		}
	}
	return fields
}()

// subscriptionReadsSmartCondition is sqlite_search's Q over one joined row
// (subscription x associate x tutor relation x tutor).
func subscriptionReadsSmartCondition(search *domain.PersonaSearch, args *personaArgs) string {
	words := make([]string, len(search.Words))
	for i, word := range search.Words {
		parameter := args.add(word)
		terms := []string{}
		for _, field := range subscriptionReadsSearchFields {
			terms = append(terms, personaLike(field.column, parameter))
		}
		for _, variation := range search.Dates[i] {
			variationParameter := args.add(variation)
			for _, field := range subscriptionReadsSearchFields {
				if field.date {
					terms = append(terms, personaLike(field.column, variationParameter))
				}
			}
		}
		words[i] = "(" + strings.Join(terms, " OR ") + ")"
	}
	condition := "(" + strings.Join(words, " AND ") + ")"
	if len(search.Permutations) > 0 {
		terms := []string{}
		for _, permutation := range search.Permutations {
			parameter := args.add(permutation)
			for _, field := range subscriptionReadsSearchFields {
				if field.permuted {
					terms = append(terms, personaLike(field.column, parameter))
				}
			}
		}
		condition = "(" + condition + " OR " + strings.Join(terms, " OR ") + ")"
	}
	return condition
}

const subscriptionReadsSearchJoins = ` FROM (SELECT 1) d
 LEFT JOIN application_associate sa ON sa.associate_id=s.associate_id
 LEFT JOIN application_associatetutorrelation sr ON sr.associate_id=sa.associate_id
 LEFT JOIN application_associate st ON st.associate_id=sr.tutor_id`

func subscriptionReadsInts(values []int) string {
	parts := make([]string, len(values))
	for i, value := range values {
		parts[i] = strconv.Itoa(value)
	}
	return strings.Join(parts, ",")
}

// subscriptionReadsQuery is FROM ... WHERE ... of the filter (one row per subscription).
func subscriptionReadsQuery(filter domain.SubscriptionReadsFilter, args *personaArgs) string {
	from := ` FROM application_subscription s
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id`
	where := []string{"TRUE"}
	if filter.AssociationID != nil {
		where = append(where, "s.sport_association_id="+args.add(*filter.AssociationID)+"::uuid")
	}
	if filter.OwnerUser != nil {
		from += `
 JOIN application_sportassociation so ON so.sport_association_id=s.sport_association_id`
		where = append(where, "so.user_id="+args.add(*filter.OwnerUser)+"::uuid")
	}
	if filter.UserID != nil {
		where = append(where, "s.user_id="+args.add(*filter.UserID)+"::uuid")
	}
	if filter.AssociationKey != nil {
		where = append(where, "s.sport_association_id="+args.add(*filter.AssociationKey)+"::uuid")
	}
	if filter.AssociationNull || filter.Empty {
		where = append(where, "FALSE")
	}
	if filter.Archived != nil {
		where = append(where, "s.archived="+args.add(*filter.Archived))
	}
	if filter.Group != nil {
		where = append(where, "s.group_id="+args.add(*filter.Group)+"::uuid")
	}
	if filter.Scope != nil {
		where = append(where, "s.sport_association_id="+args.add(*filter.Scope)+"::uuid")
	}
	if len(filter.TagsAny) > 0 {
		where = append(where, "s.subscription_id IN (SELECT u0.subscription_id FROM application_subscription_tags u0 WHERE u0.tags_id = ANY(string_to_array("+
			args.add(strings.Join(filter.TagsAny, ","))+", ',')::uuid[]))")
	}
	for _, tag := range filter.TagsAll {
		where = append(where, "EXISTS(SELECT 1 FROM application_subscription_tags u0 WHERE u0.subscription_id=s.subscription_id AND u0.tags_id="+
			args.add(tag)+"::uuid)")
	}
	if filter.BornAtMost != nil {
		where = append(where, "a.born_date<="+args.add(filter.BornAtMost.Format("2006-01-02"))+"::date")
	}
	if filter.BornAfter != nil {
		where = append(where, "a.born_date>"+args.add(filter.BornAfter.Format("2006-01-02"))+"::date")
	}
	for _, value := range filter.ExcludeTypes {
		where = append(where, "s.type<>"+args.add(value))
	}
	if filter.Status != nil {
		where = append(where, "s.status_flag="+args.add(*filter.Status))
	}
	if filter.StatusIn != nil {
		values := make([]string, len(filter.StatusIn))
		for i, value := range filter.StatusIn {
			values[i] = strconv.FormatInt(value, 10)
		}
		where = append(where, "s.status_flag = ANY(string_to_array("+args.add(strings.Join(values, ","))+", ',')::bigint[])")
	}
	day := func(value time.Time) string { return args.add(value.Format("2006-01-02")) + "::date" }
	if filter.StartAfter != nil {
		where = append(where, "s.start_date>"+day(*filter.StartAfter))
	}
	if filter.EndBefore != nil {
		where = append(where, "s.end_date<"+day(*filter.EndBefore))
	}
	if filter.Current != nil {
		where = append(where, "s.start_date<="+day(*filter.Current)+" AND s.end_date>="+day(*filter.Current))
	}
	if filter.OverlapStart != nil && filter.OverlapEnd != nil {
		where = append(where, "s.start_date<="+day(*filter.OverlapEnd)+" AND s.end_date>="+day(*filter.OverlapStart))
	}
	if filter.EndContains != nil {
		where = append(where, personaLike("s.end_date::text", args.add(paymentReadsContains(*filter.EndContains))))
	}
	if filter.UserContains != nil {
		where = append(where, "EXISTS(SELECT 1 FROM bakney_user uu WHERE uu.user_id=s.user_id AND "+
			personaLike("uu.username::text", args.add(paymentReadsContains(*filter.UserContains)))+")")
	}
	if filter.Smart != nil && len(filter.Smart.Words) > 0 {
		where = append(where, "EXISTS(SELECT 1"+subscriptionReadsSearchJoins+" WHERE "+subscriptionReadsSmartCondition(filter.Smart, args)+")")
	}
	if filter.CustomContains != nil {
		where = append(where, personaLike("s.custom_data::text", args.add(paymentReadsContains(*filter.CustomContains))))
	}
	if filter.MedicalMissing {
		where = append(where, "s.medical_id IS NULL")
	}
	if filter.MedicalExpiredBy != nil {
		where = append(where, "EXISTS(SELECT 1 FROM application_medicalcertificate mm WHERE mm.medical_id=s.medical_id AND mm.expiration_date<="+
			day(*filter.MedicalExpiredBy)+")")
	}
	if filter.PaymentUnpaid {
		where = append(where, "EXISTS(SELECT 1 FROM application_payment pp WHERE pp.payment_id=s.payment_id AND NOT pp.paid)")
	}
	if filter.Types != nil {
		where = append(where, "s.type = ANY(string_to_array("+args.add(subscriptionReadsInts(filter.Types))+", ',')::int[])")
	}
	if filter.ExcludeCourse != nil {
		// NOT IN keeps SQL's NULL semantics: a live course subscription without subscription excludes every row.
		inner := "SELECT u0.subscription_id FROM application_coursesubscription u0 WHERE NOT u0.deleted AND u0.course_id=" + args.add(*filter.ExcludeCourse) + "::uuid"
		if filter.Scope != nil {
			scope := args.add(*filter.Scope)
			inner += " AND EXISTS(SELECT 1 FROM application_subscription u1 WHERE u1.subscription_id=u0.subscription_id AND u1.sport_association_id=" + scope +
				"::uuid) AND EXISTS(SELECT 1 FROM application_course u2 WHERE u2.course_id=u0.course_id AND u2.sport_association_id=" + scope + "::uuid)"
		}
		where = append(where, "NOT (s.subscription_id IN ("+inner+"))")
	}
	return from + "\n WHERE " + strings.Join(where, " AND ")
}

// subscriptionReadsColumns are the Subscription columns order_by() may name.
var subscriptionReadsColumns = map[string]bool{"subscription_id": true, "sport_association_id": true, "associate_id": true, "supplier_id": true,
	"instructor_id": true, "custom_data": true, "additional_fields": true, "user_id": true, "type": true, "role": true, "acceptance_date": true,
	"resignation_date": true, "medical_id": true, "status_flag": true, "creation_date": true, "start_date": true, "end_date": true,
	"signature_url": true, "signature_storage_key": true, "document_pdf_id": true, "payment_id": true, "archived": true, "draft": true, "trial": true,
	"meta": true, "subscription_number": true, "subscription_type": true, "group_id": true, "deleted": true, "competitive": true, "notes": true}

func subscriptionReadsOrder(order []domain.SubscriptionReadsOrder) (string, error) {
	if len(order) == 0 {
		return "", nil
	}
	terms := make([]string, len(order))
	for i, term := range order {
		switch {
		case term.Column == "associate.first_name" || term.Column == "associate.last_name" || term.Column == "associate.tax_code":
			terms[i] = "a." + strings.TrimPrefix(term.Column, "associate.")
		case term.Column == "age":
			terms[i] = "EXTRACT(YEAR FROM a.born_date)"
		case subscriptionReadsColumns[term.Column]:
			terms[i] = "s." + term.Column
		default:
			return "", domain.ErrSubscriptionReadsNotPorted
		}
		if term.Descending {
			terms[i] += " DESC"
		}
	}
	return " ORDER BY " + strings.Join(terms, ", "), nil
}

func (r *SubscriptionReads) CountSubscriptions(ctx context.Context, filter domain.SubscriptionReadsFilter) (int64, error) {
	args := personaArgs{}
	body := subscriptionReadsQuery(filter, &args)
	var count int64
	err := r.db.GetContext(ctx, &count, "SELECT COUNT(*)"+body, args...)
	return count, err
}

// Subscriptions are the page's keys in the filter's order (limit < 0: all).
func (r *SubscriptionReads) Subscriptions(ctx context.Context, filter domain.SubscriptionReadsFilter, limit, offset int64) ([]string, error) {
	args := personaArgs{}
	statement := "SELECT s.subscription_id" + subscriptionReadsQuery(filter, &args)
	order, err := subscriptionReadsOrder(filter.Order)
	if err != nil {
		return nil, err
	}
	statement += order
	if limit >= 0 {
		statement += " LIMIT " + args.add(limit) + " OFFSET " + args.add(offset)
	}
	ids := []string{}
	err = r.db.SelectContext(ctx, &ids, statement, args...)
	return ids, err
}

const subscriptionReadsRowColumns = `s.subscription_id, s.sport_association_id, s.associate_id, s.supplier_id, s.instructor_id,
 s.custom_data::text AS custom_data, s.additional_fields::text AS additional_fields, s.user_id, s.type, s.role, s.acceptance_date,
 s.resignation_date, s.medical_id, s.status_flag, s.creation_date, s.start_date, s.end_date, s.signature_url, s.signature_storage_key,
 s.document_pdf_id, s.payment_id, s.archived, s.draft, s.trial, s.meta::text AS meta, s.subscription_number, s.subscription_type, s.group_id,
 s.deleted, s.competitive, s.notes,
 u.username AS user_username, u.email AS user_email, u.first_name AS user_first_name, u.last_name AS user_last_name, u.phone AS user_phone,
 u.role AS user_role, m.expiration_date AS medical_expiration_date, m.competitive_medical_certificate AS medical_competitive,
 m.document_id AS medical_document_id, md.token::text AS medical_token, dp.token::text AS document_token, p.amount::text AS payment_amount,
 p.paid AS payment_paid, p.payment_date AS payment_payment_date,
 COALESCE((SELECT json_agg(json_build_object('tag_id', t.tag_id, 'tag_name', t.tag_name)) FROM application_subscription_tags st
   JOIN application_tags t ON t.tag_id=st.tags_id WHERE st.subscription_id=s.subscription_id), '[]'::json)::text AS tags`

const subscriptionReadsRowJoins = ` FROM application_subscription s
 JOIN bakney_user u ON u.user_id=s.user_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 LEFT JOIN application_medicalcertificate m ON m.medical_id=s.medical_id
 LEFT JOIN docmanager_document md ON md.document_id=m.document_id
 LEFT JOIN docmanager_document dp ON dp.document_id=s.document_pdf_id
 LEFT JOIN application_payment p ON p.payment_id=s.payment_id`

// subscriptionReadsRenewal is future_renewal_exists(): the candidates go through the group-aware manager.
const subscriptionReadsRenewal = `EXISTS(SELECT 1 FROM application_subscription u0 JOIN application_associate u1 ON u1.associate_id=u0.associate_id
 WHERE NOT u0.archived AND UPPER(u1.tax_code::text)=UPPER(a.tax_code) AND u0.sport_association_id=s.sport_association_id
 AND u0.start_date>=s.end_date AND ($2::uuid IS NULL OR u0.group_id=$2::uuid)) AS future_renewal`

func (r *SubscriptionReads) associates(ctx context.Context, ids []string) (map[string]domain.PersonaAssociate, error) {
	rows := []domain.PersonaAssociate{}
	result := map[string]domain.PersonaAssociate{}
	if len(ids) == 0 {
		return result, nil
	}
	if err := r.db.SelectContext(ctx, &rows, `SELECT `+personaColumns("a")+` FROM application_associate a
 WHERE a.associate_id = ANY(string_to_array($1, ',')::uuid[])`, strings.Join(ids, ",")); err != nil {
		return nil, err
	}
	for _, row := range rows {
		result[row.ID] = row
	}
	return result, nil
}

func (r *SubscriptionReads) Render(ctx context.Context, ids []string, group *string) (map[string]domain.SubscriptionReadsRow, error) {
	result := map[string]domain.SubscriptionReadsRow{}
	if len(ids) == 0 {
		return result, nil
	}
	rows := []domain.SubscriptionReadsRow{}
	if err := r.db.SelectContext(ctx, &rows, `SELECT `+subscriptionReadsRowColumns+`, `+subscriptionReadsRenewal+subscriptionReadsRowJoins+`
 WHERE s.subscription_id = ANY(string_to_array($1, ',')::uuid[])`, strings.Join(ids, ","), group); err != nil {
		return nil, err
	}
	people := []string{}
	for _, row := range rows {
		if row.AssociateID != nil {
			people = append(people, *row.AssociateID)
		}
	}
	associates, err := r.associates(ctx, people)
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		if row.AssociateID != nil {
			if associate, ok := associates[*row.AssociateID]; ok {
				row.Associate = &associate
			}
		}
		result[row.ID] = row
	}
	return result, nil
}

// Payments sums Payment.objects (live, group-aware) .filter(archived=False) of the associate.
func (r *SubscriptionReads) Payments(ctx context.Context, associate *string, group *string) (domain.SubscriptionReadsPayments, error) {
	var row domain.SubscriptionReadsPayments
	err := r.db.GetContext(ctx, &row, `SELECT COUNT(*) AS count, COUNT(*) FILTER (WHERE NOT paid) AS unpaid,
 COALESCE(SUM(amount), 0)::text AS total, COALESCE(SUM(amount) FILTER (WHERE NOT paid), 0)::text AS to_pay
 FROM application_payment WHERE NOT deleted AND NOT archived AND ($2::uuid IS NULL OR group_id=$2::uuid)
 AND (($1::uuid IS NULL AND associate_id IS NULL) OR associate_id=$1::uuid)`, associate, group)
	return row, err
}

func (r *SubscriptionReads) NullTaxRenewal(ctx context.Context, association string, end time.Time, group *string) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription u0
 LEFT JOIN application_associate u1 ON u1.associate_id=u0.associate_id
 WHERE u0.sport_association_id=$1 AND u0.start_date>=$2::date AND NOT u0.archived AND u1.tax_code IS NULL
 AND ($3::uuid IS NULL OR u0.group_id=$3::uuid))`, association, end.Format("2006-01-02"), group)
	return found, err
}

// subscriptionReadsFieldExclusion is the field mode's exclude(): no tutor with
// email and phone (four separate EXISTS) while the associate's own email or
// phone is empty or null.
const subscriptionReadsFieldExclusion = `NOT (NOT (
 EXISTS(SELECT 1 FROM application_associatetutorrelation u2 JOIN application_associate u3 ON u2.tutor_id=u3.associate_id WHERE u3.email>'' AND u2.associate_id=s.associate_id)
 AND EXISTS(SELECT 1 FROM application_associatetutorrelation u2 JOIN application_associate u3 ON u2.tutor_id=u3.associate_id WHERE u3.email IS NOT NULL AND u2.associate_id=s.associate_id)
 AND EXISTS(SELECT 1 FROM application_associatetutorrelation u2 JOIN application_associate u3 ON u2.tutor_id=u3.associate_id WHERE u3.phone>'' AND u2.associate_id=s.associate_id)
 AND EXISTS(SELECT 1 FROM application_associatetutorrelation u2 JOIN application_associate u3 ON u2.tutor_id=u3.associate_id WHERE u3.phone IS NOT NULL AND u2.associate_id=s.associate_id))
 AND ((a.email='' AND a.email IS NOT NULL) OR a.email IS NULL OR (a.phone='' AND a.phone IS NOT NULL) OR a.phone IS NULL))`

// FieldValues is values_list('associate__<column>', flat=True).distinct() as
// JSON texts (timestamp: the UTC timestamp text). The column is validated by the service.
func (r *SubscriptionReads) FieldValues(ctx context.Context, filter domain.SubscriptionReadsFilter, column string, timestamp bool) ([]*string, error) {
	args := personaArgs{}
	body := subscriptionReadsQuery(filter, &args)
	value := "to_jsonb(a." + column + ")::text"
	if timestamp {
		value = "to_jsonb(a." + column + " AT TIME ZONE 'UTC')::text"
	}
	rows := []sql.NullString{}
	if err := r.db.SelectContext(ctx, &rows, "SELECT "+value+" FROM (SELECT DISTINCT a."+column+body+" AND "+subscriptionReadsFieldExclusion+") a",
		args...); err != nil {
		return nil, err
	}
	values := make([]*string, len(rows))
	for i, row := range rows {
		if row.Valid {
			text := row.String
			values[i] = &text
		}
	}
	return values, nil
}

func (r *SubscriptionReads) AllowedForAthlete(ctx context.Context, user string, group, scope *string) ([]string, error) {
	ids := []string{}
	err := r.db.SelectContext(ctx, &ids, `SELECT s.subscription_id FROM application_subscription s
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE ($2::uuid IS NULL OR s.group_id=$2::uuid) AND ($3::uuid IS NULL OR s.sport_association_id=$3::uuid)
 AND (s.user_id=$1 OR a.tax_code IN (SELECT ta.tax_code FROM application_subscription ts JOIN application_associate ta ON ta.associate_id=ts.associate_id
   WHERE ts.user_id=$1 AND `+familyTaxCodeFilled("ta")+` AND ($2::uuid IS NULL OR ts.group_id=$2::uuid) AND ($3::uuid IS NULL OR ts.sport_association_id=$3::uuid)))`,
		user, group, scope)
	return ids, err
}

func (r *SubscriptionReads) Subscription(ctx context.Context, id string, group, scope *string, user *string, association *string) (*domain.SubscriptionReadsRow, error) {
	ids := []string{}
	err := r.db.SelectContext(ctx, &ids, `SELECT s.subscription_id FROM application_subscription s WHERE s.subscription_id=$1
 AND ($2::uuid IS NULL OR s.group_id=$2::uuid) AND ($3::uuid IS NULL OR s.sport_association_id=$3::uuid)
 AND ($4::uuid IS NULL OR s.user_id=$4::uuid) AND ($5::uuid IS NULL OR s.sport_association_id=$5::uuid)`, id, group, scope, user, association)
	if err != nil || len(ids) == 0 {
		return nil, err
	}
	rows, err := r.Render(ctx, ids, group)
	if err != nil {
		return nil, err
	}
	row, ok := rows[ids[0]]
	if !ok {
		return nil, nil
	}
	return &row, nil
}

// subscriptionReadsCourseColumns select the course (alias c) as a
// CourseOverviewRow with the tags and locations M2M as primary-key arrays.
const subscriptionReadsCourseColumns = `c.course_id AS "course.course_id", c.status_flag AS "course.status_flag", c.course_type AS "course.course_type",
 c.creation_date AS "course.creation_date", c.title AS "course.title", c.description AS "course.description",
 c.multi_payments AS "course.multi_payments", c.fee::text AS "course.fee", c.events::text AS "course.events",
 c.one_fee_payment AS "course.one_fee_payment", c.one_fee::text AS "course.one_fee", c.google_calendar_id AS "course.google_calendar_id",
 c.google_background_color AS "course.google_background_color", c.multiple_quotes::text AS "course.multiple_quotes",
 c.start_date AS "course.start_date", c.end_date AS "course.end_date", c.pinned AS "course.pinned",
 c.billed_duration_is_sport_season AS "course.billed_duration_is_sport_season", c.billed_frequency AS "course.billed_frequency",
 c.billed_from_subscription_date AS "course.billed_from_subscription_date", c.billed_from_day_of_month AS "course.billed_from_day_of_month",
 c.auto_renewal AS "course.auto_renewal", c.deleted AS "course.deleted", c.sport_association_id AS "course.sport_association_id",
 c.group_id AS "course.group_id",
 COALESCE((SELECT json_agg(ct.coursetags_id) FROM application_course_tags ct WHERE ct.course_id=c.course_id), '[]'::json)::text AS "course.tags",
 COALESCE((SELECT json_agg(cl.courselocation_id) FROM application_course_locations cl WHERE cl.course_id=c.course_id), '[]'::json)::text AS "course.locations"`

func (r *SubscriptionReads) Courses(ctx context.Context, subscription string, scope *string) ([]domain.SubscriptionReadsCourse, error) {
	rows := []domain.SubscriptionReadsCourse{}
	err := r.db.SelectContext(ctx, &rows, `SELECT `+courseSubscriptionColumns+`, `+subscriptionReadsCourseColumns+`
 FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 LEFT JOIN application_subscription s ON s.subscription_id=cs.subscription_id
 WHERE cs.subscription_id=$1 AND NOT cs.deleted AND ($2::uuid IS NULL OR (s.sport_association_id=$2::uuid AND c.sport_association_id=$2::uuid))`,
		subscription, scope)
	return rows, err
}

func (r *SubscriptionReads) Installments(ctx context.Context, courseSubscriptions []string, group, scope *string) ([]domain.SubscriptionReadsInstallment, error) {
	rows := []domain.SubscriptionReadsInstallment{}
	if len(courseSubscriptions) == 0 {
		return rows, nil
	}
	err := r.db.SelectContext(ctx, &rows, `SELECT i.course_subscription_installment_id, i.course_subscription_id, i.payment_id, i.amount::text AS amount,
 i.payment_date, i.paid, p.paid AS payment_paid FROM application_coursesubscriptioninstallment i
 LEFT JOIN application_payment p ON p.payment_id=i.payment_id
 JOIN application_coursesubscription cs ON cs.course_subscription_id=i.course_subscription_id
 JOIN application_course c ON c.course_id=cs.course_id
 LEFT JOIN application_subscription s ON s.subscription_id=cs.subscription_id
 WHERE i.course_subscription_id = ANY(string_to_array($1, ',')::uuid[]) AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR (s.sport_association_id=$3::uuid AND c.sport_association_id=$3::uuid))
 ORDER BY i.course_subscription_installment_id`, strings.Join(courseSubscriptions, ","), group, scope)
	return rows, err
}

func (r *SubscriptionReads) Files(ctx context.Context, subscription string, scope *string) ([]domain.SubscriptionReadsFile, error) {
	rows := []domain.SubscriptionReadsFile{}
	err := r.db.SelectContext(ctx, &rows, `SELECT f.subscription_file_id, d.document_id, d.filename, d.token::text AS token, f.creation_date
 FROM application_subscriptionfile f JOIN docmanager_document d ON d.document_id=f.document_id
 JOIN application_subscription s ON s.subscription_id=f.subscription_id
 WHERE f.subscription_id=$1 AND ($2::uuid IS NULL OR s.sport_association_id=$2::uuid) ORDER BY f.subscription_file_id`, subscription, scope)
	return rows, err
}

func (r *SubscriptionReads) Card(ctx context.Context, association string) (*domain.SubscriptionReadsCard, error) {
	var row domain.SubscriptionReadsCard
	err := r.db.GetContext(ctx, &row, `SELECT emit_only_on_approval, customized_template::text AS customized_template
 FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1
 ORDER BY sport_association_membership_card_configuration_id LIMIT 1`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// Persona is the associate (forward relation) with is_tutor as the relation lookup.
func (r *SubscriptionReads) Persona(ctx context.Context, associate string) (*domain.PersonaListed, error) {
	var row domain.PersonaListed
	err := r.db.GetContext(ctx, &row, `SELECT `+personaColumns("a")+`, `+personaIsTutor+` AS is_tutor_annotated
 FROM application_associate a WHERE a.associate_id=$1`, associate)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (r *SubscriptionReads) Relations(ctx context.Context, associates []string) ([]domain.PersonaRelation, error) {
	return r.personas.Relations(ctx, associates)
}

func (r *SubscriptionReads) Tutors(ctx context.Context, associates []string, group *string) ([]domain.PersonaRelation, error) {
	return r.personas.Tutors(ctx, associates, group)
}

func (r *SubscriptionReads) FamilyMembers(ctx context.Context, family, self string, group *string) ([]string, error) {
	return r.personas.FamilyMembers(ctx, family, self, group)
}

// Registries are the attendance registries of the subscription's live course subscriptions' courses.
func (r *SubscriptionReads) Registries(ctx context.Context, subscription string, scope *string) ([]domain.SubscriptionReadsRegistry, error) {
	rows := []domain.SubscriptionReadsRegistry{}
	err := r.db.SelectContext(ctx, &rows, `SELECT ar.events::text AS events, rc.title AS course_title FROM application_attendanceregistry ar
 JOIN application_course rc ON rc.course_id=ar.course_id
 WHERE ($2::uuid IS NULL OR rc.sport_association_id=$2::uuid) AND ar.course_id IN (
   SELECT cs.course_id FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
   LEFT JOIN application_subscription s ON s.subscription_id=cs.subscription_id
   WHERE cs.subscription_id=$1 AND NOT cs.deleted AND ($2::uuid IS NULL OR (s.sport_association_id=$2::uuid AND c.sport_association_id=$2::uuid)))
 ORDER BY ar.attendance_registry_id`, subscription, scope)
	return rows, err
}

func (r *SubscriptionReads) BalanceStart(ctx context.Context, user string) (int, int, error) {
	var row struct {
		Day   int `db:"balance_sheet_start_day"`
		Month int `db:"balance_sheet_start_month"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT balance_sheet_start_day, balance_sheet_start_month FROM bakney_user WHERE user_id=$1`, user)
	return row.Day, row.Month, err
}

// ExportTags is prefetch_related('tags') as current_view_subscriptions runs
// it: Django's statement, without ORDER BY, so each subscription keeps the
// order the database returns (subscription-reads.md, "Excel export").
func (r *SubscriptionReads) ExportTags(ctx context.Context, ids []string) ([]domain.SubscriptionReadsExportTag, error) {
	rows := []domain.SubscriptionReadsExportTag{}
	if len(ids) == 0 {
		return rows, nil
	}
	found := []struct {
		SubscriptionID string    `db:"subscription_id"`
		ID             string    `db:"tag_id"`
		Name           string    `db:"tag_name"`
		AssociationID  string    `db:"sport_association_id"`
		CreationDate   time.Time `db:"creation_date"`
	}{}
	err := r.db.SelectContext(ctx, &found, `SELECT ("application_subscription_tags"."subscription_id") AS "subscription_id",
 "application_tags"."tag_id", "application_tags"."tag_name", "application_tags"."sport_association_id",
 "application_tags"."creation_date" FROM "application_tags" INNER JOIN "application_subscription_tags"
 ON ("application_tags"."tag_id" = "application_subscription_tags"."tags_id")
 WHERE "application_subscription_tags"."subscription_id" = ANY($1::uuid[])`, ids)
	for _, row := range found {
		rows = append(rows, domain.SubscriptionReadsExportTag{SubscriptionID: row.SubscriptionID, Name: row.Name})
	}
	return rows, err
}

// AssociationDenomination is sport_association.denomination.
func (r *SubscriptionReads) AssociationDenomination(ctx context.Context, association string) (*string, error) {
	var denomination *string
	err := r.db.GetContext(ctx, &denomination, `SELECT denomination FROM application_sportassociation WHERE sport_association_id=$1`, association)
	return denomination, err
}
