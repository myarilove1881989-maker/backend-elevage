"""Tenant-scoped administration, proof of device possession and grant foundation."""
from datetime import timedelta
import uuid
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework import serializers
from .models import (Exploitation, ExploitationMembership, DeviceRegistration,
                     DeviceChallenge, OfflineAuthorization, AuditEvent)
from .permissions import require_member, require_owner, capabilities_for
from .device_services import load_public_key, verify_request_device
from .audit import ActorContext, audit_scope, record, snapshot
from .request_context import online_context


class MemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    class Meta:
        model = ExploitationMembership
        fields = ('id', 'user', 'username', 'role', 'is_active', 'can_reconcile',
                  'version', 'created_at', 'updated_at', 'disabled_at')
        read_only_fields = fields


class OperatorInput(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, default='')
    password = serializers.CharField(write_only=True, max_length=256)


class MemberUpdate(serializers.Serializer):
    is_active = serializers.BooleanField(required=False)
    can_reconcile = serializers.BooleanField(required=False)


class DeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceRegistration
        fields = ('id', 'installation_uuid', 'display_name', 'platform', 'status',
                  'is_primary_writer', 'write_generation', 'registered_at',
                  'last_seen_at', 'last_full_sync_at', 'revoked_at', 'revocation_reason')
        read_only_fields = fields


class DeviceInput(serializers.Serializer):
    installation_uuid = serializers.UUIDField()
    display_name = serializers.CharField(max_length=100)
    public_key = serializers.CharField(max_length=1024)
    platform = serializers.ChoiceField(choices=['ANDROID'], default='ANDROID')

    def validate_public_key(self, value):
        load_public_key(value)
        return value


class ChallengeInput(serializers.Serializer):
    device_id = serializers.IntegerField(min_value=1)
    purpose = serializers.ChoiceField(choices=['WRITE', 'ACTIVATE', 'REPLACE', 'GRANT', 'RECOVER'])


@api_view(['GET'])
def capabilities(request):
    member = require_member(request.user)
    device = DeviceRegistration.objects.filter(exploitation=member.exploitation,
                                               status='ACTIVE', is_primary_writer=True).first()
    return Response({'user': request.user.pk, 'exploitation': member.exploitation_id,
                     'role': member.role, 'membership': MemberSerializer(member).data,
                     'offline_policy_enabled': member.exploitation.offline_policy_enabled,
                     'capabilities': capabilities_for(request.user),
                     'primary_device': DeviceSerializer(device).data if device else None,
                     'write_generation': member.exploitation.write_generation,
                     'offline_authorization_days': settings.OFFLINE_AUTHORIZATION_DAYS})


@api_view(['GET', 'POST'])
def members(request):
    owner = require_owner(request.user)
    if request.method == 'GET':
        return Response(MemberSerializer(ExploitationMembership.objects.filter(
            exploitation=owner.exploitation).select_related('user'), many=True).data)
    data = OperatorInput(data=request.data)
    data.is_valid(raise_exception=True)
    values = data.validated_data
    User = get_user_model()
    candidate = User(username=values['username'], email=values['email'])
    try:
        validate_password(values['password'], candidate)
    except DjangoValidationError as exc:
        raise ValidationError({'password': exc.messages})
    with audit_scope(online_context(request)):
        Exploitation.objects.select_for_update().get(pk=owner.exploitation_id)
        require_owner(request.user)
        if User.objects.filter(username=values['username']).exists():
            raise ValidationError({'username': 'Nom de compte indisponible.'})
        if values['email'] and User.objects.filter(email__iexact=values['email']).exists():
            raise ValidationError({'email': 'Adresse indisponible.'})
        # Explicit farm prevents the historical registration path creating another farm.
        user = User.objects.create_user(exploitation=owner.exploitation, **values)
        member = ExploitationMembership.objects.get(user=user, exploitation=owner.exploitation)
        return Response(MemberSerializer(member).data, status=201)


