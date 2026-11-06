"""Tenant-scoped business history for every active farm member. Read only."""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from .permissions import HasExploitation
from .access_policy import authorize
from .models import AuditEvent
from .foundation_views import AuditSerializer


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasExploitation])
def business_history(request):
    authorize(request.user, request.user.exploitation_id, 'history.read')
    if request.query_params.get('view') == 'operations':
        return operation_history(request)
    query = AuditEvent.objects.filter(exploitation_id=request.user.exploitation_id,
                                     category__in=['BUSINESS', 'AGENDA'])
    pagination = PageNumberPagination()
    pagination.page_size = 100
    page = pagination.paginate_queryset(query, request)
    return pagination.get_paginated_response(AuditSerializer(page, many=True).data)


def operation_history(request):
    from datetime import timedelta
    from rest_framework import serializers
    from .models import TerrainSubmission, TerrainEntityMapping, User, Lot
    from django.db.models import Q
    from .permissions import require_owner
    from .permission_catalog import operation_action
    require_owner(request.user)

    class Filters(serializers.Serializer):
        author = serializers.CharField(max_length=100, required=False)
        lot = serializers.CharField(max_length=100, required=False)
        start = serializers.DateTimeField(required=False)
        end = serializers.DateTimeField(required=False)
        kind = serializers.CharField(max_length=32, required=False)
        status = serializers.CharField(max_length=24, required=False)
        operation = serializers.UUIDField(required=False)

    filters = Filters(data=request.query_params)
    filters.is_valid(raise_exception=True)
    values = filters.validated_data
    query = TerrainSubmission.objects.filter(exploitation_id=request.user.exploitation_id).select_related(
        'outcome', 'offline_authorization').order_by('-local_recorded_at', '-id')
    if 'operation' in values:
        values = {'operation':values['operation']}
    if 'author' in values:
        author = values['author']
        if author.isdigit():
            query = query.filter(author_user_id=int(author))
        else:
            authors = User.objects.filter(exploitation_id=request.user.exploitation_id).filter(
                Q(username__icontains=author) | Q(first_name__icontains=author) | Q(last_name__icontains=author))
            query = query.filter(author_user_id__in=authors.values('pk'))
    if 'start' in values: query = query.filter(local_recorded_at__gte=values['start'])
    if 'end' in values: query = query.filter(local_recorded_at__lt=values['end'])
    if 'kind' in values: query = query.filter(entity_type=values['kind'])
    if 'status' in values: query = query.filter(outcome__business_status=values['status'])
    if 'operation' in values: query = query.filter(client_operation_id=values['operation'])
    if 'lot' in values:
        lot = values['lot']
        lot_ids = [int(lot)] if lot.isdigit() else list(Lot.objects.filter(
            exploitation_id=request.user.exploitation_id,nom__icontains=lot).values_list('pk',flat=True)[:101])
        if len(lot_ids)>100:
            raise serializers.ValidationError('Précisez le nom du lot.')
        local_ids = [str(value) for value in TerrainEntityMapping.objects.filter(
            exploitation_id=request.user.exploitation_id, entity_type='LOT', server_entity_id__in=lot_ids
        ).values_list('local_entity_id',flat=True)]
        matches = Q(payload__lot_ref__server_id__in=lot_ids) | Q(payload__lot_ref__local_uuid__in=local_ids)
        for lot_id in lot_ids: matches |= Q(outcome__affected_lot_ids__contains=[lot_id])
        query = query.filter(matches)
    # The terrain register alone excludes normal owner HTTP operations. Merge
    # their immutable audit facts in SQL before pagination, never rewriting authors.
    from django.db.models import F, Value, CharField
    from django.db.models.functions import Cast
    online = online_operation_events(request, values)
    terrain_index=query.order_by().annotate(history_id=F('pk'),history_date=F('local_recorded_at'),
        history_source=Value('terrain',output_field=CharField())).values('history_id','history_date','history_source')
    online_index=online.order_by().annotate(history_id=F('pk'),history_date=F('applied_at'),
        history_source=Value('online',output_field=CharField())).values('history_id','history_date','history_source')
    index=terrain_index.union(online_index).order_by('-history_date','-history_id','history_source')
    pagination = PageNumberPagination()
    pagination.page_size = 50
    selected=list(pagination.paginate_queryset(index, request))
    terrain_ids=[item['history_id'] for item in selected if item['history_source']=='terrain']
    online_ids=[item['history_id'] for item in selected if item['history_source']=='online']
    page=list(query.filter(pk__in=terrain_ids))
    authors = dict(User.objects.filter(exploitation_id=request.user.exploitation_id,
        pk__in=[row.author_user_id for row in page]).values_list('id', 'username'))
    results = []
    for row in page:
        outcome = row.outcome
        lot_ids = set(outcome.affected_lot_ids)
        ref = row.payload.get('lot_ref',{})
        if isinstance(ref,dict) and type(ref.get('server_id')) is int: lot_ids.add(ref['server_id'])
        if isinstance(ref,dict) and ref.get('local_uuid'):
            from django.core.exceptions import ValidationError as ModelValidationError
            try:
                lot_ids.update(TerrainEntityMapping.objects.filter(exploitation_id=row.exploitation_id,
                    entity_type='LOT',local_entity_id=ref['local_uuid']).values_list('server_entity_id',flat=True))
            except (ModelValidationError,ValueError):
                pass  # A refused malformed reference remains visible, without an invented lot.
        cancellations = TerrainSubmission.objects.filter(exploitation_id=row.exploitation_id,
            operation_type='REVERSE', payload__original_operation_id=str(row.client_operation_id))
        tolerance = AuditEvent.objects.filter(exploitation_id=row.exploitation_id,
            operation_id=row.client_operation_id, action='TERRAIN_APPLIED',
            after_data__ordinary_business_tolerance=True).exists()
        results.append({'id':row.pk, 'operation_id':str(row.client_operation_id),
            'actor_user_id':row.author_user_id, 'author_name':authors.get(row.author_user_id, 'Ancien employé'),
            'entity_type':row.entity_type, 'operation_type':row.operation_type,
            'business_occurred_at':row.business_occurred_at, 'local_recorded_at':row.local_recorded_at,
            'received_at':row.received_at, 'applied_at':outcome.applied_at,
            'business_status':outcome.business_status, 'reason_code':outcome.reason_code,
            'reason_text':outcome.reason_text, 'payload':row.payload,
            'lot_ids':outcome.affected_lot_ids, 'authorization_id':str(row.offline_authorization_id),
            'lots':list(Lot.objects.filter(exploitation_id=row.exploitation_id,pk__in=lot_ids).values('id','nom')),
            'permission':operation_action(row.entity_type,row.operation_type),
            'rights_version':row.offline_authorization.rights_version,
            'ordinary_business_tolerance':tolerance,
            'received_with_old_permissions':AuditEvent.objects.filter(exploitation_id=row.exploitation_id,
                operation_id=row.client_operation_id,action='TERRAIN_RECEIVED',
                after_data__ordinary_business_tolerance=True).exists(),
            'late_sync':row.received_at-row.local_recorded_at > timedelta(minutes=5),
            'original_operation_id':row.payload.get('original_operation_id') if row.operation_type=='REVERSE' else None,
            'cancellations':[{'operation_id':str(item.client_operation_id),
                'status':item.outcome.business_status, 'reason':item.payload.get('reason')}
                for item in cancellations.select_related('outcome')]})
    by_id={('terrain',item['id']):item for item in results}
    for item in online.filter(pk__in=online_ids):
        by_id[('online',item.pk)]=online_operation_row(item,request.user.exploitation_id)
    return pagination.get_paginated_response([by_id[(item['history_source'],item['history_id'])] for item in selected])


