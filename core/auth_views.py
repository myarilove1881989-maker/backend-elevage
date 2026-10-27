from django.contrib.auth import get_user_model
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer, TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenRefreshView, TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from .permissions import membership_for


class MembershipLoginSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        # Authenticate before generating tokens; disabled memberships get no pair.
        from django.contrib.auth import authenticate
        user = authenticate(request=self.context.get('request'),
                            username=attrs.get('username'), password=attrs.get('password'))
        from .access_policy import active_membership
        active_membership(user, allow_password_change=True)
        if user.password_change_required:
            from .credential_services import InitialPasswordToken
            token = InitialPasswordToken.for_user(user)
            token['credential_version'] = user.credential_version
            return {'password_change_required': True, 'change_token': str(token)}
        return super().validate(attrs)

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['credential_version'] = user.credential_version
        return token


class MembershipLoginView(TokenObtainPairView):
    serializer_class = MembershipLoginSerializer


class MembershipRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        token = RefreshToken(attrs['refresh'])
        user = get_user_model().objects.filter(pk=token['user_id']).first()
        if not user or not user.is_active:
            raise PermissionDenied('ACCOUNT_INACTIVE')
        if token.get('credential_version', 1) != user.credential_version:
            raise PermissionDenied('SESSION_REVOKED')
        from .access_policy import active_membership
        active_membership(user)
        return super().validate(attrs)


class MembershipRefreshView(TokenRefreshView):
    serializer_class = MembershipRefreshSerializer


@api_view(['POST'])
def logout(request):
    try:
        token = RefreshToken(request.data.get('refresh', ''))
        if str(token['user_id']) != str(request.user.pk):
            raise PermissionDenied('REFRESH_NOT_OWNED')
        token.blacklist()
    except TokenError:
        raise ValidationError('REFRESH_INVALID')
    return Response(status=204)
