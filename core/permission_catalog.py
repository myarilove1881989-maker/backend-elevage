"""Closed action catalogue. Versioned HTTP policy, separate from signed V1 grants."""
PERMISSION_CONTRACT_VERSION = 1

DOMAIN_ACTIONS = {
    'purchases': ('read', 'create', 'delete'),
    'animal_sales': ('read', 'create', 'delete'),
    'expenses': ('read', 'create', 'delete'),
    'mortality': ('read', 'create', 'delete'),
    'births': ('read', 'create', 'delete'),
    'donations': ('read', 'create', 'delete'),
    'thefts': ('read', 'create', 'delete'),
    'clients': ('read', 'create'), 'lots': ('read',),
    'stocks': ('read',), 'species': ('read', 'create'),
    'egg_collections': ('read', 'create', 'update', 'delete'),
    'egg_sales': ('read', 'create', 'delete'), 'cash': ('read', 'create'),
    'feed': ('read', 'create', 'update', 'delete'), 'weights': ('read', 'create'),
    'agenda': ('read', 'progress', 'plan'),
    'history': ('read',), 'finance': ('read',),
    'users': ('manage',), 'devices': ('manage',),
    'reconciliation': ('read', 'decide'),
}
for _domain in ('animal_sales', 'egg_sales', 'expenses', 'mortality', 'donations', 'thefts'):
    DOMAIN_ACTIONS[_domain] += ('cancel',)
PERMISSIONS = frozenset(f'{domain}.{action}' for domain, actions in DOMAIN_ACTIONS.items()
                        for action in actions)
OWNER_ONLY = frozenset({'users.manage', 'devices.manage'})
# Finance is a right of every active employee, never an individual restriction.
BASE_READS = frozenset(code for code in PERMISSIONS
                      if code.endswith('.read') and code not in {'agenda.read', 'reconciliation.read'})
ASSIGNABLE = PERMISSIONS - OWNER_ONLY - BASE_READS
BUSINESS_ACTIONS = frozenset(code for code in PERMISSIONS
                            if not code.endswith('.read') and code.split('.')[0] not in
                            {'agenda', 'users', 'devices', 'reconciliation'})
ENTITY_DOMAINS = {
    'ACHAT': 'purchases', 'VENTE': 'animal_sales', 'VENTE_ANIMAUX': 'animal_sales',
    'VENTE_OEUFS': 'egg_sales', 'DEPENSE': 'expenses', 'MORTALITE': 'mortality',
    'NAISSANCE': 'births', 'DON': 'donations', 'VOL': 'thefts', 'CLIENT': 'clients',
    'COLLECTE_OEUFS': 'egg_collections', 'ENCAISSEMENT': 'cash',
    'ALIMENTATION': 'feed', 'PESEE': 'weights',
}


def operation_action(entity_type, operation_type):
    if operation_type == 'REVERSE' and entity_type in {
            'VENTE_ANIMAUX', 'VENTE_OEUFS', 'DEPENSE', 'MORTALITE', 'DON', 'VOL'}:
        return ENTITY_DOMAINS[entity_type] + '.cancel'
    if entity_type == 'COLLECTE_OEUFS' and operation_type == 'UPDATE':
        return 'egg_collections.update'
    if entity_type == 'TASK' and operation_type == 'UPDATE':
        return 'agenda.progress'
    if operation_type != 'CREATE' or entity_type not in ENTITY_DOMAINS:
        return None
    return ENTITY_DOMAINS[entity_type] + '.create'
