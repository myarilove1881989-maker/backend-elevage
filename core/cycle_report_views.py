from django.db import transaction
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from .access_policy import authorize
from .models import Exploitation, Lot
from .permissions import HasExploitation
from .cycle_report import report, compare


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
@transaction.atomic
def cycle_report(request, pk):
    # Serialize with ordinary writes for a consistent farm revision; never mutate.
    farm = Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
    authorize(request.user, farm, 'finance.read')
    lot = Lot.objects.select_related('exploitation').filter(pk=pk, exploitation=farm).first()
    if lot is None:
        raise NotFound()
    result = report(lot)
    candidates = Lot.objects.filter(exploitation=farm, espece_id=lot.espece_id,
                                    type_production=lot.type_production).exclude(pk=lot.pk).order_by('-date_debut', '-pk')
    result['candidates'] = list(candidates.values('id', 'nom', 'date_debut', 'date_fin'))
    other_id = request.query_params.get('compare')
    if other_id is not None:
        if not other_id.isascii() or not other_id.isdigit() or len(other_id) > 18:
            raise ValidationError('Cycle de comparaison invalide.')
        other = Lot.objects.select_related('exploitation').filter(pk=int(other_id), exploitation=farm).first()
        if other is None:
            raise NotFound()
        if other.pk == lot.pk or other.espece_id != lot.espece_id or other.type_production != lot.type_production:
            raise ValidationError('Choisissez deux lots distincts de même espèce et de même production.')
        result['comparison'] = compare(result, report(other))
    response = Response(result)
    response['Cache-Control'] = 'private, no-store'
    return response
