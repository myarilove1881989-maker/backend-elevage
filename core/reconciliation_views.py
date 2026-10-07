"""Supervision contains server-received originals only, scoped to one farm."""
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from .models import TerrainSubmission
from .permissions import require_member
from .terrain_reconciliation import DecisionInput, decide
from .terrain_transport import receipt


def reviewer(request):
    member = require_member(request.user)
    owner = member.role == 'OWNER' and member.exploitation.proprietaire_id == request.user.pk
    if not owner and not member.can_reconcile:
        raise PermissionDenied('RECONCILIATION_PERMISSION_REQUIRED')
    return member


def submission_data(row):
    return {'operation_uuid':str(row.client_operation_id), 'entity_type':row.entity_type,
        'original_author_id':row.author_user_id, 'device_id':row.device_id,
        'business_occurred_at':row.business_occurred_at, 'received_at':row.received_at,
        'original_payload':row.payload, 'decision_version':row.outcome.decision_version,
        'receipt':receipt(row)}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reconciliation_list(request):
    member = reviewer(request)
    rows = TerrainSubmission.objects.filter(exploitation_id=member.exploitation_id).select_related('outcome')
    state = request.query_params.get('state', 'NEEDS_RECONCILIATION')
    if state != 'ALL':
        from .terrain_models import BUSINESS_STATES
        if state not in BUSINESS_STATES:
            raise ValidationError('UNKNOWN_BUSINESS_STATUS')
        rows = rows.filter(outcome__business_status=state)
    if request.query_params.get('entity_type'):
        rows = rows.filter(entity_type=request.query_params['entity_type'])
    pagination = PageNumberPagination()
    pagination.page_size = 50
    page = pagination.paginate_queryset(rows.order_by('-received_at', '-pk'), request)
    return pagination.get_paginated_response([submission_data(row) for row in page])


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def reconciliation_detail(request, operation_uuid):
    member = reviewer(request)
    if request.method == 'POST':
        data = DecisionInput().run_validation(request.data)
        return Response(decide(request, operation_uuid, data))
    row = get_object_or_404(TerrainSubmission.objects.select_related('outcome'),
        exploitation_id=member.exploitation_id, client_operation_id=operation_uuid)
    result = submission_data(row)
    result['decisions'] = list(row.decisions.order_by('decided_at', 'pk').values(
        'decision_uuid', 'decision_actor_id', 'action', 'reason', 'effective_payload',
        'before_data', 'after_data', 'decided_at'))
    return Response(result)
