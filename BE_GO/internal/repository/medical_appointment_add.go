package repository

import (
	"context"
	"database/sql"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type MedicalAppointmentAdd struct{ db *sqlx.DB }

func NewMedicalAppointmentAdd(db *sqlx.DB) *MedicalAppointmentAdd {
	return &MedicalAppointmentAdd{db: db}
}

var _ domain.MedicalAppointmentAddStore = (*MedicalAppointmentAdd)(nil)

func (r *MedicalAppointmentAdd) BeginMedicalAppointmentAdd(ctx context.Context) (domain.MedicalAppointmentAddTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &medicalAppointmentAddTransaction{ownedTransaction: tx}, nil
}

type medicalAppointmentAddTransaction struct{ *ownedTransaction }

func (t *medicalAppointmentAddTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

func (t *medicalAppointmentAddTransaction) Subscription(ctx context.Context, id, association string, impersonation, group *string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE subscription_id=$1 AND sport_association_id=$2
 AND ($3::uuid IS NULL OR sport_association_id=$3::uuid) AND ($4::uuid IS NULL OR group_id=$4::uuid))`, id, association, impersonation, group)
	return found, err
}

func (t *medicalAppointmentAddTransaction) InsertAppointment(ctx context.Context, row domain.MedicalAppointmentAddRow, association string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_medicalappointments
(medical_appointments_id,subscription_id,region,document_id,date,notes,sport,meta,address,creation_date)
VALUES($1,$2,$3,NULL,$4::date,$5,$6,$7::jsonb,$8,$9)`, row.ID, row.Subscription, row.Region, row.Date, row.Notes, row.Sport, row.Meta,
		row.Address, row.CreationDate); err != nil {
		return err
	}
	path := "subscription.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "medicalappointments", Key: row.ID,
		Representation: "MedicalAppointments object (" + row.ID + ")", Action: 0, Changes: row.Changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}
