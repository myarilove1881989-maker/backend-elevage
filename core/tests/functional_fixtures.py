"""Explicit synthetic operators for legacy API regression tests (no role defaults)."""
from core.models import User, MembershipPermission


def operator_for_tests(owner, username, permissions):
    user = User.objects.create_user(username=username, exploitation=owner.exploitation)
    member = user.memberships.get(exploitation=owner.exploitation)
    MembershipPermission.objects.bulk_create([
        MembershipPermission(membership=member, code=code, created_by=owner)
        for code in permissions
    ])
    return user
