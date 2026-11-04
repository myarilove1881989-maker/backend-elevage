"""Physical stock presentation, reusing immutable movements and product settings."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .permissions import HasExploitation, require_owner
from .access_policy import authorize
from .models import Exploitation
from .supply_models import SupplyProduct, SupplyMovement
from .supply_services import configure_product

class ThresholdInput(serializers.Serializer):
    alert_threshold=serializers.DecimalField(max_digits=14,decimal_places=3,min_value=0)
    expected_threshold=serializers.DecimalField(max_digits=14,decimal_places=3,min_value=0)

@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def threshold(request,pk):
    require_owner(request.user)
    Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
    row=get_object_or_404(SupplyProduct,pk=pk,exploitation_id=request.user.exploitation_id)
    fields=ThresholdInput(data=request.data);fields.is_valid(raise_exception=True)
    values=fields.validated_data
    if row.alert_threshold not in {values['expected_threshold'],values['alert_threshold']}:
        raise serializers.ValidationError({'alert_threshold':'THRESHOLD_VERSION_CONFLICT'})
    if row.kind=='VACCINE' and values['alert_threshold']%1:
        raise serializers.ValidationError({'alert_threshold':'INTEGER_DOSES_REQUIRED'})
    if row.alert_threshold!=values['alert_threshold']:
        row=configure_product(request.user,row.exploitation_id,row.pk,name=row.name,
            alert_threshold=values['alert_threshold'],archived=row.archived)
    return Response({'id':str(row.pk),'alert_threshold':str(row.alert_threshold)})

class HistoryInput(serializers.Serializer):
    before=serializers.IntegerField(min_value=1,required=False)
    limit=serializers.IntegerField(min_value=1,max_value=100,default=50)

@api_view(['GET'])
@permission_classes([IsAuthenticated,HasExploitation])
def history(request,pk):
    authorize(request.user,request.user.exploitation_id,'stocks.read')
    product=get_object_or_404(SupplyProduct,pk=pk,exploitation_id=request.user.exploitation_id)
    inputs=HistoryInput(data=request.query_params);inputs.is_valid(raise_exception=True)
    limit=inputs.validated_data['limit']
    rows=SupplyMovement.objects.filter(product=product,operation__exploitation_id=request.user.exploitation_id).select_related('operation')
    if 'before' in inputs.validated_data:rows=rows.filter(pk__lt=inputs.validated_data['before'])
    rows=list(rows.order_by('-pk')[:limit+1]);more=len(rows)>limit;rows=rows[:limit]
    return Response({'product':str(product.pk),'unit':'kg' if product.kind=='FEED' else 'dose',
        'results':[{'id':r.pk,'kind':r.kind,'quantity':str(r.quantity),'species':r.species_id,'lot':r.lot_id,
            'destination_lot':r.destination_lot_id,'vial':str(r.vial_id) if r.vial_id else None,
            'at':r.operation.occurred_at.isoformat(),'operator':r.operation.author_id,'reason':r.operation.reason,
            'operation_uuid':str(r.operation.operation_uuid)} for r in rows],
        'next_before':rows[-1].pk if more else None})
