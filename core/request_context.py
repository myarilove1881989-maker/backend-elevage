"""Explicit online actor context; future sync must validate its author separately."""
from functools import wraps
from django.db import transaction
from rest_framework.exceptions import PermissionDenied
from .audit import ActorContext, audit_scope
from .models import Exploitation, DeviceRegistration
from .permissions import require_member


def online_context(request):
    device = getattr(request, 'verified_device', None)
    return ActorContext(request.user.pk, request.user.exploitation_id,
                        device.pk if device else None)


def lock_write_context(request):
    farm = Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
    if farm.offline_policy_enabled:
        member = require_member(request.user)
        if member.role != 'OPERATEUR':
            raise PermissionDenied('Le propriétaire supervise les opérations terrain.')
        device = getattr(request, 'verified_device', None)
        if not device or not DeviceRegistration.objects.filter(
            pk=device.pk, exploitation=farm, status='ACTIVE', is_primary_writer=True,
            write_generation=farm.write_generation,
        ).exists():
            raise PermissionDenied('Appareil principal actif requis.')


def audited_endpoint(function):
    @wraps(function)
    def wrapped(request, *args, **kwargs):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return function(request, *args, **kwargs)
        with audit_scope(online_context(request)):
            lock_write_context(request)
            response = function(request, *args, **kwargs)
            if response.status_code >= 400:
                transaction.set_rollback(True)
            return response
    return wrapped
