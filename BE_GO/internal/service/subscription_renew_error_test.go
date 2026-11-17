package service

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestRenewErrorTexts pins subscription_renew's except Exception branch: a
// Python exception whose str() the port carries is the view's 400 body, while
// an exception without one keeps the answer it had.
func TestRenewErrorTexts(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want string
	}{
		{name: "create failure", err: &domain.SubscriptionCreateFailure{Text: "String does not contain a date: "},
			want: "String does not contain a date: "},
		{name: "create raised", err: &domain.SubscriptionCreateRaised{Text: "Day must be between 1 and 31"},
			want: "Day must be between 1 and 31"},
		{name: "course raised", err: &domain.CourseSubscriptionRaised{Text: "'NoneType' object is not iterable"},
			want: "'NoneType' object is not iterable"},
		{name: "wrapped create raised", err: errors.Join(errors.New("create"), &domain.SubscriptionCreateRaised{Text: "boom"}),
			want: "boom"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			got := renewError(test.err)
			var failure *SubscriptionRenewFailure
			if !errors.As(got, &failure) || failure.Text != test.want {
				t.Fatalf("got %v, want the 400 body %q", got, test.want)
			}
		})
	}
	t.Run("plain server keeps its answer", func(t *testing.T) {
		err := renewError(domain.ErrSubscriptionCreateServer)
		if !errors.Is(err, domain.ErrSubscriptionCreateServer) || errors.As(err, new(*SubscriptionRenewFailure)) {
			t.Fatalf("got %v, want the untexted server error", err)
		}
	})
	t.Run("unrelated error", func(t *testing.T) {
		original := errors.New("connection reset")
		if got := renewError(original); got != original {
			t.Fatalf("got %v, want the original error", got)
		}
	})
}

type renewErrorCategoryTransaction struct {
	domain.SubscriptionCreateTransaction
}

func (t *renewErrorCategoryTransaction) Category(context.Context, *string, string) (*string, error) {
	return nil, nil
}

type associationUserTransaction struct {
	domain.SubscriptionCreateTransaction
	settingsUser string
	memberUser   string
	count        int
	memberErr    error
}

func (t *associationUserTransaction) Settings(_ context.Context, user string) (*domain.SubscriptionCreateSettings, int, error) {
	t.settingsUser = user
	return &domain.SubscriptionCreateSettings{AssociationID: "a1000000-0000-4000-8000-000000999001", OwnerID: user}, t.count, nil
}

func (t *associationUserTransaction) Member(_ context.Context, id string) (*domain.SubscriptionCreateMember, error) {
	t.memberUser = id
	if t.memberErr != nil {
		return nil, t.memberErr
	}
	return &domain.SubscriptionCreateMember{ID: id, Role: domain.RoleCollaborator, StartDay: 1, StartMonth: 1, EndDay: 31, EndMonth: 12}, nil
}

