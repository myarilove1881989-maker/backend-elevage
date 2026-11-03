from django.db import transaction
from django.db.models import Sum
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .permissions import HasExploitation
from .access_policy import authorize
from .request_context import audited_endpoint
from .models import Espece, Lot, Exploitation
from .supply_models import SupplyProduct, SupplyMovement
from .feed_purchases import correct


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
@transaction.atomic
def catalog(request):
    farm=request.user.exploitation_id
    authorize(request.user,farm,'stocks.read')
    Exploitation.objects.select_for_update().get(pk=farm)
    from .feed_stock import snapshot
    data=snapshot(farm,presentation=True)
    return Response({**data,'species':list(Espece.objects.filter(exploitation_id=farm).values('id','nom')),
        'lots':list(Lot.objects.filter(exploitation_id=farm,date_fin__isnull=True).exclude(statut_production='TERMINE').values('id','nom','espece_id','date_fin','statut_production')),
        'confirmed':True,'stock_contract':1})


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, HasExploitation])
@audited_endpoint
def correction(request,pk):
    return Response(correct(request.user,pk,request.data))
