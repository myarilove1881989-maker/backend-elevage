"""Explicit, audited owner corrections of classic online records, no offline rewrite."""
from .supply_models import SupplyReceipt
from decimal import Decimal
from dataclasses import replace
from .lot_lifecycle import closed
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from rest_framework.response import Response
from .models import (Lot, Vente, VenteOeufs, Mouvement, MouvementOeufs, PeseeProduction,
    Depense, CollecteOeufs, ConsommationAliment, Exploitation, AuditEvent, Lettrage)
from .audit import audit_scope
from .request_context import online_context
from .permissions import require_owner
from .owner_http import owner_http_request
from .access_policy import authorize
from .serializers import LotSerializer, PeseeProductionSerializer, CollecteOeufsSerializer

MODELS = {'lots': Lot, 'weights': PeseeProduction, 'collections': CollecteOeufs,
          'egg_sales': VenteOeufs, 'expenses': Depense, 'sales': Vente}

def classic(obj):
    if getattr(obj, 'voided_at', None) is not None or AuditEvent.objects.filter(
        entity_type=obj._meta.label_lower, entity_id=str(obj.pk), operation_id__isnull=False).exists():
        raise ValidationError('Écriture hors ligne ou annulée : utilisez son parcours de rapprochement, sans modifier les données originales.')

def field(name, label, kind='text', value='', choices=None):
    result={'name':name,'label':label,'type':kind,'value':str(value) if value is not None else ''}
    if choices is not None: result['choices']=choices
    return result

def fields(kind, obj, farm):
    if kind == 'expenses' and obj is not None and obj.pk:
        receipt=SupplyReceipt.objects.filter(expense_id=obj.pk,operation__kind='FEED_PURCHASE').first()
        if receipt:
            from .supply_models import SupplyMovement
            from django.db.models import Sum
            delta=SupplyMovement.objects.filter(operation__exploitation_id=farm.pk,operation__kind='FEED_CORRECT',operation__payload__expense=obj.pk).aggregate(n=Sum('quantity'))['n'] or 0
            return [field('quantity_kg','Quantité totale approvisionnée (kg)','decimal',receipt.quantity+delta)]
    if kind == 'lots':
        return [field('nom','Nom du lot',value=obj.nom),
            field('espece','Espèce','choice',obj.espece_id,[{'value':str(x.pk),'label':x.nom} for x in farm.especes.all()]),
            field('type_production','Production','choice',obj.type_production,[{'value':v,'label':l} for v,l in Lot.TYPE_PRODUCTION_CHOICES]),
            field('statut_production','Statut','choice',obj.statut_production,[{'value':v,'label':l} for v,l in Lot.STATUT_PRODUCTION_CHOICES]),
            field('date_debut','Date de début (AAAA-MM-JJ)','date',obj.date_debut)]
    if kind == 'weights':
        return [field('nombre_animaux_peses','Animaux pesés','integer',obj.nombre_animaux_peses),
            field('poids_total_kg','Poids total (kg)','decimal',obj.poids_total_kg),field('note','Note',value=obj.note)]
    if kind == 'collections':
        return [field(k,label,'integer',getattr(obj,k)) for k,label in (
            ('nombre_collecte','Œufs collectés'),('nombre_casses','Œufs cassés'),
            ('nombre_declasses','Œufs déclassés'),('nombre_consommes_donnes','Œufs consommés / donnés'))]
    if kind == 'egg_sales':
        return [field('nombre_oeufs','Quantité totale d’œufs','integer',obj.nombre_oeufs),
            field('montant_total','Montant total','decimal',obj.montant_total)]
    if kind == 'sales':
        return [field('quantite','Quantité vendue','integer',obj.quantite),field('prix_unitaire','Prix unitaire','decimal',obj.prix_unitaire)]
    return [field('montant','Montant','decimal',obj.montant),field('date','Date (AAAA-MM-JJ)','date',obj.date),field('note','Note',value=obj.note)]

def domain(kind):
    return {'collections':'egg_collections','sales':'animal_sales'}.get(kind,kind)

