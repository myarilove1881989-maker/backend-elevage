from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, authentication_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from .audit import audit_scope, ActorContext
from .models import Exploitation, ExploitationMembership
from .access_policy import active_membership
from .permissions import require_owner
from .credential_services import InitialPasswordToken, validate_new_password, rotate_credentials


class ChangeThrottle(AnonRateThrottle):
    rate='30/hour'


class PasswordInput(serializers.Serializer):
    new_password=serializers.CharField(write_only=True,max_length=256,trim_whitespace=False)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([ChangeThrottle])
def change_initial_password(request):
    if set(request.data)-{'change_token','new_password'}:
        raise ValidationError('Champ non autorisé.')
    data=PasswordInput(data=request.data);data.is_valid(raise_exception=True)
    try:
        token=InitialPasswordToken(request.data.get('change_token',''))
    except TokenError:
        raise PermissionDenied('CHANGE_AUTHORIZATION_INVALID')
    user=get_object_or_404(get_user_model(),pk=token.get('user_id'))
    with audit_scope(ActorContext(user.pk,user.exploitation_id)):
        Exploitation.objects.select_for_update().get(pk=user.exploitation_id)
        user=get_user_model().objects.select_for_update().get(pk=user.pk)
        member=active_membership(user,allow_password_change=True)
        if not user.password_change_required or token.get('credential_version')!=user.credential_version:
            raise PermissionDenied('CHANGE_AUTHORIZATION_INVALID')
        validate_new_password(user,data.validated_data['new_password'],must_differ=True)
        rotate_credentials(user,password=data.validated_data['new_password'],initial_required=False)
        member.version+=1;member.changed_by=user;member.save()
    return Response({'password_changed':True,'login_required':True})


class ResetInput(PasswordInput):
    expected_version=serializers.IntegerField(min_value=1)


@api_view(['POST'])
def reset_operator_password(request,pk):
    owner=require_owner(request.user)
    data=ResetInput(data=request.data);data.is_valid(raise_exception=True)
    if set(request.data)-set(data.fields):
        raise ValidationError('Champ administratif non autorisé.')
    with audit_scope(ActorContext(request.user.pk,owner.exploitation_id)):
        Exploitation.objects.select_for_update().get(pk=owner.exploitation_id)
        require_owner(request.user)
        member=get_object_or_404(ExploitationMembership.objects.select_for_update(),pk=pk,
            exploitation_id=owner.exploitation_id,role='OPERATEUR')
        if member.version!=data.validated_data['expected_version']:
            raise ValidationError('MEMBERSHIP_VERSION_CONFLICT')
        user=get_user_model().objects.select_for_update().get(pk=member.user_id)
        validate_new_password(user,data.validated_data['new_password'],must_differ=True)
        rotate_credentials(user,password=data.validated_data['new_password'],initial_required=True)
        member.version+=1;member.changed_by=request.user;member.save()
        from .foundation_views import MemberSerializer
        return Response(MemberSerializer(member).data)
