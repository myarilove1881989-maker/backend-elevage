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
    revision=Exploitation.objects.select_for_update().get(pk=farm).business_revision
    authorize(request.user,farm,'stocks.read')
    rows=[]
    for product in SupplyProduct.objects.filter(exploitation_id=farm,kind='FEED',archived=False).order_by('name','id'):
        positions=list(SupplyMovement.objects.filter(product=product).values('species_id','lot_id').annotate(kg=Sum('quantity')))
        for position in positions:position['kg']=str(position['kg'])
        rows.append({'id':str(product.pk),'name':product.name,'positions':positions})
    return Response({'products':rows,'species':list(Espece.objects.filter(exploitation_id=farm).values('id','nom')),
        'lots':list(Lot.objects.filter(exploitation_id=farm,date_fin__isnull=True).exclude(statut_production='TERMINE').values('id','nom','espece_id')),
        'confirmed':True,'confirmed_business_revision':revision,'stock_contract':1})


@api_view(['PATCH'])
@permission_classes([IsAuthenticated, HasExploitation])
@audited_endpoint
def correction(request,pk):
    return Response(correct(request.user,pk,request.data))
