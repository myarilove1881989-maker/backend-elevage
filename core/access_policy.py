"""Tenant-bound authorization; callers retain their transaction and business checks."""
from rest_framework.exceptions import PermissionDenied
from .permission_catalog import (PERMISSIONS, OWNER_ONLY, BASE_READS, BUSINESS_ACTIONS,
                                 PERMISSION_CONTRACT_VERSION, operation_action)


def active_membership(actor, farm_id=None, *, allow_password_change=False):
    from .models import ExploitationMembership
    if not actor or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied('ACCOUNT_INACTIVE')
    farm_id = farm_id or actor.exploitation_id
    if not farm_id or actor.exploitation_id != farm_id:
        raise PermissionDenied('MEMBERSHIP_CONTEXT_MISMATCH')
    member = ExploitationMembership.objects.select_related('exploitation', 'user').filter(
        user_id=actor.pk, exploitation_id=farm_id, is_active=True).first()
    if not member:
        raise PermissionDenied('MEMBERSHIP_INACTIVE')
    if not member.user.is_active:
        raise PermissionDenied('ACCOUNT_INACTIVE')
    if member.user.password_change_required and not allow_password_change:
        raise PermissionDenied('PASSWORD_CHANGE_REQUIRED')
    if member.role not in {'OWNER', 'OPERATEUR'}:
        raise PermissionDenied('ROLE_INVALID')
    if member.role == 'OWNER' and member.exploitation.proprietaire_id != actor.pk:
        raise PermissionDenied('OWNER_CONTEXT_MISMATCH')
    if member.role == 'OPERATEUR' and member.exploitation.proprietaire_id == actor.pk:
        raise PermissionDenied('OWNER_CONTEXT_MISMATCH')
    return member


def effective_permissions(member):
    if not member.is_active or not member.user.is_active:
        return frozenset()
    if member.role == 'OWNER':
        if member.exploitation.proprietaire_id != member.user_id:
            return frozenset()
        return PERMISSIONS - BUSINESS_ACTIONS - {'agenda.progress'}
    return BASE_READS | frozenset(member.action_permissions.values_list('code', flat=True))


def authorize(actor, farm, action, resource=None, *, device=None):
    farm_id = farm.pk if hasattr(farm, 'pk') else farm
    member = active_membership(actor, farm_id)
    if action not in PERMISSIONS:
        raise PermissionDenied('UNKNOWN_PERMISSION')
    if resource is not None:
        resource_farm = getattr(resource, 'exploitation_id', None)
        if resource_farm is None and hasattr(resource, 'lot'):
            resource_farm = resource.lot.exploitation_id
        if resource_farm != farm_id:
            raise PermissionDenied('RESOURCE_OUTSIDE_FARM')
    if member.role == 'OWNER' and action in BUSINESS_ACTIONS:
        raise PermissionDenied('TERRAIN_OPERATOR_REQUIRED')
    if member.role != 'OWNER' and action in OWNER_ONLY:
        raise PermissionDenied('OWNER_REQUIRED')
    if action not in effective_permissions(member):
        raise PermissionDenied('ACTION_PERMISSION_REQUIRED')
    if device is not None and (device.exploitation_id != farm_id or device.status != 'ACTIVE'
                              or not device.is_primary_writer
                              or device.write_generation != member.exploitation.write_generation):
        raise PermissionDenied('ACTIVE_PRIMARY_DEVICE_REQUIRED')
    return member


def permission_contract(actor):
    member = active_membership(actor)
    return {'version': PERMISSION_CONTRACT_VERSION, 'rights_version': member.version,
            'user': actor.pk, 'exploitation': member.exploitation_id, 'role': member.role,
            'permissions': sorted(effective_permissions(member))}


