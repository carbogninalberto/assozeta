package service

import (
	"context"
	"errors"
	"fmt"
	"math"
	"math/big"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
)

// subscriptionExistsLike is connection.ops.prep_for_like_query of the icontains
// lookup: psycopg2 quotes the pattern after this replacement.
var subscriptionExistsLike = strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)

// subscriptionExistsStatement is the text psycopg2 sends for Subscription.objects.subscription_exists(...).exists().
// names are the raw initial_data values of first_name, last_name, tax_code (any parsed JSON value): IExact does not
// prepare its value, so psycopg2 writes it as its literal.
func subscriptionExistsStatement(association string, names []any, start, end domain.CalendarDate, custom *string,
	group *string) (string, error) {
	if len(names) != 3 {
		return "", domain.ErrSubscriptionCreateUnreachable
	}
	// The literal errors of the names come in the query's order (first_name,
	// last_name, tax_code), then the custom pattern.
	clauses := make([]string, 3)
	for i, column := range []string{"first_name", "last_name", "tax_code"} {
		clause, err := subscriptionExistsClause(column, names[i])
		if err != nil {
			return "", err
		}
		clauses[i] = clause
	}
	var pattern string
	if custom != nil {
		literal, err := dto.Psycopg2Literal("%" + subscriptionExistsLike.Replace(*custom) + "%")
		if err != nil {
			return "", err
		}
		pattern = literal
	}
	statement := `SELECT 1 AS "a" FROM "application_subscription" INNER JOIN "application_associate" ON (` +
		`"application_subscription"."associate_id" = "application_associate"."associate_id") WHERE (`
	if group != nil {
		statement += `"application_subscription"."group_id" = '` + uuid.MustParse(*group).String() + `'::uuid AND `
	}
	statement += `NOT "application_subscription"."archived" AND ` + clauses[0] + ` AND ` + clauses[1] + ` AND ` + clauses[2] +
		` AND "application_subscription"."end_date" >= '` + subscriptionExistsDate(start) + `'::date AND ` +
		`"application_subscription"."sport_association_id" = '` + association + `'::uuid AND ` +
		`"application_subscription"."start_date" <= '` + subscriptionExistsDate(end) + `'::date`
	if custom != nil {
		statement += ` AND UPPER("application_subscription"."custom_data"::text) LIKE UPPER(` + pattern + `)`
	}
	return statement + `) LIMIT 1`, nil
}

// subscriptionExistsClause is one iexact lookup: None is IS NULL, anything else
// reaches UPPER(<literal>) as psycopg2 wrote it (no cast: PostgreSQL has no
// upper() for a number, a boolean or an array).
func subscriptionExistsClause(column string, value any) (string, error) {
	quoted := `"application_associate"."` + column + `"`
	if value == nil {
		return quoted + ` IS NULL`, nil
	}
	literal, err := dto.Psycopg2Literal(value)
	if err != nil {
		return "", err
	}
	return `UPPER(` + quoted + `::text) = UPPER(` + literal + `)`, nil
}

// subscriptionExistsDate is the date literal Django sends for a CalendarDate.
func subscriptionExistsDate(value domain.CalendarDate) string {
	return fmt.Sprintf("%04d-%02d-%02d", value.Year, value.Month, value.Day)
}

// subscriptionCreateHasNUL reports a raw name or the custom kind psycopg2 cannot
// write as a literal (a NUL character fails in the client, before sending).
func subscriptionCreateHasNUL(raw []any, custom *string) bool {
	for _, value := range raw {
		if text, ok := value.(string); ok && strings.ContainsRune(text, 0) {
			return true
		}
	}
	return custom != nil && strings.ContainsRune(*custom, 0)
}

// existsLiteral reproduces check_if_subscription_exists' statement on the raw
// names: psycopg2 writes every parameter as a literal, so a dict or a NUL
// character raises in the client and PostgreSQL may refuse the text (the
// ProgrammingError). The statement runs in a savepoint; its answer is the
// exists() query's. It returns the names get_subscription_if_it_exists compares
// with (the literal text of the values that were no text).
func (s *SubscriptionCreates) existsLiteral(ctx context.Context, tx domain.SubscriptionCreateTransaction, association string,
	raw []any, start, end domain.CalendarDate, custom *string, group *string) ([]*string, error) {
	statement, err := subscriptionExistsStatement(association, raw, start, end, custom, group)
	if err != nil {
		var adapt *dto.Psycopg2AdaptError
		if errors.As(err, &adapt) {
			return nil, raisedFailure(adapt.Text)
		}
		return nil, err
	}
	if _, err = tx.ExistsStatement(ctx, statement); err != nil {
		var server *pgconn.PgError
		if errors.As(err, &server) {
			return nil, raisedFailure(dto.Psycopg2ServerErrorText(statement, server.Message, server.Position, server.Detail,
				server.Hint, server.InternalQuery, server.Where))
		}
		return nil, err
	}
	names := make([]*string, 3)
	for i, value := range raw {
		switch v := value.(type) {
		case nil:
			names[i] = nil
		case string:
			text := v
			names[i] = &text
		default:
			// A value UPPER() accepted is its quoted literal: the array text of a
			// list of NULLs ('{}', '{NULL}'), which is what the next lookup compares.
			literal, err := dto.Psycopg2Literal(value)
			if err != nil {
				return nil, err
			}
			if len(literal) < 2 || !strings.HasPrefix(literal, "'") || !strings.HasSuffix(literal, "'") {
				return nil, domain.ErrSubscriptionCreateUnreachable
			}
			text := strings.ReplaceAll(literal[1:len(literal)-1], "''", "'")
			names[i] = &text
		}
	}
	return names, nil
}

// subscriptionDecimalInvalid is str() of DecimalField.to_python's ValidationError
// for a float that is not a decimal (NaN or an infinity).
func subscriptionDecimalInvalid(value float64) string {
	return "['“" + dto.PythonFloatStr(value) + "” value must be a decimal number.']"
}

// subscriptionInt64 is create_subscription's int of any size as the fee rules
// compare it: a value outside int64 matches none of them, like the 0 sentinel
// (get_subscription_fee only compares against 1 and 2).
func subscriptionInt64(parsed *big.Int) int64 {
	if parsed.IsInt64() {
		return parsed.Int64()
	}
	return 0
}

// subscriptionSmallint is application_subscription's type and role columns:
// psycopg2's DataError ("smallint out of range") is the INSERT's answer for a
// value outside them.
func subscriptionSmallint(parsed *big.Int) bool {
	return parsed.Cmp(big.NewInt(math.MinInt16)) >= 0 && parsed.Cmp(big.NewInt(math.MaxInt16)) <= 0
}

// subscriptionFeeIsZero is get_membership_fee's `membership_fee == 0` on the
// stored numeric: no fee is a zero fee, while Decimal('NaN') == 0 is False (and
// big.Float cannot parse it).
func subscriptionFeeIsZero(fee *string) bool {
	if fee == nil {
		return true
	}
	value, parsed := new(big.Float).SetString(*fee)
	return parsed && value.Sign() == 0
}
