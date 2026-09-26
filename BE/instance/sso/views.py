import base64
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone as utc_timezone
from urllib.parse import urlencode

from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from application.services.jwt_token_service import JWTTokenService
from instance.permissions import is_instance_administrator
from .models import BakneyLogin, BakneyChallenge
from .protocol import CALLBACK, VERSION, OPAQUE, canonical_uuid, SSOError, decrypt, digest, encrypt, signature, upstream, normalize_origin, browser_cookie
from .service import (check_binding, consume_nonce, disconnect, local_user, locked_pairing,
                      redeemed_user, require_forwarding, rotate, snapshot, synchronize)


class PairingAdministrator(BasePermission):
    message = 'Only the instance owner or an administrator can administer Bakney pairing.'

    def has_permission(self, request, view):
        actor = getattr(request, 'authenticated_user', getattr(request, 'original_user', request.user))
        return is_instance_administrator(actor)


class HandoffThrottle(SimpleRateThrottle):
    rate = '30/min'
    scope = 'bakney-sso'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope + ':' + type(view).__name__, 'ident': self.get_ident(request)}


@method_decorator(sensitive_post_parameters(), name='dispatch')
class SecureView(APIView):
    throttle_classes = [HandoffThrottle]

    @transaction.atomic
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def handle_exception(self, exc):
        if isinstance(exc, SSOError):
            return Response({'error': exc.code}, status=exc.status)
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        response['Pragma'] = 'no-cache'
        response['Referrer-Policy'] = 'no-referrer'
        response['X-Frame-Options'] = 'DENY'
        return response


class PairingAdminView(SecureView):
    permission_classes = [PairingAdministrator]

    def get(self, request):
        pairing = locked_pairing()
        error = None
        if pairing.remote_pairing_id and pairing.state in ('pending', 'paired'):
            try:
                synchronize(pairing)
            except SSOError as exc:
                error = exc.code
        return Response({**snapshot(pairing), 'sync_error': error})

    @sensitive_variables()
    def post(self, request):
        pairing = locked_pairing()
        if (not isinstance(request.data, dict) or set(request.data) != {'action', 'pairing_id'}
                or request.data['pairing_id'] != str(pairing.pairing_id)):
            raise SSOError('stale_pairing', 409)
        action = request.data['action']
        if action == 'generate':
            secret = rotate(pairing)
            return Response({**snapshot(pairing), 'secret': secret})
        if action == 'disconnect':
            disconnect(pairing)
        elif action == 'sync':
            synchronize(pairing)
        else:
            raise SSOError('invalid_request')
        return Response(snapshot(pairing))


class PublicView(SecureView):
    authentication_classes = []
    permission_classes = [AllowAny]


