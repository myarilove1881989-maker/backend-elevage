from django.db import transaction
from django.core.exceptions import ValidationError as ModelError
from rest_framework.decorators import api_view,permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied,ValidationError
from rest_framework.response import Response
from .audit import audit_scope
from .request_context import online_context
from .permissions import require_owner
from .owner_http import owner_http_request
from .supply_services import execute


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def transfer(request):
    require_owner(request.user)
    if not owner_http_request(request):raise PermissionDenied('OWNER_HTTP_CONTRACT_REQUIRED')
    if set(request.data)!={'operation_uuid','product','species','source_lot','target_lot','quantity','reason'}:
        raise ValidationError('EXPLICIT_TRANSFER_FIELDS_REQUIRED')
    reason=request.data['reason']
    if not isinstance(reason,str) or len(reason.strip())<3:raise ValidationError('TRANSFER_REASON_REQUIRED')
    with audit_scope(online_context(request),reason_code='FEED_TRANSFER',reason_text=reason):
        try:
            op=execute(request.user,request.user.exploitation_id,request.data['operation_uuid'],'TRANSFER',
                {key:request.data[key] for key in ['product','species','source_lot','target_lot','quantity']},reason=reason)
        except ModelError as exc:raise ValidationError(exc.messages)
    return Response({'operation_uuid':str(op.operation_uuid),'confirmed':True})
