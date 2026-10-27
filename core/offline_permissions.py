"""Fine contract inside the existing EdDSA grant; legacy absence is explicit.

Stored grants are server-issued provenance, never supplied by a submission.
Expiry gates new local writes; delayed originals use their original recorded time.
"""
from .permission_catalog import PERMISSIONS, OWNER_ONLY, operation_action

VERSION = 2
KEY = 'permission_contract'


def has_fine_contract(grant):
    return KEY in grant.capabilities


def contract_for(grant, permissions):
    member = grant.membership
    return {'version': VERSION, 'grant_id': str(grant.pk), 'grant_version': 1,
        'user': member.user_id, 'membership_id': member.pk,
        'exploitation': member.exploitation_id, 'role': member.role,
        'device_id': grant.device_id, 'write_generation': grant.write_generation,
        'rights_version': grant.rights_version, 'permissions': sorted(permissions),
        'iat': int(grant.issued_at.timestamp()), 'exp': int(grant.expires_at.timestamp())}


def fine_contract(grant):
    """Return None only for true V1 absence. Unknown/present malformed fails closed."""
    if not has_fine_contract(grant):
        return None
    value = grant.capabilities[KEY]
    if not isinstance(value, dict):
        raise ValueError('OFFLINE_CONTRACT_INVALID')
    expected = contract_for(grant, [])
    for key, item in expected.items():
        if key == 'permissions':
            continue
        if type(value.get(key)) is not type(item) or value[key] != item:
            raise ValueError('OFFLINE_CONTRACT_INVALID')
    codes = value.get('permissions')
    if (value['role'] != 'OPERATEUR' or not isinstance(codes, list)
            or len(codes) > len(PERMISSIONS)
            or any(not isinstance(code, str) or code not in PERMISSIONS for code in codes)
            or len(set(codes)) != len(codes) or set(codes) & OWNER_ONLY
            or 'finance.read' not in codes or not 0 < value['exp'] - value['iat'] <= 7 * 86400):
        raise ValueError('OFFLINE_CONTRACT_INVALID')
    return frozenset(codes)


def ordinary_business_tolerance(grant):
    return (has_fine_contract(grant) and grant.revoked_at is not None
            and grant.revocation_kind == 'BUSINESS'
            and grant.business_through_version == grant.membership.version)


def permission_failure(grant, declaration, *, action=None):
    """Pure assessment before effect. Known invalid intentions remain durable."""
    try:
        codes = fine_contract(grant)
    except (ValueError, TypeError, KeyError):
        return 'OFFLINE_CONTRACT_INVALID'
    if grant.capabilities.get('can_create_terrain_operation') is not True:
        return 'OFFLINE_PERMISSION_DENIED'
    member = grant.membership
    from .access_policy import effective_permissions
    tolerated = ordinary_business_tolerance(grant)
    if ((grant.revoked_at and not tolerated) or not member.is_active or not member.user.is_active
            or member.user.password_change_required or (member.version != grant.rights_version and not tolerated)
            or member.role != 'OPERATEUR'
            or member.user.exploitation_id != member.exploitation_id
            or member.exploitation.proprietaire_id == member.user_id):
        return 'AUTHOR_RIGHTS_CHANGED'
    def field(name):
        return declaration[name] if isinstance(declaration, dict) else getattr(declaration, name)
    if (field('author_user_id') != member.user_id or field('author_membership_id') != member.pk
            or field('exploitation_id') != member.exploitation_id or field('device_id') != grant.device_id
            or field('device_generation') != grant.write_generation
            or str(field('offline_authorization_id')) != str(grant.pk)):
        return 'OFFLINE_CONTEXT_INVALID'
    recorded = field('local_recorded_at')
    from django.utils import timezone
    from datetime import timedelta
    if recorded > timezone.now() + timedelta(minutes=5):
        return 'OFFLINE_CLOCK_INVALID'
    # Match JWT's half-open window at second precision, including delayed expiry.
    if not int(grant.issued_at.timestamp()) <= recorded.timestamp() < int(grant.expires_at.timestamp()):
        return 'OFFLINE_WINDOW_INVALID'
    resolved = operation_action(field('entity_type'), field('operation_type'))
    if codes is None and resolved is None and action is None:
        return ''  # Unsupported legacy originals remain durable, without business effect.
    if resolved is None or (action is not None and action != resolved):
        return 'OFFLINE_ACTION_UNSUPPORTED'
    # Historical V1 has no signed action snapshot: it never bypasses current rights.
    # V2 additionally requires the original signed permission.
    if (codes is not None and resolved not in codes) or (not tolerated and resolved not in effective_permissions(member)):
        return 'OFFLINE_PERMISSION_DENIED'
    return ''