class PairingChallengeView(PublicView):
    commit = False

    @sensitive_variables()
    def post(self, request):
        pairing = locked_pairing()
        check_binding(pairing)
        if pairing.state not in ('generated', 'pending') or pairing.remote_pairing_id:
            raise SSOError('invalid_pairing', 409)
        data = request.data
        fields = {'protocol', 'pairing_id', 'association_id', 'instance_id', 'origin',
                  'callback_uri', 'generation', 'nonce', 'expires_at'}
        if self.commit:
            fields.add('proof')
        if not isinstance(data, dict) or set(data) != fields:
            raise SSOError('invalid_request')
        if (data['protocol'] != VERSION
                or not canonical_uuid(data['pairing_id']) or not canonical_uuid(data['generation'])
                or data['association_id'] != str(pairing.association_id)
                or data['instance_id'] != (str(pairing.instance_id) if self.commit else '')
                or data['origin'] != pairing.origin or data['callback_uri'] != pairing.origin + CALLBACK
                or not isinstance(data['nonce'], str) or not re.fullmatch('[A-Za-z0-9_-]{43}', data['nonce'])
                or type(data['expires_at']) is not int
                or not 0 < data['expires_at'] - timezone.now().timestamp() <= 120):
            raise SSOError('invalid_request')
        payload = {key: value for key, value in data.items() if key != 'proof'}
        payload['instance_id'] = str(pairing.instance_id)
        context = digest('\n'.join([str(pairing.pairing_id)] + [payload[key] for key in (
            'pairing_id', 'association_id', 'instance_id', 'origin', 'callback_uri', 'generation')]))
        expiry = datetime.fromtimestamp(data['expires_at'], utc_timezone.utc)
        secret = decrypt(pairing.secret_encrypted)
        if self.commit:
            proof = data['proof']
            if (not isinstance(proof, str) or not re.fullmatch('[0-9a-f]{64}', proof)
                    or not hmac.compare_digest(proof, signature(secret, 'commit', payload))):
                raise SSOError('invalid_proof', 403)
            challenge = BakneyChallenge.objects.filter(digest=context, pairing_id=pairing.pairing_id,
                                                       expires_at__gt=timezone.now()).first()
            if not challenge:
                raise SSOError('invalid_pairing', 409)
            consume_nonce(digest(str(pairing.pairing_id) + ':commit:' + data['nonce']), expiry)
            pairing.remote_pairing_id = uuid.UUID(data['pairing_id'])
            pairing.remote_generation = uuid.UUID(data['generation'])
            # Bakney marks verified only after receiving this signed acknowledgement.
            # A later authenticated status check completes the local pairing.
            pairing.state = 'pending'
            pairing.save()
            BakneyChallenge.objects.filter(pairing_id=pairing.pairing_id).delete()
        else:
            consume_nonce(digest(str(pairing.pairing_id) + ':challenge:' + data['nonce']), expiry)
            BakneyChallenge.objects.update_or_create(digest=context, defaults={
                'pairing_id': pairing.pairing_id, 'expires_at': expiry})
            pairing.state = 'pending'
            pairing.save()
        return Response({'protocol': VERSION, 'instance_id': str(pairing.instance_id),
                         'proof': signature(secret, 'ack' if self.commit else 'challenge', payload)})


class PairingCommitView(PairingChallengeView):
    commit = True


def browser_origin(request, pairing):
    if normalize_origin(request.build_absolute_uri('/')) != pairing.origin:
        raise SSOError('revalidation_required', 409)


def browser_login(request, pairing):
    cookie_name, _ = browser_cookie(pairing)
    cookie = request.COOKIES.get(cookie_name, '')
    if not OPAQUE.fullmatch(cookie):
        raise SSOError('handoff_expired', 400)
    login = BakneyLogin.objects.select_for_update().filter(digest=digest(cookie), pairing_id=pairing.pairing_id).first()
    if not login or login.expires_at <= timezone.now():
        raise SSOError('handoff_expired', 400)
    return login


def current_users(request):
    """Consider both credentials, including a cookie changed by another tab."""
    authentication = JWTAuthentication()
    candidates = [request.COOKIES.get('BKN_AUTH', '')]
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        candidates.append(header[7:])
    users = set()
    for raw in candidates:
        if not raw:
            continue
        try:
            user = authentication.get_user(authentication.get_validated_token(raw))
            users.add(str(user.pk))
        except (AuthenticationFailed, InvalidToken, TokenError):
            pass
    return sorted(users)


class LoginStartView(PublicView):
    @sensitive_variables()
    def get(self, request):
        try:
            pairing = locked_pairing()
            browser_origin(request, pairing)
            require_forwarding(pairing)
            if (set(request.query_params) != {'protocol', 'pairing_id', 'attempt_id'}
                    or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)
                    or request.query_params['protocol'] != VERSION
                    or request.query_params['pairing_id'] != str(pairing.remote_pairing_id)
                    or not canonical_uuid(request.query_params['attempt_id'])):
                raise SSOError('invalid_handoff')
            browser_secret, state, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            cookie_name, cookie_secure = browser_cookie(pairing)
            previous = request.COOKIES.get(cookie_name, '')
            if OPAQUE.fullmatch(previous):
                BakneyLogin.objects.filter(digest=digest(previous)).delete()
            BakneyLogin.objects.create(digest=digest(browser_secret), pairing_id=pairing.pairing_id,
                state=state, remote_generation=pairing.remote_generation,
                attempt_id=uuid.UUID(request.query_params['attempt_id']), verifier_encrypted=encrypt(verifier), expires_at=timezone.now() + timedelta(minutes=5))
            query = urlencode({
                'attempt_id': request.query_params['attempt_id'],
                'callback_uri': pairing.origin + CALLBACK, 'state': state,
                'code_challenge_method': 'S256',
                'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
            })
            response = HttpResponseRedirect(pairing.ui_origin + '/handoff.html#' + query)
            response.set_cookie(cookie_name, browser_secret, max_age=300, secure=cookie_secure, httponly=True, samesite='Lax', path='/')
            return response
        except SSOError as exc:
            return HttpResponseRedirect('/#/bakney-login?error=' + exc.code)