def revoke_grants(queryset):
    for grant in queryset.filter(revoked_at__isnull=True):
        grant.revoked_at = timezone.now()
        grant.save(update_fields=['revoked_at'])


@api_view(['PATCH'])
def member_detail(request, pk):
    owner = require_owner(request.user)
    data = MemberUpdate(data=request.data)
    data.is_valid(raise_exception=True)
    if set(request.data) - set(data.fields):
        raise ValidationError('Seuls activation et droit de réconciliation sont modifiables.')
    with audit_scope(online_context(request)):
        Exploitation.objects.select_for_update().get(pk=owner.exploitation_id)
        require_owner(request.user)
        member = get_object_or_404(ExploitationMembership.objects.select_for_update(),
                                  pk=pk, exploitation=owner.exploitation, role='OPERATEUR')
        for name, value in data.validated_data.items():
            setattr(member, name, value)
        member.disabled_at = None if member.is_active else timezone.now()
        member.changed_by = request.user
        member.version += 1
        member.save()
        revoke_grants(member.offline_authorizations.all())
        if not member.is_active:
            from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
            for token in OutstandingToken.objects.filter(user_id=member.user_id):
                BlacklistedToken.objects.get_or_create(token=token)
        return Response(MemberSerializer(member).data)


@api_view(['GET', 'POST'])
def devices(request):
    owner = require_owner(request.user)
    if request.method == 'GET':
        return Response(DeviceSerializer(DeviceRegistration.objects.filter(
            exploitation=owner.exploitation), many=True).data)
    data = DeviceInput(data=request.data)
    data.is_valid(raise_exception=True)
    with audit_scope(online_context(request)):
        Exploitation.objects.select_for_update().get(pk=owner.exploitation_id)
        require_owner(request.user)
        if DeviceRegistration.objects.filter(installation_uuid=data.validated_data['installation_uuid']).exists():
            raise ValidationError('Installation déjà enregistrée ; identité non réutilisable.')
        device = DeviceRegistration.objects.create(exploitation=owner.exploitation, **data.validated_data)
        return Response(DeviceSerializer(device).data, status=201)


@api_view(['POST'])
def challenge(request):
    member = require_member(request.user)
    data = ChallengeInput(data=request.data)
    data.is_valid(raise_exception=True)
    device = get_object_or_404(DeviceRegistration, pk=data.validated_data['device_id'],
                               exploitation=member.exploitation)
    purpose = data.validated_data['purpose']
    if purpose == 'RECOVER':
        require_owner(request.user)
        if device.status != 'REVOKED' or device.is_primary_writer:
            raise ValidationError('Appareil révoqué requis pour une récupération explicite.')
    elif purpose in ('ACTIVATE', 'REPLACE'):
        require_owner(request.user)
        if device.status != 'PENDING':
            raise ValidationError('Appareil en attente requis.')
    elif device.status != 'ACTIVE' or not device.is_primary_writer:
        raise PermissionDenied('Appareil principal actif requis.')
    # Possession is proven by an Ed25519 or Android Keystore P-256 signature.
    obj = DeviceChallenge.objects.create(device=device, user=request.user, purpose=purpose,
                                         expires_at=timezone.now() + timedelta(minutes=5))
    return Response({'id': str(obj.pk), 'device_id': device.pk, 'user_id': request.user.pk,
                     'purpose': purpose, 'expires_at': obj.expires_at,
                     'signature_contract': 'ELEVAGE-DEVICE-V1'}, status=201)


