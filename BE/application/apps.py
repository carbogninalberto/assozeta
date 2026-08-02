from django.apps import AppConfig


class UsersManagerConfig(AppConfig):
    name = 'application'

    def ready(self):
        # Patch SimpleJWT to support EdDSA algorithm
        from core.jwt_backend import patch_simplejwt_for_eddsa
        patch_simplejwt_for_eddsa()

        # Import signals to ensure they are connected
        import application.signals  # noqa

        # Setup audit log resolvers and connect signal
        from application.audit_resolvers import setup_audit_resolvers
        from application.signals import connect_audit_log_signal

        setup_audit_resolvers()
        connect_audit_log_signal()
