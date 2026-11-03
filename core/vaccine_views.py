from datetime import datetime, time, timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .models import Exploitation, Espece, Lot
from .permissions import HasExploitation
from .access_policy import authorize
from .request_context import audited_endpoint
from .supply_models import SupplyProduct, SupplyVial, SupplyVialOpening
from .vaccine_services import administer, serialize
from .vaccination_models import Vaccination


def snapshot(farm_id):
    from .feed_stock import operation_coverage
    # Reuse coverage of the existing outbox; no second acknowledgement protocol.
    coverage=operation_coverage(farm_id)
    farm=Exploitation.objects.get(pk=farm_id);now=timezone.now();vials=[]
    products=list(SupplyProduct.objects.filter(exploitation=farm,kind='VACCINE').order_by('id').values('id','name','archived','alert_threshold'))
    if len(products)>1000:raise ValidationError('VACCINE_CATALOG_TOO_LARGE')
    for product in products:
        product['id']=str(product['id']);product['alert_threshold']=str(product['alert_threshold'])
    from django.db.models import Sum
    from .supply_models import SupplyMovement
    entries={row.vial_id:row for row in SupplyMovement.objects.filter(operation__exploitation=farm,product__kind='VACCINE',kind='ENTRY')}
    balances={row['vial_id']:row['total'] for row in SupplyMovement.objects.filter(operation__exploitation=farm,product__kind='VACCINE').values('vial_id').annotate(total=Sum('quantity'))}
    for vial in SupplyVial.objects.filter(receipt__operation__exploitation=farm).select_related('receipt__operation','receipt__product','opening').order_by('id')[:10001]:
        entry=entries.get(vial.pk)
        if entry is None:raise ValidationError('VACCINE_ENTRY_MISSING')
        opening=getattr(vial,'opening',None)
        until=timezone.make_aware(datetime.combine(vial.receipt.expires_on+timedelta(days=1),time.min))
        if opening and opening.usable_seconds is not None:until=min(until,opening.opened_at+timedelta(seconds=opening.usable_seconds))
        physical=int(balances.get(vial.pk,0))
        vials.append({'id':str(vial.pk),'product':str(vial.receipt.product_id),'label':f'{vial.receipt.manufacturer_batch} / {vial.ordinal} / {str(vial.pk)[:8]}',
            'species':entry.species_id,'lot':entry.lot_id,'physical':physical,'usable':physical if now<until and (opening is None or now>=opening.opened_at) else 0,
            'expires_on':str(vial.receipt.expires_on),'usable_until':until.isoformat(),
            'opened_at':opening.opened_at.isoformat() if opening else None,'usable_seconds':opening.usable_seconds if opening else None,
            'manufacturer_batch':vial.receipt.manufacturer_batch,'doses_per_vial':vial.receipt.doses_per_vial})
    if len(vials)>10000:raise ValidationError('VACCINE_VIAL_LIMIT_REQUIRES_PAGINATION')
    return {'id':1,'schema':1,'exploitation':farm_id,'vaccine_contract':1,'confirmed':True,'as_of':now.isoformat(),
        'confirmed_business_revision':farm.business_revision,'coverage':coverage,'products':products,'vials':vials,
        'species':list(Espece.objects.filter(exploitation=farm).values('id','nom')),
        'lots':list(Lot.objects.filter(exploitation=farm,date_fin__isnull=True).exclude(statut_production='TERMINE').values('id','nom','espece_id'))}


@api_view(['GET'])
@permission_classes([IsAuthenticated,HasExploitation])
@transaction.atomic
def catalog(request):
    authorize(request.user,request.user.exploitation_id,'stocks.read')
    Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
    return Response(snapshot(request.user.exploitation_id))


@api_view(['GET','POST'])
@permission_classes([IsAuthenticated,HasExploitation])
@audited_endpoint
def vaccinations(request):
    if request.method=='POST':return Response(serialize(administer(request.user,dict(request.data))),status=201)
    authorize(request.user,request.user.exploitation_id,'vaccinations.read')
    rows=Vaccination.objects.filter(exploitation_id=request.user.exploitation_id).select_related('lot','product','current','created_by').order_by('-vaccinated_at','-pk')
    if request.query_params.get('lot'):rows=rows.filter(lot_id=request.query_params['lot'])
    # History contains cancelled projections explicitly, never deletes them.
    return Response([serialize(item) for item in rows[:1000]])


@api_view(['GET','PATCH','DELETE'])
@permission_classes([IsAuthenticated,HasExploitation])
@audited_endpoint
def detail(request,pk):
    if request.method=='GET':
        from .supply_models import SupplyOperation
        from rest_framework.exceptions import NotFound
        authorize(request.user,request.user.exploitation_id,'vaccinations.read')
        item=Vaccination.objects.filter(pk=pk,exploitation_id=request.user.exploitation_id).first()
        if item is None:raise NotFound()
        from django.db.models import Q
        operations=SupplyOperation.objects.filter(exploitation_id=request.user.exploitation_id).filter(
            Q(pk=item.original_id)|Q(kind__in=['VAC_EDIT','VAC_CANCEL'],payload__vaccination=pk)).order_by('pk')
        return Response({**serialize(item),'history':[{'version':f'vac:{op.pk}','kind':op.kind,'author':op.author_id,
            'occurred_at':op.occurred_at.isoformat(),'reason':op.reason,'animals':op.payload.get('animals'),
            'allocations':op.payload.get('allocations',[])} for op in operations]})
    body=dict(request.data);body.pop('confirm',None)
    return Response(serialize(administer(request.user,body,pk,cancel=request.method=='DELETE')))


def terrain_purchase(row,user):
    from .terrain_operations import Reference,resolve,mark_lots
    from .vaccine_services import purchase
    from .models import Depense
    p=dict(row.payload)
    if set(p)-{'lot_ref','categorie_id','montant','note','vaccine_supply','vaccine_version'} or p.get('vaccine_version')!=1:raise ValidationError('VACCINE_TERRAIN_FIELDS_INVALID')
    lot=resolve(row,Reference().run_validation(p.pop('lot_ref',None)))
    supply=dict(p['vaccine_supply'])
    item=purchase(user,{'operation_uuid':str(row.client_operation_id),'lot':lot.pk,'categorie':p['categorie_id'],
        'montant':p['montant'],'date':str(timezone.localdate(row.business_occurred_at)),'note':p.get('note',''),'vaccine_supply':supply})
    return mark_lots(Depense.objects.get(pk=item['id']),lot)


def terrain_vaccination(row,user):
    from .terrain_operations import Reference,resolve,mark_lots
    p=dict(row.payload)
    if p.pop('vaccine_version',None)!=1:raise ValidationError('VACCINE_CONTRACT_REQUIRED')
    if row.operation_type=='CREATE':
        lot=resolve(row,Reference().run_validation(p.pop('lot_ref',None)))
        item=administer(user,{**p,'operation_uuid':str(row.client_operation_id),'lot':lot.pk,'vaccinated_at':row.business_occurred_at.isoformat()})
    elif row.operation_type in {'UPDATE','REVERSE'}:
        ref=Reference().run_validation(p.pop('vaccination_ref',None))
        original=resolve(row,ref,'VACCINATION',Vaccination)
        item=administer(user,{**p,'operation_uuid':str(row.client_operation_id),'expected_version':row.expected_server_version},original.pk,cancel=row.operation_type=='REVERSE')
    else:raise ValidationError('VACCINE_OPERATION_INVALID')
    return mark_lots(item,item.lot)
