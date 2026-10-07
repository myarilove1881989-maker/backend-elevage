"""Foundation only: no ingestion endpoint or impersonation of request.user."""
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from .models import OfflineAuthorization
from .audit import ActorContext


def validated_offline_context(*, authorization_id, verified_device, transport_identity,
                              decision_actor_id=None):
    grant = OfflineAuthorization.objects.select_related(
        'membership__user', 'membership__exploitation', 'device').get(pk=authorization_id)
    member = grant.membership
    farm = member.exploitation
    if (grant.revoked_at or grant.expires_at <= timezone.now() or
        grant.issued_at > timezone.now() or not member.is_active or not member.user.is_active or
        member.role != 'OPERATEUR' or member.user.exploitation_id != farm.pk or
        not farm.offline_policy_enabled or grant.rights_version != member.version or
        grant.device_id != verified_device.pk or grant.device.exploitation_id != farm.pk or
        grant.device.status != 'ACTIVE' or not grant.device.is_primary_writer or
        grant.write_generation != farm.write_generation or
        grant.device.write_generation != farm.write_generation):
        raise PermissionDenied('OFFLINE_AUTHORIZATION_INVALID')
    return ActorContext(member.user_id, farm.pk, grant.device_id, transport_identity,
                        decision_actor_id)
