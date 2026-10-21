package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type LoginProfiles struct {
	reader domain.LoginProfileReader
	now    func() time.Time
}

func NewLoginProfiles(reader domain.LoginProfileReader, now func() time.Time) *LoginProfiles {
	return &LoginProfiles{reader: reader, now: now}
}

// Read accepts only the trusted account selected by the login workflow. It
// does not grant admission or impersonation merely by serializing preferences.
func (s *LoginProfiles) Read(ctx context.Context, id string) (*dto.UserAuthProfile, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	row, err := s.reader.LoginProfile(ctx, id)
	if err != nil {
		return nil, err
	}
	if row.UserID != id || row.Deleted {
		return nil, domain.ErrAccountNotFound
	}
	profile := &dto.UserAuthProfile{
		UserID:                             row.UserID,
		FirstName:                          row.FirstName,
		LastName:                           row.LastName,
		Username:                           row.Username,
		Email:                              row.Email,
		AvatarImage:                        row.AvatarImage,
		CollaboratorRole:                   row.CollaboratorRole,
		CollaboratorPermissions:            row.CollaboratorPermissions,
		GoogleSyncEnabled:                  row.GoogleSyncEnabled,
		DashboardLayout:                    row.DashboardLayout,
		FullInstallmentsPlan:               row.FullInstallmentsPlan,
		ShowZeroPayments:                   row.ShowZeroPayments,
		DarkMode:                           row.DarkMode,
		MedicalCertificateNotifications:    row.MedicalCertificateNotifications,
		HideCategoryName:                   row.HideCategoryName,
		Phone:                              row.Phone,
		SubscriptionDurationEqualSportYear: row.SubscriptionDurationEqualSportYear,
		BalanceSheetStartDay:               row.BalanceSheetStartDay,
		BalanceSheetStartMonth:             row.BalanceSheetStartMonth,
		BalanceSheetYear:                   row.BalanceSheetYear,
		SubscriptionDuration:               row.SubscriptionDuration,
		MembershipDuration:                 row.MembershipDuration,
		MembershipStartingNumber:           row.MembershipStartingNumber,
		DefaultMembershipType:              row.DefaultMembershipType,
		SubscriptionStartDay:               row.SubscriptionStartDay,
		SubscriptionStartMonth:             row.SubscriptionStartMonth,
		CustomEndDate:                      row.CustomEndDate,
		SubscriptionEndDay:                 row.SubscriptionEndDay,
		SubscriptionEndMonth:               row.SubscriptionEndMonth,
		LeadSportAssociationRole:           row.LeadSportAssociationRole,
		LeadSportAssociationSize:           row.LeadSportAssociationSize,
		LeadSportMarketChannel:             row.LeadSportMarketChannel,
		DefaultPaymentCategory:             row.DefaultPaymentCategory,
		DefaultPaymentCategoryCourses:      row.DefaultPaymentCategoryCourses,

		CanImpersonate: row.Active && !row.Deleted && (row.Superuser || row.Role == domain.RoleAssociation),
	}
	if !row.Superuser && row.Role == domain.RoleAssociation {
		if row.AssociationID == nil {
			return nil, domain.ErrAssociationNotFound
		}
		profile.RequiresWelcome = requiresProfileWelcome(row, s.now())
		// Django .get() suppresses missing/duplicate onboarding objects. Never load
		// all duplicates: the repository returns at most two rows.
		if len(row.Onboarding) == 1 {
			entry := row.Onboarding[0]
			profile.Onboarding = &dto.UserOnboarding{
				ID:                entry.ID,
				UserID:            entry.UserID,
				Show:              entry.Show,
				CreateMembership:  entry.CreateMembership,
				ViewMembership:    entry.ViewMembership,
				ApprovePayment:    entry.ApprovePayment,
				DownloadInvoice:   entry.DownloadInvoice,
				ViewCollaborators: entry.ViewCollaborators,
				ViewSettings:      entry.ViewSettings,
			}
		}
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return profile, nil
}
func requiresProfileWelcome(row domain.LoginProfileSnapshot, now time.Time) bool {
	if row.SetupProvenance != nil {
		switch *row.SetupProvenance {
		case "fresh":
			return row.OnboardingCompletedAt == nil
		case "import":
			return false
		}
	}
	empty := func(value *string) bool { return value == nil || *value == "" }
	if empty(row.Logo) || empty(row.Denomination) || empty(row.TaxCode) || row.BalanceSheetStartDay == 0 || row.BalanceSheetStartMonth == 0 {
		return true
	}
	return row.DateJoined.After(now.Add(-30*24*time.Hour)) && (empty(row.LeadSportAssociationRole) || empty(row.LeadSportAssociationSize) || empty(row.LeadSportMarketChannel))
}
