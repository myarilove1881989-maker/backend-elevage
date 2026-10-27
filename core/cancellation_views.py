"""Online cancellation of autonomous expenses; never rewrites V1 submissions."""
from uuid import UUID
from django.db import transaction, IntegrityError
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError, NotFound
from rest_framework.response import Response
from .models import Exploitation, Depense, ConsommationAliment, ExpenseCancellation, AuditEvent
from .audit import audit_scope
from .request_context import online_context, lock_write_context
from .permissions import HasExploitation
from .access_policy import authorize


def cancel_expense(request, pk):
    if set(request.data) - {'reason', 'operation_uuid'}:
        raise ValidationError('UNKNOWN_CANCELLATION_FIELDS')
    reason = request.data.get('reason')
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 10000:
        raise ValidationError({'reason': 'CANCELLATION_REASON_REQUIRED'})
    reason = reason.strip()
    try:
        operation_uuid = UUID(str(request.data.get('operation_uuid')))
    except (ValueError, TypeError):
        raise ValidationError({'operation_uuid': 'UUID_REQUIRED'})
    with audit_scope(online_context(request), reason_code='EXPENSE_CANCELLED', reason_text=reason):
        farm = Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
        lock_write_context(request)
        expense = Depense.history.select_for_update().filter(pk=pk, lot__exploitation=farm).first()
        if expense is None:
            raise NotFound()
        authorize(request.user, farm, 'expenses.delete', expense)
        previous = ExpenseCancellation.objects.filter(operation_uuid=operation_uuid).first()
        if previous:
            if previous.expense_id != expense.pk or previous.author_id != request.user.pk or previous.reason != reason:
                raise ValidationError('CANCELLATION_UUID_CONFLICT')
            return Response({'id': previous.pk, 'original_id': expense.pk, 'status': 'CANCELLED', 'replayed': True})
        if expense.voided_at is not None:
            raise ValidationError('BUSINESS_EFFECT_ALREADY_REVERSED')
        if ConsommationAliment.history.filter(depense_id=expense.pk).exists():
            raise ValidationError('LINKED_FEED_CANCELLATION_NOT_SUPPORTED')
        if AuditEvent.objects.filter(entity_type='core.depense', entity_id=str(expense.pk), operation_id__isnull=False).exists():
            raise ValidationError('V1_RECONCILIATION_REQUIRED')
        try:
            with transaction.atomic():
                cancellation = ExpenseCancellation.objects.create(expense=expense, exploitation=farm,
                    operation_uuid=operation_uuid, author=request.user, reason=reason)
        except IntegrityError:
            raise ValidationError('CANCELLATION_UUID_CONFLICT')
        expense.voided_at = timezone.now()
        expense.void_decision_uuid = operation_uuid
        expense.save(update_fields=['voided_at', 'void_decision_uuid'])
        farm.business_revision += 1
        farm.save(update_fields=['business_revision'])
        return Response({'id': cancellation.pk, 'original_id': expense.pk, 'status': 'CANCELLED', 'replayed': False})


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated, HasExploitation])
def expense_cancellation(request, pk):
    return cancel_expense(request, pk)
