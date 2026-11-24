UPDATE instance_bakneypairing
SET state = 'disconnected', secret_encrypted = '', authority = '', association_name = '',
    forwarding_enabled = false, checked_at = NULL;
DELETE FROM instance_bakneylogin;
DELETE FROM instance_bakneyrevocation;
DELETE FROM instance_bakneynonce;