// TestCreateSubscriptionReadsTheConnectedAssociation pins User.sport_association
// for a collaborator: the association settings come from connected_user, while
// the registration's own season settings stay the collaborator's.
func TestCreateSubscriptionReadsTheConnectedAssociation(t *testing.T) {
	creates := &SubscriptionCreates{now: func() time.Time { return time.Date(2026, 11, 12, 9, 0, 0, 0, time.UTC) }}
	identity := domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: "10000000-0000-4000-8000-000000999002", Role: domain.RoleCollaborator}}
	body := domain.RequestObject{
		{Name: "associate_data", Value: domain.RequestObject{{Name: "born_date", Value: "01/01/1990"}, {Name: "sex", Value: "M"}}},
		{Name: "new_user_account", Value: domain.RequestObject{}},
		{Name: "signature", Value: domain.RequestObject{}},
	}
	t.Run("association settings", func(t *testing.T) {
		tx := &associationUserTransaction{count: 0}
		_, err := creates.createSubscription(context.Background(), tx, identity, body,
			SubscriptionCreateRequest{AssociationUserID: "10000000-0000-4000-8000-000000999003"})
		if tx.settingsUser != "10000000-0000-4000-8000-000000999003" {
			t.Fatalf("settings read %q, want the connected account's association", tx.settingsUser)
		}
		var failure *domain.SubscriptionCreateFailure
		if !errors.As(err, &failure) || failure.Text != "SportAssociation matching query does not exist." {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("own season settings", func(t *testing.T) {
		stop := errors.New("stop after the member read")
		tx := &associationUserTransaction{count: 1, memberErr: stop}
		_, err := creates.createSubscription(context.Background(), tx, identity, body,
			SubscriptionCreateRequest{AssociationUserID: "10000000-0000-4000-8000-000000999003"})
		if !errors.Is(err, stop) {
			t.Fatalf("got %v", err)
		}
		if tx.memberUser != identity.ScopeUser.ID {
			t.Fatalf("member settings read %q, want the collaborator %q", tx.memberUser, identity.ScopeUser.ID)
		}
	})
}

// TestSubscriptionCreateRaisedTexts pins the Python str() of every exception
// this port reconstructs for subscription/renew (verified in CPython 3.13).
func TestSubscriptionCreateRaisedTexts(t *testing.T) {
	creates := &SubscriptionCreates{}
	t.Run("float", func(t *testing.T) {
		cases := []struct {
			name  string
			value any
			want  string
		}{
			{name: "none", value: nil, want: "float() argument must be a string or a real number, not 'NoneType'"},
			{name: "list", value: []any{}, want: "float() argument must be a string or a real number, not 'list'"},
			{name: "dict", value: domain.RequestObject{}, want: "float() argument must be a string or a real number, not 'dict'"},
			{name: "text", value: "abc", want: "could not convert string to float: 'abc'"},
			{name: "text with spaces", value: " abc ", want: "could not convert string to float: ' abc '"},
			{name: "huge integer", value: json.Number("100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"), want: "int too large to convert to float"},
		}
		for _, test := range cases {
			t.Run(test.name, func(t *testing.T) {
				_, err := subscriptionCreateFloat(test.value)
				var raised *domain.SubscriptionCreateRaised
				if !errors.As(err, &raised) || raised.Text != test.want {
					t.Fatalf("got %v, want %q", err, test.want)
				}
				if !errors.Is(err, domain.ErrSubscriptionCreateServer) {
					t.Fatal("a raised text must still be the server error (subscription/add keeps its 500 page)")
				}
			})
		}
	})
	t.Run("integer conversion", func(t *testing.T) {
		cases := []struct {
			value any
			want  string
		}{
			{value: []any{json.Number("1"), json.Number("2")}, want: "invalid literal for int() with base 10: '[1, 2]'"},
			{value: domain.RequestObject{{Name: "a", Value: json.Number("1")}}, want: `invalid literal for int() with base 10: "{'a': 1}"`},
		}
		for _, test := range cases {
			err := pythonIntError(test.value)
			var failure *domain.SubscriptionCreateFailure
			if !errors.As(err, &failure) || failure.Text != test.want {
				t.Fatalf("got %v, want %q", err, test.want)
			}
		}
	})
	t.Run("calendar", func(t *testing.T) {
		cases := map[string]string{
			"Month must be between 1 and 12":                          "Month must be between 1 and 12",
			"Day must be between 1 and 31":                            "Day must be between 1 and 31",
			"Invalid date combination: day is out of range for month": "Invalid date combination: day is out of range for month",
		}
		for message, want := range cases {
			err := subscriptionCreateCalendar(calendarError(message))
			var raised *domain.SubscriptionCreateRaised
			if !errors.As(err, &raised) || raised.Text != want {
				t.Fatalf("got %v, want %q", err, want)
			}
		}
	})
	t.Run("non-dict tutor", func(t *testing.T) {
		cases := []struct {
			name      string
			associate domain.RequestObject
			tutor     any
			age       int
			want      string
		}{
			{name: "keys", associate: domain.RequestObject{}, tutor: "x", age: 20, want: "'str' object has no attribute 'keys'"},
			{name: "minor keys", associate: domain.RequestObject{{Name: "address_city", Value: "Roma"}},
				tutor: json.Number("5"), age: 10, want: "'int' object has no attribute 'keys'"},
			{name: "not iterable", associate: domain.RequestObject{{Name: "address_city", Value: "Roma"}, {Name: "address_cap", Value: "00100"}},
				tutor: json.Number("5"), age: 20, want: "argument of type 'int' is not iterable"},
			{name: "string subscript", associate: domain.RequestObject{{Name: "address_city", Value: "Roma"}, {Name: "address_cap", Value: "00100"}},
				tutor: "address_cap", age: 20, want: "string indices must be integers, not 'str'"},
			{name: "list subscript", associate: domain.RequestObject{{Name: "address_city", Value: "Roma"}, {Name: "address_cap", Value: "00100"}},
				tutor: []any{"address_cap"}, age: 20, want: "list indices must be integers or slices, not str"},
		}
		for _, test := range cases {
			t.Run(test.name, func(t *testing.T) {
				err := nonObjectTutorError(test.associate, test.tutor, test.age)
				var raised *domain.SubscriptionCreateRaised
				var failure *domain.SubscriptionCreateFailure
				var text string
				if errors.As(err, &raised) {
					text = raised.Text
				} else if errors.As(err, &failure) {
					text = failure.Text
				}
				if text != test.want {
					t.Fatalf("got %v, want %q", err, test.want)
				}
			})
		}
	})
	t.Run("membership category", func(t *testing.T) {
		tx := &renewErrorCategoryTransaction{}
		_, err := creates.membershipCategory(context.Background(), tx, &domain.SubscriptionCreateSettings{AssociationID: "a"})
		var failure *domain.SubscriptionCreateFailure
		if !errors.As(err, &failure) || failure.Text != "'NoneType' object has no attribute 'payment_category_id'" {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("new member info", func(t *testing.T) {
		data := domain.RequestObject{{Name: "new_user_account", Value: domain.RequestObject{
			{Name: "new_member", Value: true}, {Name: "new_member_info", Value: "x"}}}}
		_, err := creates.newMember(context.Background(), nil, data, nil, SubscriptionCreateRequest{})
		var failure *domain.SubscriptionCreateFailure
		if !errors.As(err, &failure) || failure.Text != "string indices must be integers, not 'str'" {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("course events", func(t *testing.T) {
		if err := courseEventsNotIterableError(nil); !errors.Is(err, domain.ErrCourseSubscriptionServer) {
			t.Fatalf("got %v", err)
		}
		var raised *domain.CourseSubscriptionRaised
		if err := courseEventsNotIterableError(nil); !errors.As(err, &raised) || raised.Text != "'NoneType' object is not iterable" {
			t.Fatalf("got %v", err)
		}
		if err := courseEventsNotIterableError(json.Number("5")); !errors.As(err, &raised) || raised.Text != "'int' object is not iterable" {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("course title", func(t *testing.T) {
		_, _, err := courseLabel(nil, "course")
		var raised *domain.CourseSubscriptionRaised
		if !errors.As(err, &raised) || raised.Text != "sequence item 0: expected str instance, NoneType found" {
			t.Fatalf("got %v", err)
		}
	})
}
