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
    pagination = PageNumberPagination()
    pagination.page_size = 50
    page = pagination.paginate_queryset(query, request)
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
    return pagination.get_paginated_response(results)
