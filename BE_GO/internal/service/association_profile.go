package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type AssociationProfiles struct {
	reader domain.AssociationProfileReader
	now    func() time.Time
}

func NewAssociationProfiles(reader domain.AssociationProfileReader, now func() time.Time) *AssociationProfiles {
	return &AssociationProfiles{reader: reader, now: now}
}

// Read takes the trusted association owner's ID, not an untrusted requested
// tenant. Login composition must select the account/connected owner beforehand.
func (s *AssociationProfiles) Read(ctx context.Context, owner string) (*dto.AssociationProfile, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	row, err := s.reader.AssociationProfile(ctx, owner)
	if err != nil {
		return nil, err
	}
	if row.OwnerID != owner || row.ID == "" {
		return nil, domain.ErrAssociationNotFound
	}
	profile := &dto.AssociationProfile{
		ID:                         row.ID,
		Denomination:               row.Denomination,
		TaxCode:                    row.TaxCode,
		Address:                    row.Address,
		AddressCAP:                 row.AddressCAP,
		AddressCity:                row.AddressCity,
		Regulation:                 row.Regulation,
		Demand:                     row.Demand,
		Logo:                       row.Logo,
		SubscriptionFee:            row.SubscriptionFee,
		MultipleSubscriptionFee:    row.MultipleSubscriptionFee,
		SubscriptionFeePlans:       row.SubscriptionFeePlans,
		MembershipFee:              row.MembershipFee,
		MultipleMembershipFee:      row.MultipleMembershipFee,
		MembershipFeePlans:         row.MembershipFeePlans,
		CustomSubscriptionFormData: row.CustomSubscriptionFormData,
		DocumentHeader:             row.DocumentHeader,
		InvoiceFooter:              row.InvoiceFooter,
		EnableQuotesManagement:     row.EnableQuotesManagement,
		Configuration:              row.Configuration,
		AffiliateCode:              row.AffiliateCode,
		AdditionalSections:         row.AdditionalSections,
		PresidentSignature:         row.PresidentSignature,
		Stamp:                      row.Stamp,
		PresidentFirstName:         row.PresidentFirstName,
		PresidentLastName:          row.PresidentLastName,
		Federation:                 row.Federation,
		EnrollNumber:               row.EnrollNumber,
		Sport:                      row.Sport,
		EnabledFor:                 row.EnabledFor,
		CheckoutInfo:               row.CheckoutInfo,
		AdditionalFields:           row.AdditionalFields,
		StripeAvailableMethods:     row.StripeAvailableMethods,
		IBAN:                       row.IBAN,
		Website:                    row.Website,
		Abbreviated:                row.Abbreviated,
		VATNumber:                  row.VATNumber,
		WhatsApp:                   row.WhatsApp,
		InvoiceTemplate:            row.InvoiceTemplate,
		SubscriptionTemplate:       row.SubscriptionTemplate,
		ExtraTextInvoices:          row.ExtraTextInvoices,
		ImportedFromAssociami:      row.ImportedFromAssociami,
		ShowRegulationToMembers:    row.ShowRegulationToMembers,
		ShowRegulationToBoth:       row.ShowRegulationToBoth,
		ShowRegulationToAthletes:   row.ShowRegulationToAthletes,
		ShowDemandToMembers:        row.ShowDemandToMembers,
		ShowDemandToBoth:           row.ShowDemandToBoth,
		ShowDemandToAthletes:       row.ShowDemandToAthletes,

		Reviewed: row.Reviewed || row.OwnerJoined.After(s.now().Add(-5*24*time.Hour)),
		Groups:   make([]dto.AssociationProfileGroup, 0, len(row.Groups)),
	}
	for _, group := range row.Groups {
		if group.AssociationID != row.ID {
			return nil, domain.ErrAssociationNotFound
		}
		profile.Groups = append(profile.Groups, dto.AssociationProfileGroup{Label: group.Name, Value: group.ID})
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return profile, nil
}
