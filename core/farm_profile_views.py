"""Shared farm identity. Only an active owner may rename the current farm."""
from django.db import transaction
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import Exploitation
from .audit import audit_scope, record
from .request_context import online_context
from .permissions import require_member, require_owner


class FarmNameInput(serializers.Serializer):
    nom = serializers.CharField(max_length=100, allow_blank=False)


@api_view(['GET', 'PATCH'])
@transaction.atomic
def farm_profile(request):
    member = require_member(request.user)
    if request.method == 'PATCH':
        require_owner(request.user)
        data = FarmNameInput(data=request.data)
        data.is_valid(raise_exception=True)
        farm = Exploitation.objects.select_for_update().get(pk=member.exploitation_id)
        require_owner(request.user)
        before = {'nom': farm.nom}
        with audit_scope(online_context(request)):
            farm.nom = data.validated_data['nom']
            farm.save(update_fields=['nom'])
            record(farm, 'UPDATE', before, {'nom': farm.nom})
    else:
        farm = member.exploitation
    return Response({'id': farm.pk, 'nom': farm.nom})
