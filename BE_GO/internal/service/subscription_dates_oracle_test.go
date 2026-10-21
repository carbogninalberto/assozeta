//go:build subscriptiondatesintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestSubscriptionDatesDjangoOracle(t *testing.T) {
	var fixture struct {
		Ranges []struct {
			Name, Created, Start, End, Message string
			Rule                               domain.CalendarRule
		}
		Defaults []struct {
			Name, Created     string
			Rule              domain.SubscriptionDateRule
			Initial, Expected domain.SubscriptionDates
		}
	}
	raw, e := os.ReadFile(os.Getenv("ASSOZETA_SUBSCRIPTION_DATES_FIXTURE"))
	if e != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.Ranges) != 293 || len(fixture.Defaults) != 36 {
		t.Fatal("run accounts.py --only subscription-dates; all declared cases are required")
	}
	t.Run("range", func(t *testing.T) {
		for _, row := range fixture.Ranges {
			t.Run(row.Name, func(t *testing.T) {
				at, e := time.Parse(time.RFC3339Nano, row.Created)
				if e != nil {
					t.Fatal(e)
				}
				got, e := SeasonRange(context.Background(), at, row.Rule)
				if row.Message != "" {
					if !errors.Is(e, domain.ErrInvalidCalendar) || e.Error() != row.Message {
						t.Fatalf("error %v want %q", e, row.Message)
					}
					return
				}
				start, e1 := time.Parse("2006-01-02", row.Start)
				end, e2 := time.Parse("2006-01-02", row.End)
				if e != nil || e1 != nil || e2 != nil || !got.Start.Equal(start) || !got.End.Equal(end) || got.Start.Location() != time.UTC || got.End.Location() != time.UTC {
					t.Fatalf("range %+v error %v", got, e)
				}
			})
		}
	})
	t.Run("defaults", func(t *testing.T) {
		for _, row := range fixture.Defaults {
			t.Run(row.Name, func(t *testing.T) {
				at, e := time.Parse(time.RFC3339Nano, row.Created)
				if e != nil {
					t.Fatal(e)
				}
				got, e := ResolveSubscriptionDates(context.Background(), at, row.Rule, row.Initial)
				if e != nil || !reflect.DeepEqual(got, row.Expected) {
					t.Fatalf("dates %+v error %v want %+v", got, e, row.Expected)
				}
			})
		}
	})
}