def positive(value, integer=False):
    if integer:return serializers.IntegerField(min_value=1,max_value=2147483647).run_validation(value)
    return serializers.DecimalField(max_digits=10,decimal_places=2,min_value=Decimal('0.01')).run_validation(value)

def unpaid(sale):
    if sale.client_id:
        from .models import Client
        Client.objects.select_for_update().get(pk=sale.client_id,exploitation_id=sale.lot.exploitation_id)
    if sale.lettrages.exists():
        raise ValidationError('Cette vente a reçu un paiement. Sa correction exige un rapprochement ; ses paiements restent conservés.')
    classic(sale)

def update_sale(kind, obj, values, user):
    from .egg_services import (get_egg_stock,get_dated_egg_stock,plan_fifo_egg_sale,affect_egg_exit)
    sale=obj.vente if kind=='egg_sales' else obj
    unpaid(sale)
    if kind=='egg_sales':
        movement=MouvementOeufs.objects.select_for_update().get(vente_oeufs=obj)
        classic(movement)
        count=1
        price=positive(values.get('montant_total',obj.montant_total))
        quantity=positive(values.get('nombre_oeufs',obj.nombre_oeufs),True)
        serializers.DecimalField(max_digits=12,decimal_places=2).run_validation(count*price)
        if get_egg_stock(obj.exploitation,obj.lot)+obj.nombre_oeufs < quantity:
            raise ValidationError('Stock d’œufs insuffisant pour cette correction.')
        # A classic outgoing ledger line is replaced transactionally; the audit
        # retains every old allocation. Signed/offline lines are refused above.
        when=movement.date
        movement.delete()
        state=get_dated_egg_stock(obj.exploitation,obj.lot)
        try: allocations=plan_fifo_egg_sale(stock_state=state,quantity=quantity,sale_date=sale.date,movement_at=when)
        except ValueError as exc: raise ValidationError(str(exc))
        sale.quantite=count;sale.prix_unitaire=price;sale.save()
        obj.conditionnement='COMPOSE';obj.oeufs_par_conditionnement=quantity
        obj.nombre_conditionnements=count;obj.prix_unitaire_conditionnement=price
        obj.nombre_oeufs=quantity;obj.montant_total=count*price;obj.save()
        replacement=MouvementOeufs.objects.create(exploitation=obj.exploitation,lot=obj.lot,
            type_mouvement='VENTE',quantite=quantity,date=when,vente_oeufs=obj,
            created_by=user,note=f'Correction de vente #{sale.pk}')
        affect_egg_exit(replacement,allocations)
    else:
        movement=Mouvement.objects.select_for_update().filter(vente=sale,type_mouvement='VENTE').first()
        if movement is None or movement.quantite != sale.quantite:
            raise ValidationError('Ancienne vente sans lien de stock certain : rapprochement requis.')
        classic(movement)
        count=positive(values.get('quantite',sale.quantite),True)
        price=positive(values.get('prix_unitaire',sale.prix_unitaire))
        from .animal_stock import current_animal_stock
        if current_animal_stock(sale.lot)+movement.quantite < count:
            raise ValidationError('Stock animal insuffisant pour cette correction.')
        serializers.DecimalField(max_digits=12,decimal_places=2).run_validation(count*price)
        sale.quantite=count;sale.prix_unitaire=price;sale.save()
        movement.quantite=count;movement.prix_unitaire=price;movement.save()

