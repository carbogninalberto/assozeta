package service

import (
	"encoding/json"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestDjangoUUIDValueFollowsUUIDFieldToPython(t *testing.T) {
	for value, want := range map[any]string{
		"{A1000000-0000-4000-8000-000000970001}": "a1000000-0000-4000-8000-000000970001",
		"a1000000000040008000000000970001":       "a1000000-0000-4000-8000-000000970001",
		json.Number("255"):                       "00000000-0000-0000-0000-0000000000ff",
		true:                                     "00000000-0000-0000-0000-000000000001",
	} {
		if got, err := djangoUUIDValue(value); err != nil || got != want {
			t.Fatalf("%#v: %q %v", value, got, err)
		}
	}
	for _, value := range []any{"abc", json.Number("1.5"), json.Number("-1"), json.Number("340282366920938463463374607431768211456"), nil, []any{}} {
		if _, err := djangoUUIDValue(value); !errors.Is(err, ErrPaymentSaveFailed) {
			t.Fatalf("%#v accepted", value)
		}
	}
}

func TestCarnetAssignFieldIndexesOnlyObjects(t *testing.T) {
	object := domain.RequestObject{{Name: "course_subscription_id", Value: "x"}}
	if value, present, err := carnetAssignField(object, "course_subscription_id"); err != nil || !present || value != "x" {
		t.Fatal(value, present, err)
	}
	for _, body := range []any{[]any{"course_subscription_id"}, "a course_subscription_id b", json.Number("1")} {
		if _, _, err := carnetAssignField(body, "course_subscription_id"); !errors.Is(err, ErrPaymentSaveFailed) {
			t.Fatalf("%#v indexed", body)
		}
	}
	for _, body := range []any{nil, []any{"other"}, "other"} {
		if _, present, err := carnetAssignField(body, "course_subscription_id"); err != nil || present {
			t.Fatalf("%#v present", body)
		}
	}
}
