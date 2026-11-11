package domain

import (
	"context"
	"errors"
	"time"
)

// ErrPersonaNotFound is get_object_or_404's miss: "No Associate matches the given query.".
var ErrPersonaNotFound = errors.New("associate not found")

// PersonaAssociate is one application_associate row with every concrete field
// AssociateSerializer and its nested serializers render.
type PersonaAssociate struct {
	ID                   string     `db:"associate_id"`
	UserID               *string    `db:"user_id"`
	AssociationID        *string    `db:"sport_association_id"`
	PicturePath          *string    `db:"picture_path"`
	FirstName            *string    `db:"first_name"`
	LastName             *string    `db:"last_name"`
	Sex                  string     `db:"sex"`
	TaxCode              *string    `db:"tax_code"`
	BornDate             *time.Time `db:"born_date"`
	BornCity             *string    `db:"born_city"`
	AddressCity          *string    `db:"address_city"`
	Address              *string    `db:"address"`
	AddressCap           *string    `db:"address_cap"`
	Nationality          *string    `db:"nationality"`
	NationalityResidence *string    `db:"nationality_residence"`
	Email                *string    `db:"email"`
	Phone                *string    `db:"phone"`
	Phone2               *string    `db:"phone_2"`
	Phone3               *string    `db:"phone_3"`
	Phone4               *string    `db:"phone_4"`
	PhoneLabel           *string    `db:"phone_label"`
	Phone2Label          *string    `db:"phone_2_label"`
	Phone3Label          *string    `db:"phone_3_label"`
	Phone4Label          *string    `db:"phone_4_label"`
	IsMinor              bool       `db:"is_minor"`
	Disabled             bool       `db:"disabled"`
	Draft                bool       `db:"draft"`
	GroupID              *string    `db:"group_id"`
	Deleted              bool       `db:"deleted"`
	FamilyID             *string    `db:"family_id"`
	FamilyRole           *string    `db:"family_role"`
	IDNumber             *string    `db:"id_number"`
	CreationDate         time.Time  `db:"creation_date"`
	Migrated             bool       `db:"migrated"`
	Notes                *string    `db:"notes"`
}

// PersonaListed is a row of AssociateViewSet.get_queryset with its annotations.
type PersonaListed struct {
	PersonaAssociate
	IsTutor bool `db:"is_tutor_annotated"`
}

// PersonaRelation is an AssociateTutorRelation with its tutor (base manager).
type PersonaRelation struct {
	AssociateID string `db:"relation_associate_id"`
	IsPrimary   bool   `db:"relation_is_primary"`
	PersonaAssociate
}

// PersonaSearch is sqlite_search's prepared input: escaped LIKE patterns.
type PersonaSearch struct {
	// Term is the stripped, lowercased search term (iexact operand).
	Term string
	// Prefix and Contains are Term escaped for istartswith/icontains.
	Prefix, Contains string
	// Words are the icontains patterns per \w+ word; Dates (per word) the date-variation patterns of numeric words.
	Words []string
	Dates [][]string
	// Permutations are the icontains patterns of the reversed and original word sequence (more than one word).
	Permutations []string
}

// PersonaOrder is one order_by() term: a column key, or random.
type PersonaOrder struct {
	Key        string
	Descending bool
	Random     bool
}

// PersonaQuery is AssociateViewSet.get_queryset's filters.
type PersonaQuery struct {
	AssociationID string
	GroupID       *string
	Search        *PersonaSearch
	// IsTutor holds one is_tutor_annotated condition per filter() call (query[is_tutor], query[type]).
	IsTutor        []bool
	BornOnOrBefore *time.Time
	BornOnOrAfter  *time.Time
	Order          *PersonaOrder
	// ID restricts to one primary key (get_object).
	ID *string
}

// PersonaSubscription is one all_with_subscriptions values() row.
type PersonaSubscription struct {
	SubscriptionID string  `db:"subscription_id"`
	StatusFlag     int64   `db:"status_flag"`
	Type           int64   `db:"type"`
	AssociateID    string  `db:"associate_id"`
	FirstName      *string `db:"first_name"`
	LastName       *string `db:"last_name"`
	Email          *string `db:"email"`
	TaxCode        *string `db:"tax_code"`
}

// PersonaName is a values('associate_id', 'first_name', 'last_name') row.
type PersonaName struct {
	ID        string  `db:"associate_id"`
	FirstName *string `db:"first_name"`
	LastName  *string `db:"last_name"`
}

type PersonasStore interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	CountPersonas(ctx context.Context, query PersonaQuery) (int64, error)
	Personas(ctx context.Context, query PersonaQuery, limit, offset int64) ([]PersonaListed, error)
	// Relations are the tutor relations of the associates with their tutors (base managers, unordered).
	Relations(ctx context.Context, associates []string) ([]PersonaRelation, error)
	// Tutors are associate.tutors.all() per associate: the M2M manager (live, group-aware).
	Tutors(ctx context.Context, associates []string, group *string) ([]PersonaRelation, error)
	FamilyMembers(ctx context.Context, family, self string, group *string) ([]string, error)
	PersonaNames(ctx context.Context, association string, group *string) ([]PersonaName, error)
	CurrentSubscriptions(ctx context.Context, association string, group *string, today time.Time) ([]PersonaSubscription, error)
	// PersonaByID reads one row with its is_tutor annotation in any state (an
	// instance the serializer renders without a queryset); nil when absent.
	PersonaByID(ctx context.Context, id string) (*PersonaListed, error)
}

// PersonaOrderFields are the single names order_by() resolves on this queryset
// to a column or annotation: concrete fields, attnames, forward relations (no
// related model ordering), pk and the annotations. Reverse accessors, the
// tutors M2M, relation paths and the identity document number are unknown
// orderings (FieldError, 500).
var PersonaOrderFields = []string{"pk", "is_tutor_annotated", "tutors_count", "associate_id", "picture_path", "first_name",
	"last_name", "sex", "tax_code", "born_date", "born_city", "address_city", "address", "address_cap", "nationality",
	"nationality_residence", "email", "phone", "phone_2", "phone_3", "phone_4", "phone_label", "phone_2_label", "phone_3_label",
	"phone_4_label", "is_minor", "disabled", "draft", "deleted", "family_role", "creation_date", "migrated", "notes",
	"user", "user_id", "sport_association", "sport_association_id", "group", "group_id", "family", "family_id"}

// PersonaOrderable reports whether order_by(name) resolves in this port
// ('relevance' exists only with a search).
func PersonaOrderable(name string, searched bool) bool {
	if name == "relevance" {
		return searched
	}
	for _, field := range PersonaOrderFields {
		if field == name {
			return true
		}
	}
	return false
}