ONLINE_KINDS={'core.achat':'ACHAT','core.depense':'DEPENSE','core.vente':'VENTE_ANIMAUX',
    'core.venteoeufs':'VENTE_OEUFS','core.collecteoeufs':'COLLECTE_OEUFS','core.client':'CLIENT',
    'core.payment':'ENCAISSEMENT','core.consommationaliment':'ALIMENTATION',
    'core.peseeproduction':'PESEE','core.vaccination':'VACCINATION','core.task':'TASK'}


def online_operation_events(request,values):
    from django.db.models import Q,OuterRef,Exists
    from .models import TerrainSubmission,User,Lot
    farm=request.user.exploitation_id
    q=AuditEvent.objects.filter(exploitation_id=farm,source='ONLINE',category__in=['BUSINESS','AGENDA'],
        action__in=['CREATE','UPDATE','DELETE']).filter(
        Q(entity_type__in=ONLINE_KINDS)|Q(entity_type='core.mouvement',after_data__type_mouvement__in=['NAISSANCE','MORTALITE','DON','VOL']))
    # A terrain command and its derived rows are already shown once by the register.
    q=q.annotate(terrain_record=Exists(TerrainSubmission.objects.filter(exploitation_id=farm,
        client_operation_id=OuterRef('operation_id')))).filter(terrain_record=False)
    # Egg sales also create a generic Vente in the same atomic operation.
    q=q.annotate(egg_sale=Exists(AuditEvent.objects.filter(exploitation_id=farm,
        correlation_id=OuterRef('correlation_id'),entity_type='core.venteoeufs'))).exclude(entity_type='core.vente',egg_sale=True)
    if 'operation' in values:return q.filter(correlation_id=values['operation'])
    if 'author' in values:
        term=values['author']
        if term.isdigit():q=q.filter(actor_user_id=int(term))
        else:
            ids=User.objects.filter(exploitation_id=farm).filter(Q(username__icontains=term)|Q(first_name__icontains=term)|Q(last_name__icontains=term)).values('pk')
            q=q.filter(actor_user_id__in=ids)
    if 'start' in values:q=q.filter(applied_at__gte=values['start'])
    if 'end' in values:q=q.filter(applied_at__lt=values['end'])
    if 'status' in values and values['status']!='CONFIRMED':return q.none()
    if 'kind' in values:
        kind=values['kind'];types=[key for key,val in ONLINE_KINDS.items() if val==kind]
        q=q.filter(Q(entity_type__in=types)|Q(entity_type='core.mouvement',after_data__type_mouvement=kind))
    if 'lot' in values:
        term=values['lot'];ids=[int(term)] if term.isdigit() else list(Lot.objects.filter(exploitation_id=farm,nom__icontains=term).values_list('pk',flat=True)[:101])
        if len(ids)>100:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('Précisez le nom du lot.')
        q=q.filter(Q(after_data__lot_id__in=ids)|Q(before_data__lot_id__in=ids))
    return q


