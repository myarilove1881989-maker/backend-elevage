"""Credential epochs revoke online sessions without changing signed offline V1."""
from datetime import timedelta
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import Token
from .audit import record


class InitialPasswordToken(Token):
    token_type = 'initial-password-change'
    lifetime = timedelta(minutes=5)


class CredentialJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        # Existing unversioned sessions remain valid only before any rotation.
        if validated_token.get('credential_version', 1) != user.credential_version:
            raise AuthenticationFailed('SESSION_REVOKED')
        from .access_policy import active_membership
        active_membership(user)
        return user


def validate_new_password(user, password, *, must_differ=False):
    if len(password)<12:
        raise ValidationError({'new_password': 'Utilisez au moins 12 caractères.'})
    if must_differ and user.check_password(password):
        raise ValidationError({'new_password': 'Choisissez un mot de passe différent.'})
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        raise ValidationError({'new_password': exc.messages})


def invalidate_sessions(user):
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
    from .foundation_views import revoke_grants
    for token in OutstandingToken.objects.filter(user_id=user.pk):
        BlacklistedToken.objects.get_or_create(token=token)
    revoke_grants(user.memberships.get(exploitation_id=user.exploitation_id).offline_authorizations.all())


def rotate_credentials(user, *, password=None, initial_required=None):
    """Caller holds farm/user locks and a transactional SECURITY audit scope."""
    before={'credential_epoch': user.credential_version,
            'requires_initial_change': user.password_change_required}
    if password is not None:
        user.set_password(password)
    if initial_required is not None:
        user.password_change_required=initial_required
    user.credential_version+=1
    user.save(update_fields=['password','password_change_required','credential_version'])
    invalidate_sessions(user)
    from .models import PasswordResetCode
    PasswordResetCode.objects.filter(user=user,used_at__isnull=True).update(used_at=timezone.now())
    record(user,'UPDATE',before,{'credential_epoch':user.credential_version,
           'requires_initial_change':user.password_change_required},category='SECURITY')