@api_view(['POST'])
def device_transition(request, pk, action):
    owner = require_owner(request.user)
    # Cache exact bytes before parsing: signature covers the transmitted body.
    request.body
    if action not in ('activate', 'replace', 'revoke'):
        raise ValidationError('Transition inconnue.')
    with audit_scope(ActorContext(request.user.pk, owner.exploitation_id, pk), reason_code=action.upper()):
        farm = Exploitation.objects.select_for_update().get(pk=owner.exploitation_id)
        require_owner(request.user)
        device = get_object_or_404(DeviceRegistration.objects.select_for_update(), pk=pk, exploitation=farm)
        if action in ('activate', 'replace'):
            proven = verify_request_device(request, purpose=action.upper(), allow_pending=True)
            if proven.pk != device.pk or device.status != 'PENDING':
                raise PermissionDenied('Preuve du nouvel appareil requise.')
            primary = DeviceRegistration.objects.filter(exploitation=farm, status='ACTIVE', is_primary_writer=True)
            if action == 'activate' and primary.exists():
                raise ValidationError('Un principal existe ; utiliser replace.')
            if action == 'replace':
                for previous in primary:
                    previous.status = 'REVOKED'
                    previous.is_primary_writer = False
                    previous.revoked_at = timezone.now()
                    previous.revoked_by = request.user
                    previous.revocation_reason = 'REPLACED'
                    previous.save()
                    revoke_grants(previous.offline_authorizations.all())
                farm.write_generation += 1
                before = snapshot(farm)
                before['write_generation'] -= 1
                farm.save(update_fields=['write_generation'])
                record(farm, 'WRITE_GENERATION_CHANGED', before, snapshot(farm), category='SECURITY')
            device.status = 'ACTIVE'
            device.is_primary_writer = True
            device.write_generation = farm.write_generation
            device.activated_by = request.user
        else:
            if device.status == 'REVOKED':
                raise ValidationError('Appareil déjà révoqué.')
            reason = request.data.get('reason', '')
            if not isinstance(reason, str) or len(reason) > 255:
                raise ValidationError('Motif limité à 255 caractères.')
            device.status = 'REVOKED'
            device.is_primary_writer = False
            device.revoked_at = timezone.now()
            device.revoked_by = request.user
            device.revocation_reason = reason
            revoke_grants(device.offline_authorizations.all())
        device.save()
        return Response(DeviceSerializer(device).data)


@api_view(['POST'])
def enable_policy(request):
    owner = require_owner(request.user)
    with audit_scope(online_context(request), reason_code='POLICY_ENABLED'):
        farm = Exploitation.objects.select_for_update().get(pk=owner.exploitation_id)
        require_owner(request.user)
        if not DeviceRegistration.objects.filter(exploitation=farm, status='ACTIVE', is_primary_writer=True,
                                                 write_generation=farm.write_generation).exists():
            raise ValidationError('Appareil principal avec preuve validée requis.')
        if not ExploitationMembership.objects.filter(exploitation=farm, role='OPERATEUR', is_active=True,
                                                     user__is_active=True, user__exploitation=farm).exists():
            raise ValidationError('Au moins un opérateur actif requis.')
        if farm.memberships.exclude(user__exploitation=farm).exists():
            raise ValidationError('Corriger les appartenances historiques incohérentes avant activation.')
        before = snapshot(farm)
        farm.offline_policy_enabled = True
        farm.save(update_fields=['offline_policy_enabled'])
        record(farm, 'POLICY_ENABLED', before, snapshot(farm), category='SECURITY')
        return Response({'offline_policy_enabled': True})


