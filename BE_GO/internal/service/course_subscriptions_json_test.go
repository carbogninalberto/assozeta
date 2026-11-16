package service

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// TestCourseSubscriptionJSONColumnIsAppState pins the jsonb refusal as the
// state Django reaches by its own save (the 500 page), never a refusal that
// would be handed off.
func TestCourseSubscriptionJSONColumnIsAppState(t *testing.T) {
	if _, ok := refusal.AppStateReason(errCourseSubscriptionJSONColumn); !ok {
		t.Fatalf("not an application-state error: %v", errCourseSubscriptionJSONColumn)
	}
	if errors.Is(errCourseSubscriptionJSONColumn, refusal.ErrNotPorted) {
		t.Fatalf("still a refusal: %v", errCourseSubscriptionJSONColumn)
	}
}

// TestCourseSubscriptionJSONValueGatesOnFormSurrogate is the split's second
// half: only a form JSON text that kept a lone surrogate answers the jsonb
// state at the row save (an escape the value writes back, not a pair, and not
// an ordinary value).
func TestCourseSubscriptionJSONValueGatesOnFormSurrogate(t *testing.T) {
	lone, pair, plain := `["\ud800"]`, `["\ud83d\ude00"]`, `{"title": "Base"}`
	surrogate := dto.CourseSubscriptionWrite{FormJSONSurrogate: true}
	for _, test := range []struct {
		name  string
		write dto.CourseSubscriptionWrite
		text  *string
		want  bool
	}{
		{name: "form lone surrogate", write: surrogate, text: &lone, want: true},
		{name: "form lone surrogate in an ordinary value", write: surrogate, text: &plain},
		{name: "form surrogate pair", write: surrogate, text: &pair},
		{name: "no form surrogate", write: dto.CourseSubscriptionWrite{}, text: &lone},
		{name: "no value written", write: surrogate},
	} {
		err := courseSubscriptionJSONValue(test.write, test.text)
		if test.want && !errors.Is(err, errCourseSubscriptionJSONColumn) {
			t.Errorf("%s: %v, want the jsonb state", test.name, err)
		}
		if !test.want && err != nil {
			t.Errorf("%s: %v, want no error", test.name, err)
		}
	}
}
