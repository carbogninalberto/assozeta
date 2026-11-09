package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// BakneySSO opens the transaction SecureView.dispatch runs in
// (transaction.atomic around the whole view).
type BakneySSO struct{ db *sqlx.DB }

func NewBakneySSO(db *sqlx.DB) *BakneySSO { return &BakneySSO{db: db} }

type BakneySSOTransaction struct{ tx *ownedTransaction }

func (r *BakneySSO) Begin(ctx context.Context) (*BakneySSOTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, nil)
	if err != nil {
		return nil, err
	}
	return &BakneySSOTransaction{tx: tx}, nil
}

// BeginJob is Begin for a worker job: effects are fenced by the job lease.
func (r *BakneySSO) BeginJob(ctx context.Context, job domain.Job) (*BakneySSOTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &BakneySSOTransaction{tx: tx}, nil
}

func (t *BakneySSOTransaction) Commit() error   { return t.tx.Commit() }
func (t *BakneySSOTransaction) Rollback() error { return t.tx.Rollback() }

// LockConfiguration is InstanceConfiguration.objects.select_for_update().first().
func (t *BakneySSOTransaction) LockConfiguration(ctx context.Context) (*domain.BakneyConfiguration, error) {
	var row domain.BakneyConfiguration
	err := t.tx.tx.GetContext(ctx, &row, `SELECT id,self_hosted,primary_association_id::text AS primary_association_id
 FROM instance_instanceconfiguration ORDER BY id LIMIT 1 FOR UPDATE`)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

const bakneyPairingColumns = `id,config_id,instance_id::text AS instance_id,pairing_id::text AS pairing_id,secret_encrypted,authority,ui_origin,
 remote_pairing_id::text AS remote_pairing_id,remote_generation::text AS remote_generation,origin,association_id::text AS association_id,
 association_name,state,forwarding_enabled,checked_at,updated_at`

// Pairing is BakneyPairing.objects.get(config=config) (nil when absent).
func (t *BakneySSOTransaction) Pairing(ctx context.Context, configID int64) (*domain.BakneyPairing, error) {
	var row domain.BakneyPairing
	err := t.tx.tx.GetContext(ctx, &row, `SELECT `+bakneyPairingColumns+` FROM instance_bakneypairing WHERE config_id=$1`, configID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

// CreatePairing is get_or_create's create(config=config) with the model defaults.
func (t *BakneySSOTransaction) CreatePairing(ctx context.Context, configID int64, instanceID, pairingID string, now time.Time) (*domain.BakneyPairing, error) {
	var row domain.BakneyPairing
	err := t.tx.tx.GetContext(ctx, &row, `INSERT INTO instance_bakneypairing (config_id,instance_id,pairing_id,secret_encrypted,authority,ui_origin,
 remote_pairing_id,remote_generation,origin,association_id,association_name,state,forwarding_enabled,checked_at,updated_at)
 VALUES ($1,$2::uuid,$3::uuid,'','','',NULL,NULL,'',NULL,'','disconnected',false,NULL,$4) RETURNING `+bakneyPairingColumns,
		configID, instanceID, pairingID, now)
	return &row, err
}

// SavePairing is pairing.save(): every field, updated_at (auto_now) = now.
func (t *BakneySSOTransaction) SavePairing(ctx context.Context, p *domain.BakneyPairing, now time.Time) error {
	_, err := t.tx.tx.ExecContext(ctx, `UPDATE instance_bakneypairing SET config_id=$2,instance_id=$3::uuid,pairing_id=$4::uuid,secret_encrypted=$5,
 authority=$6,ui_origin=$7,remote_pairing_id=$8::uuid,remote_generation=$9::uuid,origin=$10,association_id=$11::uuid,association_name=$12,
 state=$13,forwarding_enabled=$14,checked_at=$15,updated_at=$16 WHERE id=$1`,
		p.ID, p.ConfigID, p.InstanceID, p.PairingID, p.SecretEncrypted, p.Authority, p.UIOrigin, p.RemotePairingID, p.RemoteGeneration,
		p.Origin, p.AssociationID, p.AssociationName, p.State, p.ForwardingEnabled, p.CheckedAt, now)
	if err == nil {
		p.UpdatedAt = now
	}
	return err
}

// Denomination is config.primary_association.denomination (base manager).
func (t *BakneySSOTransaction) Denomination(ctx context.Context, associationID string) (string, error) {
	var name string
	err := t.tx.tx.GetContext(ctx, &name, `SELECT denomination FROM application_sportassociation WHERE sport_association_id=$1::uuid`, associationID)
	return name, err
}

func (t *BakneySSOTransaction) RevocationPending(ctx context.Context) (bool, error) {
	var exists bool
	err := t.tx.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM instance_bakneyrevocation)`)
	return exists, err
}

// QueueRevocation is BakneyRevocation.objects.get_or_create(pairing_id=..., defaults=...).
func (t *BakneySSOTransaction) QueueRevocation(ctx context.Context, revocation domain.BakneyRevocation, now time.Time) error {
	_, err := t.tx.tx.ExecContext(ctx, `INSERT INTO instance_bakneyrevocation (pairing_id,remote_pairing_id,authority,secret_encrypted,created_at)
 VALUES ($1::uuid,$2::uuid,$3,$4,$5) ON CONFLICT (pairing_id) DO NOTHING`,
		revocation.PairingID, revocation.RemotePairingID, revocation.Authority, revocation.SecretEncrypted, now)
	return err
}

func (t *BakneySSOTransaction) DeleteLogins(ctx context.Context, pairingID string) error {
	_, err := t.tx.tx.ExecContext(ctx, `DELETE FROM instance_bakneylogin WHERE pairing_id=$1::uuid`, pairingID)
	return err
}

func (t *BakneySSOTransaction) DeleteChallenges(ctx context.Context, pairingID string) error {
	_, err := t.tx.tx.ExecContext(ctx, `DELETE FROM instance_bakneychallenge WHERE pairing_id=$1::uuid`, pairingID)
	return err
}

// ChallengeExists is BakneyChallenge.objects.filter(digest, pairing_id, expires_at__gt=now).first().
func (t *BakneySSOTransaction) ChallengeExists(ctx context.Context, digest, pairingID string, now time.Time) (bool, error) {
	var exists bool
	err := t.tx.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM instance_bakneychallenge WHERE digest=$1 AND pairing_id=$2::uuid
 AND expires_at>$3)`, digest, pairingID, now)
	return exists, err
}

// PutChallenge is BakneyChallenge.objects.update_or_create(digest=..., defaults={pairing_id, expires_at}).
func (t *BakneySSOTransaction) PutChallenge(ctx context.Context, digest, pairingID string, expires time.Time) error {
	_, err := t.tx.tx.ExecContext(ctx, `INSERT INTO instance_bakneychallenge (digest,pairing_id,expires_at) VALUES ($1,$2::uuid,$3)
 ON CONFLICT (digest) DO UPDATE SET pairing_id=EXCLUDED.pairing_id,expires_at=EXCLUDED.expires_at`, digest, pairingID, expires)
	return err
}

// ConsumeNonce is consume_nonce's create in a savepoint: false when the
// digest already exists (IntegrityError).
func (t *BakneySSOTransaction) ConsumeNonce(ctx context.Context, digest string, expires time.Time) (bool, error) {
	result, err := t.tx.tx.ExecContext(ctx, `INSERT INTO instance_bakneynonce (digest,expires_at) VALUES ($1,$2) ON CONFLICT (digest) DO NOTHING`,
		digest, expires)
	if err != nil {
		return false, err
	}
	count, err := result.RowsAffected()
	return count == 1, err
}

const bakneyLoginColumns = `digest,pairing_id::text AS pairing_id,remote_generation::text AS remote_generation,attempt_id::text AS attempt_id,
 state,verifier_encrypted,stage,user_id::text AS user_id,expires_at`

// LockedLogin is BakneyLogin.objects.select_for_update().filter(digest, pairing_id).first().
func (t *BakneySSOTransaction) LockedLogin(ctx context.Context, digest, pairingID string) (*domain.BakneyLogin, error) {
	var row domain.BakneyLogin
	err := t.tx.tx.GetContext(ctx, &row, `SELECT `+bakneyLoginColumns+` FROM instance_bakneylogin WHERE digest=$1 AND pairing_id=$2::uuid
 ORDER BY digest LIMIT 1 FOR UPDATE`, digest, pairingID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

func (t *BakneySSOTransaction) DeleteLoginDigest(ctx context.Context, digest string) error {
	_, err := t.tx.tx.ExecContext(ctx, `DELETE FROM instance_bakneylogin WHERE digest=$1`, digest)
	return err
}

func (t *BakneySSOTransaction) CreateLogin(ctx context.Context, login domain.BakneyLogin) error {
	_, err := t.tx.tx.ExecContext(ctx, `INSERT INTO instance_bakneylogin (digest,pairing_id,remote_generation,attempt_id,state,verifier_encrypted,
 stage,user_id,expires_at) VALUES ($1,$2::uuid,$3::uuid,$4::uuid,$5,$6,$7,$8::uuid,$9)`, login.Digest, login.PairingID, login.RemoteGeneration,
		login.AttemptID, login.State, login.VerifierEncrypted, login.Stage, login.UserID, login.ExpiresAt)
	return err
}

// SaveLogin is login.save(): every field.
func (t *BakneySSOTransaction) SaveLogin(ctx context.Context, login domain.BakneyLogin) error {
	_, err := t.tx.tx.ExecContext(ctx, `UPDATE instance_bakneylogin SET pairing_id=$2::uuid,remote_generation=$3::uuid,attempt_id=$4::uuid,state=$5,
 verifier_encrypted=$6,stage=$7,user_id=$8::uuid,expires_at=$9 WHERE digest=$1`, login.Digest, login.PairingID, login.RemoteGeneration,
		login.AttemptID, login.State, login.VerifierEncrypted, login.Stage, login.UserID, login.ExpiresAt)
	return err
}

// SetLoginStage is login.save(update_fields=['stage']).
func (t *BakneySSOTransaction) SetLoginStage(ctx context.Context, digest, stage string) error {
	_, err := t.tx.tx.ExecContext(ctx, `UPDATE instance_bakneylogin SET stage=$2 WHERE digest=$1`, digest, stage)
	return err
}

// ActiveMember is User.objects.get(pk=id, is_active=True, deleted=False) (nil when absent).
func (t *BakneySSOTransaction) ActiveMember(ctx context.Context, id string) (*domain.BakneyMember, error) {
	var row domain.BakneyMember
	err := t.tx.tx.GetContext(ctx, &row, `SELECT user_id::text AS user_id,username,first_name,role,is_superuser,is_staff,
 connected_user_id::text AS connected_user_id FROM bakney_user WHERE user_id=$1::uuid AND is_active AND NOT deleted`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

// Membership is SportAssociation._base_manager.select_related('user').filter(pk=id).first().
func (t *BakneySSOTransaction) Membership(ctx context.Context, associationID string) (*domain.BakneyMembership, error) {
	var row domain.BakneyMembership
	err := t.tx.tx.GetContext(ctx, &row, `SELECT a.sport_association_id::text AS sport_association_id,a.deleted,u.user_id::text AS user_id,
 u.is_active AS owner_active,u.deleted AS owner_deleted,u.role AS owner_role
 FROM application_sportassociation a JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1::uuid`, associationID)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

// Linked is the association link test of local_user: an undeleted associate
// of the association or an instructor of the owner bound to the user.
func (t *BakneySSOTransaction) Linked(ctx context.Context, userID, associationID, ownerID string) (bool, error) {
	var linked bool
	err := t.tx.tx.GetContext(ctx, &linked, `SELECT EXISTS(SELECT 1 FROM application_associate WHERE user_id=$1::uuid
 AND sport_association_id=$2::uuid AND NOT deleted) OR EXISTS(SELECT 1 FROM application_instructor WHERE user_id=$3::uuid
 AND associated_user_id=$1::uuid)`, userID, associationID, ownerID)
	return linked, err
}

// SetLastLogin is user.save(update_fields=['last_login']); auditlog excludes the field.
func (t *BakneySSOTransaction) SetLastLogin(ctx context.Context, userID string, at time.Time) error {
	_, err := t.tx.tx.ExecContext(ctx, `UPDATE bakney_user SET last_login=$2 WHERE user_id=$1::uuid`, userID, at)
	return err
}

// Revocations is BakneyRevocation.objects.order_by('created_at')[:20].
func (t *BakneySSOTransaction) Revocations(ctx context.Context) ([]domain.BakneyRevocation, error) {
	var rows []domain.BakneyRevocation
	err := t.tx.tx.SelectContext(ctx, &rows, `SELECT pairing_id::text AS pairing_id,remote_pairing_id::text AS remote_pairing_id,authority,
 secret_encrypted FROM instance_bakneyrevocation ORDER BY created_at ASC LIMIT 20`)
	return rows, err
}

func (t *BakneySSOTransaction) DeleteRevocation(ctx context.Context, pairingID string) error {
	_, err := t.tx.tx.ExecContext(ctx, `DELETE FROM instance_bakneyrevocation WHERE pairing_id=$1::uuid`, pairingID)
	return err
}

// DeleteExpired is one of deliver_revocations' cleanups: rows of the table
// whose expires_at is before the cutoff.
func (t *BakneySSOTransaction) DeleteExpired(ctx context.Context, table string, cutoff time.Time) error {
	var statement string
	switch table {
	case "challenges":
		statement = `DELETE FROM instance_bakneychallenge WHERE expires_at<$1`
	case "nonces":
		statement = `DELETE FROM instance_bakneynonce WHERE expires_at<$1`
	case "logins":
		statement = `DELETE FROM instance_bakneylogin WHERE expires_at<$1`
	default:
		return errors.New("unknown bakney table")
	}
	_, err := t.tx.tx.ExecContext(ctx, statement, cutoff)
	return err
}

// PairingNeedsSync is BakneyPairing.objects.filter(state__in=('pending', 'paired'),
// remote_pairing_id__isnull=False).exists().
func (t *BakneySSOTransaction) PairingNeedsSync(ctx context.Context) (bool, error) {
	var exists bool
	err := t.tx.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM instance_bakneypairing WHERE state IN ('pending','paired')
 AND remote_pairing_id IS NOT NULL)`)
	return exists, err
}

// CompleteJob records the worker job's completion in this fenced transaction.
func (t *BakneySSOTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx.tx, id, token, nil)
}