class LoginCallbackView(PublicView):
    @sensitive_variables()
    def get(self, request):
        try:
            pairing = locked_pairing()
            browser_origin(request, pairing)
            check_binding(pairing)
            login = browser_login(request, pairing)
            state = request.query_params.get('state', '')
            code = request.query_params.get('code', '')
            if (set(request.query_params) != {'code', 'state'}
                    or any(len(request.query_params.getlist(key)) != 1 for key in ('code', 'state'))
                    or not OPAQUE.fullmatch(code) or not OPAQUE.fullmatch(state)
                    or not hmac.compare_digest(login.state, state) or login.stage != 'started'):
                raise SSOError('invalid_handoff')
            login.stage = 'failed'  # Consume before redemption; a lost response requires a fresh handoff.
            verifier = decrypt(login.verifier_encrypted)
            login.verifier_encrypted = ''
            login.save()
            require_forwarding(pairing)
            if login.remote_generation != pairing.remote_generation:
                raise SSOError('invalid_handoff')
            data = upstream(pairing, 'token', {'callback_uri': pairing.origin + CALLBACK,
                                              'code': code, 'code_verifier': verifier})
            user = redeemed_user(pairing, data)
            login.user_id, login.stage = user.pk, 'ready'
            login.expires_at = timezone.now() + timedelta(minutes=2)
            login.save()
            response = HttpResponseRedirect('/#/bakney-login')
            cookie_name, cookie_secure = browser_cookie(pairing)
            response.set_cookie(cookie_name, request.COOKIES[cookie_name], max_age=120, secure=cookie_secure,
                                httponly=True, samesite='Lax', path='/')
            return response
        except SSOError as exc:
            return HttpResponseRedirect('/#/bakney-login?error=' + exc.code)


class LoginSessionView(PublicView):
    def get(self, request):
        pairing = locked_pairing()
        browser_origin(request, pairing)
        check_binding(pairing)
        login = browser_login(request, pairing)
        if login.stage != 'ready':
            raise SSOError('handoff_expired')
        user = local_user(pairing, login.user_id)
        others = [value for value in current_users(request) if value != str(user.pk)]
        return Response({'user': {'id': str(user.pk), 'name': user.first_name or user.username},
                         'csrf_token': login.state, 'confirm_for': others})

    @sensitive_variables()
    def post(self, request):
        pairing = locked_pairing()
        browser_origin(request, pairing)
        check_binding(pairing)
        login = browser_login(request, pairing)
        if (request.headers.get('Origin') != pairing.origin
                or not hmac.compare_digest(request.headers.get('X-SSO-CSRF', '').encode(), login.state.encode())):
            raise SSOError('invalid_handoff', 403)
        if login.stage != 'ready':
            raise SSOError('handoff_expired')
        if not isinstance(request.data, dict) or set(request.data) != {'confirm_for'}:
            raise SSOError('invalid_request')
        others = [value for value in current_users(request) if value != str(login.user_id)]
        if request.data['confirm_for'] != others:
            raise SSOError('account_switch_required', 409)
        require_forwarding(pairing)
        if login.remote_generation != pairing.remote_generation:
            raise SSOError('invalid_handoff')
        user = local_user(pairing, login.user_id)
        tokens = JWTTokenService.generate_tokens_for_user(user)
        content = JWTTokenService.build_login_response(user, tokens)
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])
        login.stage = 'consumed'
        login.save(update_fields=['stage'])
        response = Response(content)
        cookie_name, cookie_secure = browser_cookie(pairing)
        response.set_cookie('BKN_AUTH', tokens['access_token'], httponly=True, secure=cookie_secure,
                            samesite='Strict', max_age=int(tokens['expires_in']), path='/')
        response.delete_cookie(cookie_name, path='/', samesite='Lax')
        return response
