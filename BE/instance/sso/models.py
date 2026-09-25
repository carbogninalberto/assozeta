import uuid

from django.db import models


class BakneyPairing(models.Model):
    config = models.OneToOneField('instance.InstanceConfiguration', on_delete=models.CASCADE)
    instance_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    pairing_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    secret_encrypted = models.TextField(default='')
    authority = models.URLField(default='')
    ui_origin = models.URLField(default='')
    remote_pairing_id = models.UUIDField(null=True)
    remote_generation = models.UUIDField(null=True)
    origin = models.URLField(default='')
    association_id = models.UUIDField(null=True)
    association_name = models.CharField(max_length=255, default='')
    state = models.CharField(max_length=16, default='disconnected')
    forwarding_enabled = models.BooleanField(default=False)
    checked_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)


class BakneyNonce(models.Model):
    digest = models.CharField(max_length=64, primary_key=True)
    expires_at = models.DateTimeField()


class BakneyChallenge(models.Model):
    """Unsigned challenges cannot replace a pairing; only a matching signed commit can."""
    digest = models.CharField(max_length=64, primary_key=True)
    pairing_id = models.UUIDField()
    expires_at = models.DateTimeField()


class BakneyLogin(models.Model):
    """Only the random browser cookie's hash is stored; no local JWTs here."""
    digest = models.CharField(max_length=64, primary_key=True)
    pairing_id = models.UUIDField()
    remote_generation = models.UUIDField(null=True)
    attempt_id = models.UUIDField(null=True)
    state = models.CharField(max_length=64)
    verifier_encrypted = models.TextField(default='')
    stage = models.CharField(max_length=16, default='started')
    user_id = models.UUIDField(null=True)
    expires_at = models.DateTimeField()


class BakneyRevocation(models.Model):
    """Durable outbox: local revocation never depends on upstream availability."""
    pairing_id = models.UUIDField(primary_key=True)
    remote_pairing_id = models.UUIDField(null=True)
    authority = models.URLField()
    secret_encrypted = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
