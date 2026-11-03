"""Adapters for the existing DEPENSE and ALIMENTATION signed declarations."""
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .feed_stock import stock_actor, distribute, edit_distribution
from .terrain_operations import Reference, resolve, mark_lots


def purchase(row,user):
    stock_actor(user,'expenses.create')
    p=dict(row.payload)
    if set(p)-{'lot_ref','categorie_id','montant','note','feed_supply','stock_version'} or p.get('stock_version')!=1:
        raise ValidationError('FEED_EXPENSE_FIELDS_INVALID')
    lot=resolve(row,Reference().run_validation(p.pop('lot_ref',None)))
    supply=dict(p['feed_supply'])
    target=supply.pop('lot_ref',None)
    if target is not None:supply['lot']=resolve(row,Reference().run_validation(target)).pk
    body={'operation_uuid':str(row.client_operation_id),'lot':lot.pk,'categorie':p.get('categorie_id'),
        'montant':p.get('montant'),'note':p.get('note',''),'date':str(timezone.localdate(row.business_occurred_at)),
        'feed_supply':supply}
    from .feed_purchases import purchase as apply_purchase
    from .models import Depense
    result=apply_purchase(user,body)
    return mark_lots(Depense.objects.get(pk=result['id']),lot)


def feed(row,user):
    p=dict(row.payload)
    if row.operation_type=='CREATE':
        lot=resolve(row,Reference().run_validation(p.pop('lot_ref',None)))
        if set(p)-{'stock_version','species','product','source_lot','quantite_kg','note','supply_parent_uuid'}:
            raise ValidationError('FEED_DISTRIBUTION_FIELDS_INVALID')
        item=distribute(user,{**p,'operation_uuid':str(row.client_operation_id),'lot':lot.pk,
            'distribution_at':row.business_occurred_at.isoformat()})
    elif row.operation_type in {'UPDATE','REVERSE'}:
        from .models import ConsommationAliment
        reference=Reference().run_validation(p.pop('distribution_ref',None))
        original=resolve(row,reference,'ALIMENTATION',ConsommationAliment)
        if p.pop('stock_version',None)!=1:raise ValidationError('FEED_CONTRACT_REQUIRED')
        item=edit_distribution(user,original.pk,{'operation_uuid':str(row.client_operation_id),
            'expected_version':row.expected_server_version,**p},cancel=row.operation_type=='REVERSE')
    else:raise ValidationError('FEED_OPERATION_UNSUPPORTED')
    return mark_lots(item,item.lot)
