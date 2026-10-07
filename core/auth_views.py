from django.contrib.auth import get_user_model
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from .permissions import membership_for


class MembershipRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        token = RefreshToken(attrs['refresh'])
        user = get_user_model().objects.filter(pk=token['user_id']).first()
        if not user or not user.is_active:
            raise PermissionDenied('ACCOUNT_INACTIVE')
        member = membership_for(user)
        if (member and not member.is_active) or (user.exploitation_id and
                user.exploitation.offline_policy_enabled and member is None):
            raise PermissionDenied('MEMBERSHIP_INACTIVE')
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