@api_view(['POST'])
def offline_authorization(request):
    member = require_member(request.user)
    if member.role != 'OPERATEUR' or not member.exploitation.offline_policy_enabled:
        raise PermissionDenied('Politique activée et opérateur requis.')
    request.body
    pem = settings.OFFLINE_SIGNING_PRIVATE_KEY
    if not pem:
        return Response({'detail': 'OFFLINE_SIGNING_KEY_NOT_CONFIGURED'}, status=503)
    try:
        key = serialization.load_pem_private_key(pem.encode('ascii'), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        return Response({'detail': 'OFFLINE_SIGNING_KEY_INVALID'}, status=503)
    with transaction.atomic():
        farm = Exploitation.objects.select_for_update().get(pk=member.exploitation_id)
        member = require_member(request.user)
        device = verify_request_device(request, purpose='GRANT', primary=True)
        request.verified_device = device
        return issue_grant(request, member, farm, device, key)


def issue_grant(request, member, farm, device, key):
    with audit_scope(online_context(request)):
        caps = capabilities_for(request.user)
        grant = OfflineAuthorization.objects.create(membership=member, device=device, capabilities=caps,
            rights_version=member.version, write_generation=farm.write_generation,
            expires_at=timezone.now() + timedelta(days=settings.OFFLINE_AUTHORIZATION_DAYS))
        payload = {'iss': 'elevage-offline', 'aud': 'elevage-device', 'typ': 'offline-authorization',
                   'jti': str(grant.pk), 'sub': str(request.user.pk), 'membership_id': member.pk,
                   'exploitation_id': farm.pk, 'device_id': device.pk, 'capabilities': caps,
                   'rights_version': member.version, 'write_generation': farm.write_generation,
                   'iat': int(grant.issued_at.timestamp()), 'exp': int(grant.expires_at.timestamp())}
        token = jwt.encode(payload, key, algorithm='EdDSA', headers={'kid': settings.OFFLINE_SIGNING_KEY_ID})
        public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo).decode('ascii')
        return Response({'authorization': token, 'public_key': public,
                         'key_id': settings.OFFLINE_SIGNING_KEY_ID, 'expires_at': grant.expires_at}, status=201)


class AuditSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = '__all__'
        read_only_fields = [f.name for f in AuditEvent._meta.fields]


class AuditFilters(serializers.Serializer):
    actor_user_id = serializers.IntegerField(min_value=1, required=False)
    decision_actor_id = serializers.IntegerField(min_value=1, required=False)
    operation_id = serializers.UUIDField(required=False)
    include_related_object = serializers.BooleanField(required=False, default=False)
    correlation_id = serializers.UUIDField(required=False)
    source = serializers.ChoiceField(choices=['ONLINE', 'OFFLINE', 'RECONCILE', 'RECOVERY'], required=False)
    since = serializers.DateTimeField(required=False)
    until = serializers.DateTimeField(required=False)

    def validate(self, data):
        if data.get('include_related_object') and 'operation_id' not in data:
            raise ValidationError('RELATED_OBJECT_REQUIRES_OPERATION')
        if 'since' in data and 'until' in data and data['since'] > data['until']:
            raise ValidationError('INVALID_AUDIT_DATE_RANGE')
        return data


@api_view(['GET'])
def audit_events(request):
    owner = require_owner(request.user)
    from rest_framework.pagination import PageNumberPagination
    pagination = PageNumberPagination()
    pagination.page_size = 100
    qs = AuditEvent.objects.filter(exploitation_id=owner.exploitation_id)
    filters = AuditFilters(data=request.query_params)
    filters.is_valid(raise_exception=True)
    values = dict(filters.validated_data)
    related = values.pop('include_related_object')
    if related:
        operation_id = values.pop('operation_id')
        # Derive affected objects exclusively from this farm's immutable audit.
        # A declaration's untrusted reference cannot widen access to another object.
        object_events = AuditEvent.objects.filter(
            exploitation_id=owner.exploitation_id, operation_id=operation_id,
            category__in=['BUSINESS', 'AGENDA'],
            entity_type=OuterRef('entity_type'), entity_id=OuterRef('entity_id'),
        ).exclude(entity_type__in=['core.terrainsubmission', 'core.terrainoutcome', 'core.terraindecision'])
        qs = qs.annotate(related_object=Exists(object_events)).filter(
            Q(operation_id=operation_id) | Q(related_object=True))
    if 'since' in values:
        qs = qs.filter(received_at__gte=values.pop('since'))
    if 'until' in values:
        qs = qs.filter(received_at__lte=values.pop('until'))
    qs = qs.filter(**values)
    for name in ('entity_type', 'entity_id', 'action', 'category'):
        if request.query_params.get(name):
            qs = qs.filter(**{name: request.query_params[name]})
    page = pagination.paginate_queryset(qs, request)
    return pagination.get_paginated_response(AuditSerializer(page, many=True).data)
