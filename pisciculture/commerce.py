"""Harvest custody journal; invoices and payments use core.Vente/Payment/Lettrage."""
import hashlib,json,uuid
from datetime import timezone as dt_timezone
from decimal import Decimal,ROUND_HALF_UP
from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from rest_framework.exceptions import ValidationError,PermissionDenied
from core.models import Vente,Payment,Lettrage,Client
from core.access_policy import active_membership
from .models import Harvest,HarvestMovement,Cohort,Installation,Position,Event,HealthRecord
from .services import access,get,fail,public,event
from .stock_ledger import replay,pending_reserve,sync
from .health_services import sale_check
from .observations import metadata_at


def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,cls=DjangoJSONEncoder,separators=(',',':')).encode()).hexdigest()

def check(user,site,cohort,at,harvest=None):
    at=at.astimezone(dt_timezone.utc)
    result=sale_check(user,site,cohort,at,origin_ids=[str(cohort.pk)] if harvest else None,population_at=harvest.occurred_at if harvest else None)
    result['matches']=sorted(result['matches'],key=lambda x:(x['root_id'],x['record'],x['scope_match'],x['candidate_cohort'] or ''))
    records={str(x.pk):x for x in HealthRecord.objects.filter(exploitation_id=user.exploitation_id,pk__in=[m['record'] for m in result['matches']])}
    for item in result['matches']:
        row=records[item['record']]
        item['treatment']={'occurred_at':row.occurred_at.isoformat(),'time_precision':row.time_precision,'business_date':row.business_date.isoformat() if row.business_date else None,'product_name':row.product_snapshot.get('name',''),'quantity':str(row.quantity),'unit':row.unit,'reason':row.reason,'note':row.note,'ongoing':row.ongoing}
    result['origin_harvest']=str(harvest.pk) if harvest else None
    result['blocked']=any(m['precaution']['status']=='ACTIVE' or m['ongoing'] for m in result['matches'])
    result['notice']='Vérification humaine requise. Un délai inconnu ou un contexte ambigu demande une vérification documentaire. Aucun résultat ne constitue une autorisation réglementaire.'
    result['health_token']=signing.dumps({'farm':user.exploitation_id,'user':user.pk,'digest':digest(result)},salt='aquatic-commerce-health',compress=True)
    return result

def reviewed(user,site,cohort,at,review,harvest=None):
    actual=check(user,site,cohort,at,harvest);token=actual.pop('health_token')
    try:
        signed=signing.loads(review['health_token'],salt='aquatic-commerce-health',max_age=1800)
        if signed!={'farm':user.exploitation_id,'user':user.pk,'digest':digest(actual)}:raise ValueError()
    except (signing.BadSignature,ValueError):fail('HEALTH_REVIEW_STALE')
    if actual['blocked']:fail('HEALTH_RECORDED_RESTRICTION')
    return {**actual,'human_verified':True,'verification_reason':review['verification_reason'],'verified_by':user.pk,'verified_at':timezone.now().isoformat()}


def balance(harvest,at=None):
    primary=harvest.mass_kg if harvest.stock_unit=='KG' else Decimal(harvest.live_count)
    count=harvest.live_count;mass=harvest.mass_kg if harvest.mass_quality=='MEASURED' else None
    seen={};voided=set();canceled=False
    qs=harvest.movements.all()
    if at:qs=qs.filter(occurred_at__lte=at)
    active=[]
    for row in qs.order_by('occurred_at','recorded_at','pk'):
        if row.occurred_at<harvest.occurred_at:fail('POSTHARVEST_BEFORE_HARVEST')
        if row.kind in ('SALE','LOSS'):active.append(row)
        elif row.kind=='VOID':
            if row.parent_id:
                parent=seen.get(str(row.parent_id))
                if not parent or parent.kind!='LOSS' or str(parent.pk) in voided:fail('POSTHARVEST_VOID_DEPENDENCY')
                active=[m for m in active if m.pk!=parent.pk];voided.add(str(parent.pk))
            else:
                if active:fail('HARVEST_HAS_DEPENDENTS')
                canceled=True
        elif row.kind=='CORRECTION':pass
        if canceled and row.kind in ('SALE','LOSS'):fail('HARVEST_CANCELED')
        left=primary-sum((x.quantity for x in active),Decimal('0'))
        nc=None if any(x.count is None for x in active) else count-sum(x.count for x in active)
        nm=None if mass is None or any(x.mass_kg is None for x in active) else mass-sum((x.mass_kg for x in active),Decimal('0'))
        if left<0 or (nc is not None and nc<0) or (nm is not None and nm<0):fail('POSTHARVEST_CAPACITY_EXCEEDED')
        if not left and ((nc is not None and nc!=0) or (nm is not None and nm!=0)):fail('POSTHARVEST_MEASURES_INCONSISTENT')
        seen[str(row.pk)]=row
    left=primary-sum((x.quantity for x in active),Decimal('0'))
    return {'available_quantity':str(0 if canceled else left),'unit':harvest.stock_unit,'available_count':None if canceled else (None if any(x.count is None for x in active) else count-sum(x.count for x in active)),
        'available_mass_kg':None if canceled or mass is None or any(x.mass_kg is None for x in active) else str(mass-sum((x.mass_kg for x in active),Decimal('0'))),
        'canceled':canceled,'voided':sorted(voided),'status':'CANCELED' if canceled else 'EXHAUSTED' if not left else 'AVAILABLE'}

