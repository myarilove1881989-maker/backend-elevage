"""Explicit Owner recovery from a revoked device, with no writer reactivation."""
import base64
import uuid
from cryptography.exceptions import InvalidSignature
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .device_services import load_public_key, verify_signature, request_message
from .models import DeviceChallenge, DeviceRegistration, Exploitation
from .permissions import require_owner
from .terrain_transport import StrictInput, SubmissionInput, receive_declarations


class RecoveryInput(StrictInput):
    reason = serializers.CharField(min_length=3, max_length=10000)
    operations = SubmissionInput(many=True, allow_empty=False, max_length=50)


def verify_recovery_device(request):
    """Separate from verify_request_device: WRITE still rejects revoked keys."""
    member = require_owner(request.user)
    farm = Exploitation.objects.select_for_update().get(pk=member.exploitation_id)
    require_owner(request.user)
    try:
        device_id = int(request.headers.get('X-Elevage-Device', ''))
        challenge_id = uuid.UUID(request.headers.get('X-Elevage-Challenge', ''))
        signature = base64.b64decode(request.headers.get('X-Elevage-Signature', ''), validate=True)
        device = DeviceRegistration.objects.select_for_update().get(pk=device_id, exploitation=farm)
        challenge = DeviceChallenge.objects.select_for_update().get(
            pk=challenge_id, device=device, user=request.user, purpose='RECOVER')
    except (ValueError, TypeError, DeviceRegistration.DoesNotExist, DeviceChallenge.DoesNotExist):
        raise PermissionDenied('RECOVERY_DEVICE_PROOF_REQUIRED')
    if device.status != 'REVOKED' or device.is_primary_writer:
        raise PermissionDenied('REVOKED_DEVICE_REQUIRED')
    if challenge.consumed_at or challenge.expires_at <= timezone.now():
        raise PermissionDenied('DEVICE_CHALLENGE_EXPIRED_OR_USED')
    if request.method != 'POST' or request.get_full_path() != '/api/offline/recovery/':
        raise PermissionDenied('RECOVERY_DEVICE_PROOF_INVALID')
    try:
        verify_signature(load_public_key(device.public_key), signature, request_message(request, challenge))
    except (InvalidSignature, ValueError):
        raise PermissionDenied('RECOVERY_DEVICE_PROOF_INVALID')
    device.exploitation = farm
    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=['consumed_at'])
    return device


@transaction.atomic
def recover(request, data):
    device = verify_recovery_device(request)
    return receive_declarations(device, data['operations'], recovery_actor_id=request.user.pk,
        recovery_reason=data['reason'])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def recovery(request):
    # Exact transmitted bytes must be available to the signed proof.
    if len(request.body) > 262144:
        raise serializers.ValidationError('TRANSPORT_BODY_TOO_LARGE')
    data = RecoveryInput(data=request.data)
    data.is_valid(raise_exception=True)
    _, received = recover(request, data.validated_data)
    # Deliberately no process_pending: every newly recovered original requires
    # a separate reasoned decision, even if a newer device is active.
    return Response({'receipts':received})