def ordinary_request_action(request):
    """Closed resolver for actual REST mutations. Unknown routes fail closed."""
    from .models import Mouvement
    path = request.path.rstrip('/')
    suffix = path.split('/api/', 1)[-1]
    method = request.method
    if suffix.startswith('depenses/cancel/'):
        return 'expenses.delete'
    if suffix == 'mouvements/create' or suffix.startswith('mouvements/delete/'):
        if method == 'POST':
            kind = request.data.get('type_mouvement')
        else:
            movement = Mouvement.objects.filter(pk=request.parser_context['kwargs'].get('pk'),
                lot__exploitation_id=request.user.exploitation_id).first()
            if movement is None:
                from rest_framework.exceptions import NotFound
                raise NotFound()
            kind = movement.type_mouvement
        from .permission_catalog import ENTITY_DOMAINS
        domain = ENTITY_DOMAINS.get(kind)
        if domain:
            return domain + ('.create' if method == 'POST' else '.delete')
        raise PermissionDenied('UNKNOWN_BUSINESS_ACTION')
    prefixes = {'achats': 'purchases', 'depenses': 'expenses',
        'clients/create': 'clients', 'payments/create': 'cash', 'especes': 'species',
        'oeufs/collectes': 'egg_collections', 'oeufs/ventes': 'egg_sales',
        'oeufs/alimentation': 'feed', 'alimentation/distributions': 'feed',
        'production/pesees': 'weights'}
    for prefix, domain in prefixes.items():
        if suffix == prefix or suffix.startswith(prefix + '/'):
            action = {'POST': 'create', 'PUT': 'update', 'PATCH': 'update', 'DELETE': 'delete'}.get(method)
            code = f'{domain}.{action}'
            if code in PERMISSIONS:
                return code
    raise PermissionDenied('UNKNOWN_BUSINESS_ACTION')


def authorize_prepared_offline(actor, farm, entity_type, operation_type, contract):
    """Separate future policy adapter; signed V1 ingestion is deliberately not rewired."""
    action = operation_action(entity_type, operation_type)
    if (action is None or not isinstance(contract, dict)
            or type(contract.get('version')) is not int
            or contract.get('version') != PERMISSION_CONTRACT_VERSION):
        raise PermissionDenied('OFFLINE_PERMISSION_CONTRACT_REQUIRED')
    codes = contract.get('permissions')
    if (not isinstance(codes, list) or len(codes) > len(PERMISSIONS)
            or any(not isinstance(code, str) or code not in PERMISSIONS for code in codes)
            or len(set(codes)) != len(codes)):
        raise PermissionDenied('OFFLINE_PERMISSION_CONTRACT_REQUIRED')
    member = authorize(actor, farm, action)
    if (contract.get('user') != actor.pk or contract.get('exploitation') != member.exploitation_id
            or contract.get('rights_version') != member.version
            or contract.get('role') != member.role or action not in codes):
        raise PermissionDenied('AUTHOR_RIGHTS_CHANGED')
    return member


def authorize_business_service(actor, farm, action, resource=None):
    """Offline services recheck original context and current individual rights.

    Only a trusted server audit scope can designate historical reconciliation.
    No request parameter or client-supplied grant can create that scope.
    """
    from .audit import current_actor_context
    context = current_actor_context()
    farm_id = farm.pk if hasattr(farm, 'pk') else farm
    if not actor or getattr(actor, 'exploitation_id', None) != farm_id:
        raise PermissionDenied('RESOURCE_OUTSIDE_FARM')
    from .models import Exploitation
    from django.db import connection
    farms = Exploitation.objects.all()
    if connection.in_atomic_block:
        farms = farms.select_for_update()
    actual_farm = farms.get(pk=farm_id)
    if actual_farm.proprietaire_id == actor.pk:
        raise PermissionDenied('TERRAIN_OPERATOR_REQUIRED')
    if context and context.source in {'OFFLINE', 'RECONCILE'}:
        if context.author_id != actor.pk or context.exploitation_id != farm_id or not context.operation_id:
            raise PermissionDenied('AUTHOR_CONTEXT_INVALID')
        if resource is not None:
            resource_farm = getattr(resource, 'exploitation_id', None)
            if resource_farm is None and hasattr(resource, 'lot'):
                resource_farm = resource.lot.exploitation_id
            if resource_farm != farm_id:
                raise PermissionDenied('RESOURCE_OUTSIDE_FARM')
        from .models import TerrainSubmission
        from .offline_permissions import has_fine_contract, permission_failure
        original = TerrainSubmission.objects.select_related(
            'offline_authorization__membership__user', 'offline_authorization__membership__exploitation'
        ).filter(client_operation_id=context.operation_id, exploitation_id=farm_id).first()
        if original is None or original.author_user_id != actor.pk:
            raise PermissionDenied('AUTHOR_CONTEXT_INVALID')
        if context.source == 'OFFLINE':
            reason = permission_failure(original.offline_authorization, original, action=action)
            if reason:
                raise PermissionDenied(reason)
        elif has_fine_contract(original.offline_authorization):
            raise PermissionDenied('FINE_REVIEW_NOT_ACTIVATED')
        return  # Explicit historical reconciliation remains a separate owner decision.
    authorize(actor, farm_id, action, resource)