def invoice(sale):
    h=sale.aquatic_detail.harvest
    return {'id':sale.pk,'invoice_reference':'FAC-'+str(sale.date.year)+'-'+str(sale.pk).zfill(5),'client':sale.client_id,'client_name':sale.client.nom,'client_phone':sale.client.telephone,'date':sale.date.isoformat(),
        'produit_vendu':'POISSONS','lot_nom':'Récolte piscicole','espece':h.species.nom,'quantite':str(sale.priced_quantity),'priced_quantity':str(sale.priced_quantity),'price_unit':sale.price_unit,
        'prix_unitaire':str(sale.prix_unitaire),'montant_total':str(sale.montant_total),'montant_paye':str(sale.montant_paye),'reste':str(sale.reste_a_payer),'statut':sale.statut,
        'exploitation_id':sale.accounting_exploitation_id,'exploitation_nom':sale.accounting_exploitation.nom,
        'payments':[{'id':x.payment_id,'amount':str(x.montant),'date':x.payment.date.isoformat()} for x in sale.lettrages.select_related('payment').order_by('pk')]}

def harvest_public(row):
    return {**public(row),'species_name':row.species.nom,'cohort_name':row.cohort.name,'installation_name':row.installation.name,'actor_name':row.actor.get_full_name() or row.actor.username,'balance':balance(row)}

def movement_public(row):
    return {**public(row),'cohort_name':row.harvest.cohort.name,'installation_name':row.harvest.installation.name,'species_name':row.harvest.species.nom,'harvest_data':{'cohort':str(row.harvest.cohort_id),'installation':str(row.harvest.installation_id),'species':row.harvest.species_id},'unit':row.harvest.stock_unit,'actor_name':row.actor.get_full_name() or row.actor.username,'invoice':invoice(row.sale) if row.sale_id else None,'canceled':str(row.pk) in balance(row.harvest)['voided']}

def new_move(user,farm,h,identifier,kind,at,**data):
    row=HarvestMovement(exploitation=farm,harvest=h,command_uuid=identifier,actor=user,kind=kind,occurred_at=at,**data)
    row.full_clean();row.save();balance(h);return row

def consume(user,farm,h,identifier,at,kind,data):
    if h.destination=='DIRECT' and h.movements.filter(kind='SALE').exists():fail('DIRECT_HARVEST_ALREADY_SOLD')
    state=balance(h,at)
    if state['canceled']:fail('HARVEST_CANCELED')
    q=data.pop('quantity');count=data.pop('count',None);mass=data.pop('mass_kg',None)
    if h.stock_unit=='PIECE':
        if q!=int(q):raise ValidationError('INTEGER_FISH_REQUIRED')
        if count is not None and Decimal(count)!=q:raise ValidationError('POSTHARVEST_MEASURES_INCONSISTENT')
        count=int(q)
    else:
        if mass is not None and mass!=q:raise ValidationError('POSTHARVEST_MEASURES_INCONSISTENT')
        mass=q
    sale=None;review={}
    if kind=='SALE':
        price=data.pop('price');review=reviewed(user,h.installation,h.cohort,at,data.pop('review'),h)
        client=get(Client,farm.pk,price['client'])
        if at.date()<h.occurred_at.date():raise ValidationError('SALE_BEFORE_HARVEST')
        sale=Vente(shared_exploitation=farm,client=client,quantite=count,priced_quantity=q,price_unit=h.stock_unit,prix_unitaire=price['unit_price'],date=at.date())
        total=(q*price['unit_price']).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
        if total>Decimal('9999999999.99'):raise ValidationError('SALE_TOTAL_TOO_LARGE')
        sale.montant_total=total;sale.full_clean();sale.save()
    return new_move(user,farm,h,identifier,kind,at,quantity=q,count=count,mass_kg=mass,sale=sale,health_review=review,**data)