def online_operation_row(event,farm):
    from .models import User,Lot
    from .permission_catalog import operation_action
    data=event.after_data or event.before_data
    payload=dict(data)
    for source,target in [('lot_id','lot'),('client_id','client'),('categorie_id','categorie')]:
        if source in data:payload[target]=data[source]
    kind=data.get('type_mouvement') if event.entity_type=='core.mouvement' else ONLINE_KINDS[event.entity_type]
    ids=[data['lot_id']] if type(data.get('lot_id')) is int else []
    author=User.objects.filter(pk=event.actor_user_id,exploitation_id=farm).values_list('username',flat=True).first()
    return {'id':-event.pk,'operation_id':'online:'+str(event.pk),'actor_user_id':event.actor_user_id,
        'author_name':author or 'Auteur historique non renseigné','entity_type':kind,
        'operation_type':{'CREATE':'CREATE','UPDATE':'UPDATE','DELETE':'DELETE'}[event.action],
        'business_occurred_at':event.business_occurred_at or event.applied_at,
        'local_recorded_at':event.applied_at,'received_at':event.applied_at,'applied_at':event.applied_at,
        'business_status':'CONFIRMED','reason_code':event.reason_code,'reason_text':event.reason_text,
        'payload':payload,'lot_ids':ids,'lots':list(Lot.objects.filter(exploitation_id=farm,pk__in=ids).values('id','nom')),
        'authorization_id':None,'permission':operation_action(kind,'CREATE'),'rights_version':None,
        'ordinary_business_tolerance':False,'received_with_old_permissions':False,'late_sync':False,
        'original_operation_id':None,'cancellations':[],'source':'ONLINE'}
