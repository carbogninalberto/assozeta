package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// modelHooksSubscriptionSave is the part of Subscription.save() that runs for
// every save of an existing row, update_fields included: the season range of
// the owner's settings, which raises (CalendarDateError) for settings that
// name no date. Writers that persist only some columns call it where Django's
// save() runs, before their own write.
func modelHooksSubscriptionSave(ctx context.Context, season domain.SubscriptionSaveSeason) error {
	_, err := SeasonRange(ctx, season.Created, season.Rule)
	return err
}
