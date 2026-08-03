"""
API views for instance configuration endpoints.
"""
import os
import logging
import secrets

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.db import transaction

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework import status

from celery.result import AsyncResult

from .models import InstanceConfiguration
from .serializers import (
    CANONICAL_LOGO_URL,
    InstanceConfigSerializer,
    InstanceSetupSerializer,
    InstanceReconfigureSerializer,
)
from .defaults import SUPPORTED_FEATURES, DEFAULT_DISPLAY_SETTINGS
from .permissions import SetupTokenOrAuthenticated, is_primary_association_owner_or_superuser

logger = logging.getLogger(__name__)


class InstanceStatusView(APIView):
    """
    GET /instance/status
    Check if instance is configured.
    Public endpoint - no authentication required.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        config = InstanceConfiguration.get_config()
        return Response({
            "configured": config is not None,
            "version": getattr(settings, 'RUNNING_VERSION', 'v0.0.0'),
            "instance_name": config.name if config else None,
            "supported_features": SUPPORTED_FEATURES,
        })


class InstanceConfigView(APIView):
    """
    GET /instance/config
    Get full instance configuration for frontend.
    Public endpoint - no authentication required.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        config = InstanceConfiguration.get_config()
        if not config:
            return Response(
                {"error": "Instance not configured"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = InstanceConfigSerializer(config)
        return Response(serializer.data)


class InstanceSetupView(APIView):
    """
    POST /instance/configure
    Initial instance setup (only works once).
    Protected by setup token during first-run setup wizard.
    """
    permission_classes = [SetupTokenOrAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request):
        # Check if already configured
        if InstanceConfiguration.is_configured():
            return Response(
                {
                    "success": False,
                    "error": "Instance already configured. Use /instance/reconfigure to update."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = InstanceSetupSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        data = serializer.validated_data
        init_data = data['initialization']

        try:
            with transaction.atomic():
                # Create instance configuration
                config = InstanceConfiguration.objects.create(
                    domain=data['domain'],
                    name=data['oem']['name'],
                    abbreviation=data['oem'].get('abbreviation') or '',
                    primary_color=data['oem'].get('primaryColor') or '#351DC2',
                    support_email=data['oem'].get('supportEmail') or '',
                    logo_path=data['oem'].get('logo') or '',
                    display_settings=DEFAULT_DISPLAY_SETTINGS.copy(),
                    # OAuth (convert null to empty string)
                    google_client_id=data.get('oauth', {}).get('googleClientId') or '',
                    apple_client_id=data.get('oauth', {}).get('appleClientId') or '',
                    # Stripe (convert null to empty string)
                    stripe_public_key=data.get('stripe', {}).get('publicKey') or '',
                    stripe_secret_key=data.get('stripe', {}).get('secretKey') or '',
                    stripe_webhook_secret=data.get('stripe', {}).get('webhookSecret') or '',
                    stripe_pricing_table=data.get('stripe', {}).get('pricingTable') or '',
                    stripe_client_portal=data.get('stripe', {}).get('clientPortal') or '',
                    # Flags
                    self_hosted=True,
                    support_multiple_associations=False,
                )

                association = None

                if init_data['type'] == 'fresh':
                    # Create new user and association
                    association = self._create_fresh_instance(
                        config=config,
                        association_name=init_data['associationName'],
                        owner_email=init_data['ownerEmail'],
                        owner_password=init_data['ownerPassword'],
                    )

                elif init_data['type'] == 'import':
                    # Link existing association from import
                    association = self._link_imported_instance(
                        config=config,
                        import_task_id=init_data['importTaskId'],
                    )

                if association:
                    self._ensure_selfhost_billing(association.user)
                    config.primary_association = association
                    config.save()

                logger.info(
                    "Instance configured successfully",
                    extra={
                        'domain': config.domain,
                        'instance_name': config.name,
                        'init_type': init_data['type'],
                        'association_id': str(association.sport_association_id) if association else None,
                    }
                )

                return Response({
                    "success": True,
                    "message": "Istanza configurata con successo",
                    "association_id": str(association.sport_association_id) if association else None,
                    "redirect": "/login"
                })

        except Exception as e:
            logger.error(f"Instance setup failed: {str(e)}", exc_info=True)
            return Response(
                {"success": False, "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _create_fresh_instance(self, config, association_name, owner_email, owner_password):
        """Create a new user and sport association for fresh instance setup."""
        # Import here to avoid circular imports
        from application.models import User, SportAssociation, CustomAccounts
        from application.models.user_models import UsersOnboarding

        # Create owner user
        user = User.objects.create_user(
            email=owner_email,
            username=secrets.token_hex(10),
            password=owner_password,
            role=User.ASSOCIATION,
            is_active=True,
        )

        # Create sport association
        association = SportAssociation.objects.create(
            user=user,
            denomination=association_name,
            tax_code='',  # Will be filled during onboarding
        )

        # Create default custom accounts (cassa and banca)
        CustomAccounts.objects.create(
            name='cassa',
            initial_balance=0,
            account_type=1,
            account_code='cassa',
            editable=False,
            sport_association=association,
        )

        CustomAccounts.objects.create(
            name='banca',
            initial_balance=0,
            account_type=2,
            account_code='banca',
            editable=False,
            sport_association=association,
        )

        # Create onboarding record
        try:
            UsersOnboarding.objects.create(user=user)
        except Exception:
            pass  # Onboarding is optional

        logger.info(
            "Fresh instance created",
            extra={
                'user_id': str(user.user_id),
                'association_id': str(association.sport_association_id),
                'association_name': association_name,
            }
        )

        return association

    @staticmethod
    def _ensure_selfhost_billing(user):
        """Keep self-hosted owners on the included Pro plan."""
        import datetime

        from django.utils import timezone

        from application.models.billing_models import BillingPlan, BillingSubscription

        billing_plan = BillingPlan.objects.filter(name__exact="Piano Pro").first()
        if not billing_plan:
            billing_plan = BillingPlan.objects.first()
        if not billing_plan:
            raise ValueError("No billing plan is available for the self-hosted owner.")

        defaults = {
            'auto_renewal': True,
            'renewal_type': BillingSubscription.ANNUALLY,
            'ends_on': timezone.now() + datetime.timedelta(days=36500),
            'billing_plan': billing_plan,
        }
        subscriptions = BillingSubscription.objects.filter(user=user)
        if subscriptions.exists():
            subscriptions.update(**defaults)
        else:
            BillingSubscription.objects.create(user=user, **defaults)

    def _link_imported_instance(self, config, import_task_id):
        """Link an association from a completed import task."""
        from application.models import SportAssociation

        # Check import task status
        result = AsyncResult(import_task_id)

        if not result.ready():
            raise ValueError("Import task is still in progress. Please wait for completion.")

        if result.failed():
            raise ValueError(f"Import task failed: {result.result}")

        # Get the association created by import
        # The import task returns sport_association_id in its result
        task_result = result.result
        logger.info(f"Import task result: {task_result}")

        if isinstance(task_result, dict):
            # Check for success first
            if not task_result.get('success', True):
                raise ValueError(f"Import failed: {task_result.get('error', 'Unknown error')}")

            association_id = task_result.get('sport_association_id') or task_result.get('association_id')
            logger.info(f"Looking for association with ID: {association_id}")

            if association_id:
                association = SportAssociation.original_objects.filter(
                    sport_association_id=association_id
                ).first()

                if not association:
                    # Debug: check if any associations exist
                    all_assocs = SportAssociation.original_objects.all().values_list('sport_association_id', flat=True)
                    logger.error(f"Association {association_id} not found. Existing associations: {list(all_assocs)}")
                    raise ValueError(f"Association {association_id} not found after import.")

                return association
        else:
            logger.warning(f"Unexpected task result type: {type(task_result)}")

        # Fallback: get the most recently created association
        association = SportAssociation.original_objects.order_by('-sport_association_id').first()
        if not association:
            raise ValueError("No association found after import.")

        return association


class InstanceLogoUploadView(APIView):
    """
    POST /instance/logo
    Upload instance logo.
    """
    permission_classes = [SetupTokenOrAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        if 'file' not in request.FILES:
            return Response(
                {"success": False, "error": "No file provided"},
                status=status.HTTP_400_BAD_REQUEST
            )

        file = request.FILES['file']

        # Validate file type
        allowed_types = ['image/png', 'image/jpeg', 'image/webp']
        if file.content_type not in allowed_types:
            return Response(
                {"success": False, "error": "Invalid file type. Use PNG, JPG, or WebP."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate file size (max 5MB)
        if file.size > 5 * 1024 * 1024:
            return Response(
                {"success": False, "error": "File too large. Maximum size is 5MB."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Determine file extension
        ext_map = {
            'image/png': 'png',
            'image/jpeg': 'jpg',
            'image/webp': 'webp',
        }
        ext = ext_map.get(file.content_type, 'png')
        filename = f'instance/logo.{ext}'

        # Save file using default storage (S3 or local)
        try:
            # Delete existing logo if present
            for old_ext in ['png', 'jpg', 'svg', 'webp']:
                old_path = f'instance/logo.{old_ext}'
                if default_storage.exists(old_path):
                    default_storage.delete(old_path)

            # Save new logo
            saved_path = default_storage.save(filename, file)
            logo_url = CANONICAL_LOGO_URL

            # Update config if exists
            config = InstanceConfiguration.get_config()
            if config:
                config.logo_path = logo_url
                config.save()

            logger.info(f"Logo uploaded: {saved_path}")

            return Response({
                "success": True,
                "logo_url": logo_url
            })

        except Exception as e:
            logger.error(f"Logo upload failed: {str(e)}", exc_info=True)
            return Response(
                {"success": False, "error": "Failed to save logo"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class InstanceLogoServeView(APIView):
    """
    GET /instance/logo.png (or .jpg, .svg, .webp)
    Serve the instance logo.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        # Try to find logo with any extension
        for ext in ['png', 'jpg', 'svg', 'webp']:
            logo_path = f'instance/logo.{ext}'
            if default_storage.exists(logo_path):
                try:
                    file = default_storage.open(logo_path, 'rb')
                    content_types = {
                        'png': 'image/png',
                        'jpg': 'image/jpeg',
                        'svg': 'image/svg+xml',
                        'webp': 'image/webp',
                    }
                    response = FileResponse(
                        file,
                        content_type=content_types.get(ext, 'image/png')
                    )
                    response['Cache-Control'] = 'public, max-age=86400'
                    return response
                except Exception:
                    pass

        # Return 404 if no logo found
        raise Http404("Logo not found")


class InstanceManifestView(APIView):
    """
    GET /instance/manifest.json
    Dynamic PWA manifest.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        config = InstanceConfiguration.get_config()

        if not config:
            # Default manifest for unconfigured instance
            return Response({
                "name": "Bakney Sport",
                "short_name": "Bakney",
                "description": "Gestionale per associazioni sportive",
                "icons": [
                    {
                        "src": "/favicon.svg",
                        "sizes": "192x192",
                        "type": "image/svg+xml"
                    },
                    {
                        "src": "/favicon.svg",
                        "sizes": "512x512",
                        "type": "image/svg+xml"
                    }
                ],
                "start_url": "/",
                "display": "standalone",
                "background_color": "#ffffff",
                "theme_color": "#351DC2"
            })

        # Determine icon info
        icon_src = config.logo_path if config.logo_path else "/favicon.svg"
        icon_type = "image/png"
        if icon_src.endswith('.svg'):
            icon_type = "image/svg+xml"
        elif icon_src.endswith('.jpg') or icon_src.endswith('.jpeg'):
            icon_type = "image/jpeg"
        elif icon_src.endswith('.webp'):
            icon_type = "image/webp"

        return Response({
            "name": config.name,
            "short_name": config.abbreviation or config.name[:12],
            "description": config.meta_description or "Gestionale per associazioni sportive",
            "icons": [
                {
                    "src": icon_src,
                    "sizes": "192x192",
                    "type": icon_type
                },
                {
                    "src": icon_src,
                    "sizes": "512x512",
                    "type": icon_type
                }
            ],
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": config.primary_color
        })


class InstanceReconfigureView(APIView):
    """
    PUT /instance/reconfigure
    Update instance configuration.
    Requires authentication and admin privileges.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def put(self, request):
        config = InstanceConfiguration.get_config()
        if not config:
            return Response(
                {"success": False, "error": "Instance not configured"},
                status=status.HTTP_404_NOT_FOUND
            )

        user = request.user
        if not is_primary_association_owner_or_superuser(user, config):
            return Response(
                {"success": False, "error": "Only the instance owner can reconfigure"},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = InstanceReconfigureSerializer(
            config,
            data=request.data,
            partial=True
        )

        if not serializer.is_valid():
            return Response(
                {"success": False, "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer.save()

        logger.info(
            "Instance reconfigured",
            extra={'domain': config.domain, 'user_id': str(user.user_id)}
        )

        return Response({
            "success": True,
            "message": "Configurazione aggiornata"
        })
