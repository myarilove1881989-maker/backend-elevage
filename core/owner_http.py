"""Opt-in HTTP owner operations; signed/offline and v1 clients keep their policy."""
from .permissions import require_member

def owner_http_request(request):
    return (contract_version(request) == 2
            and require_member(request.user).role == 'OWNER')

def contract_version(request):
    return 2 if any(part.strip() == 'elevage-contract=2' for part in
                    request.headers.get('Accept', '').split(';')[1:]) else 1

def owner_context(actor, farm_id):
    from .audit import current_actor_context
    context = current_actor_context()
    return bool(context and context.source == 'ONLINE' and context.owner_http and
                context.author_id == actor.pk and context.exploitation_id == farm_id and
                context.operation_id is None)
