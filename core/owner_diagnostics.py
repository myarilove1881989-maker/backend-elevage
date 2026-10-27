"""Read-only preflight; no account is selected, repaired or reassigned."""
def owner_inconsistencies(apps):
    Farm = apps.get_model('core', 'Exploitation')
    Member = apps.get_model('core', 'ExploitationMembership')
    problems = []
    for farm in Farm.objects.all().iterator():
        owners = list(Member.objects.filter(exploitation_id=farm.pk, role='OWNER').values(
            'user_id', 'is_active'))
        if len(owners) != 1 or owners[0]['user_id'] != farm.proprietaire_id or not owners[0]['is_active']:
            problems.append(farm.pk)
    return problems