def apply(user,farm,args,identifier,kind):
    d=dict(args);at=d.pop('occurred_at');note=d.pop('note','')
    if kind=='commerce_client':
        from core.serializers import ClientSerializer
        raw=d['customer'];s=ClientSerializer(data=raw);s.is_valid(raise_exception=True)
        row=s.save(exploitation=farm);return {'id':row.pk,'nom':row.nom,'telephone':row.telephone,'pays':row.pays,'ville':row.ville}
    if kind=='harvest':
        cohort=get(Cohort,farm.pk,d.pop('cohort'));site=get(Installation,farm.pk,d.pop('installation'),status='ACTIVE')
        if at<cohort.started_at:raise ValidationError('HARVEST_BEFORE_COHORT')
        n=d.pop('live_count');d.pop('counted');price=d.pop('direct_sale',None)
        review=reviewed(user,site,cohort,at,d.pop('review'))
        state=replay(farm.pk,at);key=(str(cohort.pk),str(site.pk))
        if n>max(0,state['gross'].get(key,0)-pending_reserve(state,key[1],key[0])):fail('HARVEST_LIVE_CAPACITY_EXCEEDED')
        position=Position.objects.filter(cohort=cohort,installation=site).first()
        if position is None:raise ValidationError('COHORT_NOT_IN_INSTALLATION')
        ev=event(user,identifier,cohort,'HARVEST',at,'Récolte confirmée',[(position,-n)]);sync(farm.pk)
        metadata,_=metadata_at(cohort,at)
        row=Harvest(exploitation=farm,cohort=cohort,installation=site,species_id=metadata['species_id'],stock_event=ev,command_uuid=identifier,actor=user,occurred_at=at,live_count=n,note=note,health_review=review,**d)
        row.full_clean();row.save()
        if price:
            # Same review was checked before the debit; no second token is issued or trusted.
            q=row.mass_kg if row.stock_unit=='KG' else Decimal(n)
            client=get(Client,farm.pk,price['client']);sale=Vente(shared_exploitation=farm,client=client,quantite=n,priced_quantity=q,price_unit=row.stock_unit,prix_unitaire=price['unit_price'],date=at.date())
            sale.montant_total=(q*price['unit_price']).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP);sale.full_clean();sale.save()
            new_move(user,farm,row,uuid.uuid5(identifier,'direct-sale'),'SALE',at,quantity=q,count=n,mass_kg=row.mass_kg if row.mass_quality=='MEASURED' else None,sale=sale,health_review=review,note=note)
        return harvest_public(row)
    if kind=='commerce_payment':
        sale=Vente.objects.filter(pk=d['sale'],shared_exploitation=farm).first()
        if not sale:from rest_framework.exceptions import NotFound;raise NotFound('NOT_FOUND')
        Client.objects.select_for_update().get(pk=sale.client_id,exploitation=farm)
        sale=Vente.objects.select_for_update().get(pk=sale.pk,shared_exploitation=farm)
        if at.date()<sale.date:raise ValidationError('PAYMENT_BEFORE_SALE')
        if d['amount']>sale.reste_a_payer:fail('PAYMENT_EXCEEDS_BALANCE')
        payment=Payment.objects.create(client=sale.client,exploitation=farm,montant=d['amount'],date=at.date(),note=note)
        Lettrage.objects.create(vente=sale,payment=payment,montant=d['amount'])
        return {'id':payment.pk,'invoice':invoice(sale)}
    h=get(Harvest,farm.pk,d.pop('harvest'))
    if at<h.occurred_at:raise ValidationError('POSTHARVEST_BEFORE_HARVEST')
    if kind in ('harvest_sale','harvest_loss'):
        row=consume(user,farm,h,identifier,at,'SALE' if kind=='harvest_sale' else 'LOSS',{**d,'note':note});return movement_public(row)
    if active_membership(user).role!='OWNER':raise PermissionDenied('OWNER_REQUIRED')
    target=get(HarvestMovement,farm.pk,d['target']) if d.get('target') else None
    if target and target.harvest_id!=h.pk:raise ValidationError('HARVEST_PARENT_MISMATCH')
    if kind=='commerce_correction':
        row=new_move(user,farm,h,identifier,'CORRECTION',at,parent=target,reason=d['reason'],note=note)
        return {**movement_public(row),'decision':'REVIEW_REQUIRED','financial_effect':False,'notice':'Demande conservée. Aucun avoir, remboursement ou annulation comptable automatique.'}
    if target and target.kind!='LOSS':fail('FINANCIAL_CORRECTION_REQUIRED' if target.sale_id else 'UNSUPPORTED_CORRECTION')
    if target and at<target.occurred_at:raise ValidationError('VOID_BEFORE_ORIGINAL')
    ev=None
    if not target:
        if h.movements.filter(kind__in=['SALE','LOSS']).exists():fail('HARVEST_HAS_DEPENDENTS')
        if balance(h)['canceled']:fail('HARVEST_CANCELED')
        position=Position.objects.get(cohort=h.cohort,installation=h.installation)
        ev=event(user,identifier,h.cohort,'HARVEST_REVERSAL',at,d['reason'][:500],[(position,h.live_count)]);sync(farm.pk)
    row=new_move(user,farm,h,identifier,'VOID',at,parent=target,stock_event=ev,reason=d['reason'],note=note)
    return movement_public(row)
