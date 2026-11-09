package domain

import "time"

// BakneyConfiguration is the locked InstanceConfiguration row the pairing
// views read (InstanceConfiguration.objects.select_for_update().first()).
type BakneyConfiguration struct {
	ID                   int64   `db:"id"`
	SelfHosted           bool    `db:"self_hosted"`
	PrimaryAssociationID *string `db:"primary_association_id"`
}

// BakneyPairing is instance.sso.models.BakneyPairing.
type BakneyPairing struct {
	ID                int64      `db:"id"`
	ConfigID          int64      `db:"config_id"`
	InstanceID        string     `db:"instance_id"`
	PairingID         string     `db:"pairing_id"`
	SecretEncrypted   string     `db:"secret_encrypted"`
	Authority         string     `db:"authority"`
	UIOrigin          string     `db:"ui_origin"`
	RemotePairingID   *string    `db:"remote_pairing_id"`
	RemoteGeneration  *string    `db:"remote_generation"`
	Origin            string     `db:"origin"`
	AssociationID     *string    `db:"association_id"`
	AssociationName   string     `db:"association_name"`
	State             string     `db:"state"`
	ForwardingEnabled bool       `db:"forwarding_enabled"`
	CheckedAt         *time.Time `db:"checked_at"`
	UpdatedAt         time.Time  `db:"updated_at"`
}

// BakneyLogin is instance.sso.models.BakneyLogin: only the browser cookie's
// SHA-256 digest is stored.
type BakneyLogin struct {
	Digest            string    `db:"digest"`
	PairingID         string    `db:"pairing_id"`
	RemoteGeneration  *string   `db:"remote_generation"`
	AttemptID         *string   `db:"attempt_id"`
	State             string    `db:"state"`
	VerifierEncrypted string    `db:"verifier_encrypted"`
	Stage             string    `db:"stage"`
	UserID            *string   `db:"user_id"`
	ExpiresAt         time.Time `db:"expires_at"`
}

// BakneyRevocation is the durable revocation outbox row.
type BakneyRevocation struct {
	PairingID       string  `db:"pairing_id"`
	RemotePairingID *string `db:"remote_pairing_id"`
	Authority       string  `db:"authority"`
	SecretEncrypted string  `db:"secret_encrypted"`
}

// BakneyMember is the local user an SSO login resolves to.
type BakneyMember struct {
	ID              string  `db:"user_id"`
	Username        string  `db:"username"`
	FirstName       string  `db:"first_name"`
	Role            int     `db:"role"`
	Superuser       bool    `db:"is_superuser"`
	Staff           bool    `db:"is_staff"`
	ConnectedUserID *string `db:"connected_user_id"`
}

// BakneyMembership is the pairing's association with its owner account.
type BakneyMembership struct {
	ID           string `db:"sport_association_id"`
	Deleted      bool   `db:"deleted"`
	OwnerID      string `db:"user_id"`
	OwnerActive  bool   `db:"owner_active"`
	OwnerDeleted bool   `db:"owner_deleted"`
	OwnerRole    int    `db:"owner_role"`
}
