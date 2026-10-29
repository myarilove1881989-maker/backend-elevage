"""Public validation is non-consuming; only password confirmation consumes a link."""
from django.core import signing
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import PasswordResetCode
from .reset_links import decode_link
from .reset_limits import allow_reset

@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def validate_link(request):
    if not allow_reset(request, str(request.data.get('link_token', '')), 'link'):
        return Response({'message': 'Réessayez plus tard.'}, status=429)
    try:
        payload = decode_link(str(request.data.get('link_token', '')))
        reset = PasswordResetCode.objects.select_related('user').filter(pk=payload['code_id'], user_id=payload['user_id']).first()
        valid = reset is not None and reset.is_valid and reset.user.is_active
    except (signing.BadSignature, signing.SignatureExpired):
        valid = False
    if not valid:
        return Response({'message': 'Lien invalide, expiré ou déjà utilisé. Demandez un nouveau lien.'}, status=400)
    return Response({'valid': True})
