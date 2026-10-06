"""One server policy for legacy APIs, new APIs and capability discovery."""
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS
from .models import ExploitationMembership, DeviceRegistration


def membership_for(user):
    return ExploitationMembership.objects.filter(user=user, exploitation_id=user.exploitation_id).first()


def require_member(user):
    membership = membership_for(user)
    if not user.is_active or membership is None or not membership.is_active:
        raise PermissionDenied('MEMBERSHIP_INACTIVE')
    if membership.user.exploitation_id != membership.exploitation_id:
        raise PermissionDenied('MEMBERSHIP_CONTEXT_MISMATCH')
    return membership


def require_owner(user):
    member = require_member(user)
    if member.role != 'OWNER' or member.exploitation.proprietaire_id != user.pk:
        raise PermissionDenied('OWNER_REQUIRED')
    return member


def capabilities_for(user):
    member = membership_for(user)
    active = bool(user.is_active and member and member.is_active)
    owner = active and member.role == 'OWNER' and member.exploitation.proprietaire_id == user.pk
    legacy = bool(user.exploitation_id and not user.exploitation.offline_policy_enabled)
    writer = active and (legacy or member.role == 'OPERATEUR')
    return {
        'can_view_business': active, 'can_view_finance': active,
        'can_create_sale': writer, 'can_create_expense': writer,
        'can_create_terrain_operation': writer, 'requires_primary_device': not legacy,
        'can_manage_users': owner, 'can_manage_devices': owner,
        'can_view_audit': owner, 'can_create_task': owner or (active and legacy),
        'can_assign_task': owner, 'can_progress_task': active and member.role == 'OPERATEUR',
        'can_reconcile': active and member.can_reconcile,
    }


class HasExploitation(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.exploitation_id:
            return False
        member = membership_for(user)
        # A disabled membership is never revived by legacy mode.
        if member and not member.is_active:
            raise PermissionDenied('MEMBERSHIP_INACTIVE')
        if not user.exploitation.offline_policy_enabled:
            return True
        member = require_member(user)
        if request.method in SAFE_METHODS:
            return True
        if getattr(view, 'agenda_endpoint', False):
            if member.role == 'OWNER':
                return True
            if request.method in ('PATCH', 'PUT'):
                return True  # object/field restrictions enforced by Task serializer
            raise PermissionDenied('AGENDA_PLANNING_OWNER_ONLY')
        if member.role != 'OPERATEUR':
            raise PermissionDenied('TERRAIN_OPERATOR_REQUIRED')
        from .device_services import verify_request_device
        request.verified_device = verify_request_device(request, primary=True)
        return True
