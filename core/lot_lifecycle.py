"""Lot lifecycle using existing dates; no stock write and no offline contract change."""
from datetime import datetime
from django.db.models import Q,Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

def closed(lot):
    return lot.date_fin is not None or lot.statut_production=='TERMINE'

def closed_filter():
    return Q(date_fin__isnull=False)|Q(statut_production='TERMINE')

def correction_context():
    from .audit import current_actor_context
    from .owner_http import owner_context
    from .models import User
    context=current_actor_context()
    if not context:return False
    if context.source=='RECONCILE':return True  # existing reasoned owner decision
    actor=User.objects.filter(pk=context.author_id).first()
    return bool(actor and context.closed_lot_correction and owner_context(actor,context.exploitation_id))

def ensure_open(lot, *, correction=False):
    if closed(lot) and not (correction and correction_context()):
        raise ValidationError({'lot':'LOT_CLOSED : ce lot est clôturé. Réouvrez-le pour une nouvelle opération ; les règlements de créances restent possibles.'})

def resource_guard(resource,action):
    if resource is None or action=='cash.create':return
    from .models import Lot
    lot=resource if isinstance(resource,Lot) else getattr(resource,'lot',None)
    if lot is not None:ensure_open(lot,correction=not action.endswith('.create'))

def guard_request(request,action):
    # Called under the existing farm lock, before any classic REST mutation.
    from .models import Lot,CollecteOeufs,VenteOeufs,ConsommationAliment,Mouvement,Depense,PeseeProduction
    if action=='cash.create' or request.path.rstrip('/').startswith('/api/depenses/cancel/'):return
    pk=request.parser_context.get('kwargs',{}).get('pk')
    models={'egg_collections':CollecteOeufs,'egg_sales':VenteOeufs,'feed':ConsommationAliment,
            'weights':PeseeProduction,'expenses':Depense}
    model=Mouvement if '/mouvements/delete/' in request.path else models.get(action.split('.')[0])
    lot_ids=[]
    if pk and model:
        tenant='lot__exploitation_id'
        lot_ids+=list(model.objects.filter(pk=pk,**{tenant:request.user.exploitation_id}).values_list('lot_id',flat=True))
        if model is Mouvement:lot_ids+=list(model.objects.filter(pk=pk,lot__exploitation_id=request.user.exploitation_id,lot_origine__isnull=False).values_list('lot_origine_id',flat=True))
    value=request.data.get('lot')
    if isinstance(value,int) or (isinstance(value,str) and value.isascii() and value.isdigit() and len(value)<19):
        lot_ids.append(int(value))
    for lot in Lot.objects.select_for_update().filter(pk__in=lot_ids,exploitation_id=request.user.exploitation_id):ensure_open(lot)

def latest_activity(lot):
    dates=[lot.date_debut]
    for relation,field in [('achats','date'),('ventes','date'),('depenses','date'),('mouvements','date'),
        ('naissances_issues','date'),('collectes_oeufs','collecte_at'),('consommations_aliment','date'),('pesees_production','pesee_at')]:
        manager=getattr(lot,relation)
        # Include voided history too: closing never re-dates recorded facts.
        value=manager.model._base_manager.filter(**{manager.field.name:lot.pk}).order_by('-'+field).values_list(field,flat=True).first()
        if isinstance(value,datetime):value=timezone.localtime(value).date()
        if value:dates.append(value)
    from .supply_models import SupplyMovement, SupplyOperation
    supply_dates = list(SupplyMovement.objects.filter(lot=lot).values_list('operation__occurred_at', flat=True))
    supply_dates += list(SupplyOperation.objects.filter(exploitation_id=lot.exploitation_id, payload__lot=lot.pk).values_list('occurred_at', flat=True))
    dates.extend(timezone.localtime(value).date() for value in supply_dates)
    return max(dates)