@api_view(['GET','POST','PATCH','DELETE'])
@permission_classes([IsAuthenticated])
def owner_maintenance(request,kind,pk=None):
    member=require_owner(request.user)
    if not owner_http_request(request) or kind not in MODELS:
        raise PermissionDenied('Accès propriétaire HTTP vérifié requis.')
    with audit_scope(replace(online_context(request),closed_lot_correction=request.data.get('allow_closed') is True),reason_code='OWNER_MAINTENANCE',
                     reason_text=str(request.data.get('reason',''))[:1000]):
        farm=Exploitation.objects.select_for_update().get(pk=member.exploitation_id)
        obj=None
        if pk is not None:
            model=MODELS[kind]
            scope={'exploitation_id':farm.pk} if kind not in {'expenses','sales'} else {'lot__exploitation_id':farm.pk}
            obj=model.objects.select_for_update().filter(pk=pk,**scope).first()
            if obj is None:raise NotFound()
            if kind=='sales':
                eggs=VenteOeufs.objects.select_for_update().filter(vente_id=obj.pk,exploitation_id=farm.pk).first()
                if eggs is not None:kind='egg_sales';obj=eggs
            if kind!='lots':Lot.objects.select_for_update().get(pk=obj.lot_id,exploitation_id=farm.pk)
        if request.method=='GET':
            if pk is None and request.query_params.get('list') == '1':
                model=MODELS[kind]
                scope={'exploitation_id':farm.pk} if kind not in {'expenses','sales'} else {'lot__exploitation_id':farm.pk}
                query=model.objects.filter(**scope).order_by('-pk')
                if request.query_params.get('lot'):query=query.filter(lot_id=request.query_params['lot'])
                try: offset=max(0,int(request.query_params.get('offset',0)))
                except ValueError:raise ValidationError('Page invalide.')
                rows=list(query[offset:offset+31])
                return Response({'results':[{'id':x.pk,'label':str(x),
                    'date':str(getattr(x,'date',getattr(x,'pesee_at',getattr(x,'collecte_at',''))))} for x in rows[:30]],
                    'next_offset':offset+30 if len(rows)>30 else None})
            if obj is None:
                if kind!='lots':raise NotFound()
                obj=Lot(nom='',date_debut=timezone.localdate(),exploitation=farm)
            return Response({'id':pk,'farm':farm.pk,'revision':farm.business_revision,'closed':closed(obj if kind=='lots' else obj.lot),'fields':fields(kind,obj,farm),
                'consequences':('Le lot et ses achats, mouvements, dépenses et suivis associés seront supprimés. '
                    'Les ventes non payées seront retirées et les soldes clients recalculés. Les ventes payées, dépendances protégées et écritures hors ligne empêchent la suppression. '
                    'Le journal d’audit sera conservé.' if kind=='lots' else
                    'Les stocks, calculs et soldes liés seront recalculés. Les paiements et écritures hors ligne sont protégés. Le journal d’audit sera conservé.')})
        action={'POST':'create','PATCH':'update','DELETE':'delete'}[request.method]
        authorize(request.user,farm,domain(kind)+'.'+action,obj,owner_http=True)
        if request.data.get('farm') != farm.pk:raise ValidationError('L’exploitation a changé. Rouvrez le formulaire.')
        reason=request.data.get('reason')
        if request.data.get('confirm') is not True or not isinstance(reason,str) or not 3<=len(reason.strip())<=1000:
            raise ValidationError('Confirmation explicite et motif requis.')
        if str(request.data.get('expected_revision')) != str(farm.business_revision):
            raise ValidationError('Les données ont changé. Rechargez avant de confirmer cette correction.')
        values=request.data.get('values',{})
        if not isinstance(values,dict):raise ValidationError('Champs invalides.')
        if obj is not None:
            target=obj if kind=='lots' else obj.lot
            if closed(target) and request.data.get('allow_closed') is not True:raise ValidationError('Confirmez explicitement la correction exceptionnelle de ce lot clôturé.')
            classic(obj)
        allowed={f['name'] for f in fields(kind,obj or Lot(nom='',date_debut=timezone.localdate(),exploitation=farm),farm)}
        if kind=='lots' and 'statut_production' in values and (obj is None or values['statut_production']!=obj.statut_production) and (values['statut_production']=='TERMINE' or (obj and obj.statut_production=='TERMINE')):
            raise ValidationError('Utilisez les actions Clôturer ou Réouvrir le lot.')
        if set(values)-allowed:raise ValidationError('Champs non autorisés.')
        if kind=='expenses' and obj is not None and SupplyReceipt.objects.filter(expense_id=obj.pk,operation__kind='FEED_PURCHASE').exists():
            from uuid import uuid5, NAMESPACE_URL
            from .feed_purchases import correct, cancel
            identifier=uuid5(NAMESPACE_URL, f'feed-maintenance:{farm.pk}:{obj.pk}:{farm.business_revision}:{request.method}:{reason}:{sorted(values.items())}')
            if request.method=='DELETE':
                cancel(request.user,obj,identifier,reason)
            else:
                if set(values)!={'quantity_kg'}:raise ValidationError('Quantité physique requise ; les montants restent inchangés.')
                correct(request.user,obj.pk,{'operation_uuid':str(identifier),'quantity_kg':values['quantity_kg'],'reason':reason})
            farm.business_revision+=1;farm.save(update_fields=['business_revision'])
            return Response({'status':'deleted' if request.method=='DELETE' else 'saved','id':obj.pk,'revision':farm.business_revision})
        if request.method=='POST':
            if kind!='lots'  or pk is not None:raise ValidationError('Création non prise en charge.')
            serializer=LotSerializer(data=values,context={'request':request})
            serializer.is_valid(raise_exception=True);obj=serializer.save(exploitation=farm,created_by=request.user)
        elif request.method=='DELETE':
            if kind=='lots':
                if Lettrage.objects.filter(vente__lot_id=obj.pk).exists():raise ValidationError('Ce lot possède des ventes payées : suppression interdite.')
                try: obj.delete()
                except ProtectedError: raise ValidationError('Ce lot est lié à des données protégées, notamment des naissances ou annulations. Suppression refusée.')
            elif kind=='collections':
                from .egg_services import delete_collection
                delete_collection(obj,actor=request.user)
            elif kind=='egg_sales':
                from .egg_services import delete_egg_sale
                try:delete_egg_sale(obj,actor=request.user)
                except ValueError as exc:raise ValidationError(str(exc))
            elif kind=='sales':
                unpaid(obj)
                movement=Mouvement.objects.filter(vente=obj,type_mouvement='VENTE').first()
                if movement is None:raise ValidationError('Lien de stock manquant : rapprochement requis.')
                classic(movement);movement.delete();obj.delete()
            elif kind=='expenses':
                if (ConsommationAliment.history.filter(depense_id=obj.pk).exists() or SupplyReceipt.objects.filter(expense_id=obj.pk).exists()):raise ValidationError('Dépense liée à une alimentation : correction coordonnée requise.')
                obj.delete()
            else:obj.delete()
        elif kind in {'egg_sales','sales'}:update_sale(kind,obj,values,request.user)
        elif kind=='weights':
            from .animal_stock import current_animal_stock
            serializer=PeseeProductionSerializer(obj,data=values,partial=True,
                context={'request':request,'stock_actuel':max(current_animal_stock(obj.lot),0)})
            serializer.is_valid(raise_exception=True);serializer.save()
        elif kind=='collections':
            from .egg_services import update_collection
            serializer=CollecteOeufsSerializer(obj,data=values,partial=True,context={'request':request})
            serializer.is_valid(raise_exception=True);update_collection(serializer=serializer)
        elif kind=='expenses':
            if (ConsommationAliment.history.filter(depense_id=obj.pk).exists() or SupplyReceipt.objects.filter(expense_id=obj.pk).exists()):raise ValidationError('Dépense liée à une alimentation : correction coordonnée requise.')
            class ExpenseInput(serializers.Serializer):
                montant=serializers.DecimalField(max_digits=12,decimal_places=2,min_value=Decimal('0.01'),required=False)
                date=serializers.DateField(required=False)
                note=serializers.CharField(max_length=255,allow_blank=True,required=False)
            serializer=ExpenseInput(data=values);serializer.is_valid(raise_exception=True)
            for name,value in serializer.validated_data.items():setattr(obj,name,value)
            obj.save()
        else:
            serializer=LotSerializer(obj,data=values,partial=True,context={'request':request})
            serializer.is_valid(raise_exception=True);serializer.save()
        farm.business_revision+=1;farm.save(update_fields=['business_revision'])
        return Response({'status':'deleted' if request.method=='DELETE' else 'saved','id':getattr(obj,'pk',None),'revision':farm.business_revision},status=201 if request.method=='POST' else 200)
