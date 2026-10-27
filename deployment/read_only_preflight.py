"""Standalone PostgreSQL diagnostics; no Django setup, writes, exports or repair.
Run ONLY after target/access approval. Credentials come from a protected libpq service.
Exit 0=PASS for requested phase, 2=STOP, 3=diagnostic unavailable. No row details printed.
"""
import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time


class Stop(Exception):
    pass


def valid_money(value):
    try:
        amount = Decimal(str(value))
        return (amount.is_finite() and Decimal('0') <= amount <= Decimal('9999999999.99')
                and abs(amount - amount.quantize(Decimal('0.01'))) <= Decimal('0.0000001'))
    except (InvalidOperation, ValueError, TypeError):
        return False


def valid_owner_rows(proprietor, owners):
    return owners == [(proprietor, True)]


def validate_registry(rows, phase, graph):
    actual = set(rows)
    known = {tuple(n) for n in graph['all_graph_nodes']}
    if len(actual) != len(rows) or not actual <= known:
        raise Stop('MIGRATION_REGISTRY_UNKNOWN_OR_DUPLICATE')
    if phase in ('baseline', 'final'):
        wanted = {tuple(n) for n in graph['historical_nodes_assumed']} if phase == 'baseline' else known
        if actual != wanted:
            raise Stop('MIGRATION_REGISTRY_MISMATCH')
    else:
        end = 22 if phase == 'pre0023' else 31
        expected_core = {n for n in known if n[0] == 'core' and int(n[1][:4]) <= end}
        common = {n for n in known if n[0] not in ('core', 'token_blacklist')}
        if {n for n in actual if n[0] == 'core'} != expected_core or not common <= actual:
            raise Stop('WRONG_MIGRATION_PHASE')
        # No partial/out-of-order token_blacklist chain is accepted.
        tokens = sorted(n for n in known if n[0] == 'token_blacklist')
        applied_tokens = sorted(n for n in actual if n[0] == 'token_blacklist')
        if applied_tokens != tokens[:len(applied_tokens)]:
            raise Stop('TOKEN_HISTORY_NOT_PREFIX')


def diagnostics(conn, phase, max_rows, budget_seconds, graph):
    deadline = time.monotonic() + budget_seconds
    def budget():
        if time.monotonic() > deadline:
            raise Stop('DIAGNOSTIC_TIME_BUDGET_EXCEEDED')
    result = {'phase': phase, 'business_rows_exported': 0}
    with conn.cursor() as c:
        c.execute("SELECT current_setting('transaction_read_only'), current_setting('server_version'), current_database(), current_user")
        readonly, version, database, user = c.fetchone()
        if readonly != 'on':
            raise Stop('READ_ONLY_NOT_ENFORCED')
        result['server_version'] = version
        c.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        role = c.fetchone()
        if role is None or any(role):
            raise Stop('PRIVILEGED_ROLE_REFUSED')
        c.execute('SELECT app, name FROM public.django_migrations ORDER BY app, name')
        registry = c.fetchall()
        validate_registry(registry, phase, graph)
        result['migration_count'] = len(registry)
    if phase in ('baseline', 'pre0023'):
        money = {}
        for table in ('core_payment', 'core_lettrage'):
            budget()
            # Fixed identifiers only. Driver preserves historical float values;
            # Decimal(str(value)) deliberately matches migration 0023 exactly.
            with conn.cursor(name='money_' + table) as c:
                c.itersize = 500
                c.execute('SELECT montant FROM public.' + table)
                count = invalid = 0
                for (value,) in c:
                    budget()
                    count += 1
                    if count > max_rows:
                        raise Stop('MONEY_ROW_BUDGET_EXCEEDED')
                    invalid += not valid_money(value)
                money[table] = {'rows_checked': count, 'invalid': invalid}
        result['money'] = money
        if any(v['invalid'] for v in money.values()):
            result['status'] = 'STOP'
            result['reason'] = 'MONEY_RECONCILIATION_REQUIRED'
            return result
    with conn.cursor() as c:
        budget()
        if phase == 'baseline':
            # This is a prerequisite for bootstrap 0017, not a PASS for 0032.
            c.execute("""SELECT count(*), count(*) FILTER (WHERE u.id IS NULL),
                count(*) FILTER (WHERE u.id IS NOT NULL AND NOT u.is_active)
                FROM public.core_exploitation f LEFT JOIN public.core_user u ON u.id=f.proprietaire_id""")
            farms, missing, inactive = c.fetchone()
            result['historical_owners'] = {'farms': farms, 'missing': missing, 'inactive_accounts': inactive}
            result['owner_0032'] = 'NOT_YET_APPLICABLE_REQUIRES_0017_THEN_PRE0032'
            if missing or inactive:
                result.update(status='STOP', reason='HISTORICAL_OWNER_REVIEW_REQUIRED')
                return result
        if phase in ('pre0032', 'final'):
            # Equivalent to 0032: exactly one OWNER INCLUDING inactive rows,
            # same proprietor and active membership. Account activity is separate.
            c.execute("""SELECT count(*) FROM public.core_exploitation f
                WHERE (SELECT count(*) FROM public.core_exploitationmembership m
                       WHERE m.exploitation_id=f.id AND m.role='OWNER') <> 1
                   OR NOT EXISTS (SELECT 1 FROM public.core_exploitationmembership m
                       WHERE m.exploitation_id=f.id AND m.role='OWNER'
                         AND m.user_id=f.proprietaire_id AND m.is_active IS TRUE)""")
            invalid = c.fetchone()[0]
            result['owner_0032'] = {'invalid_farms': invalid}
            if invalid:
                result.update(status='STOP', reason='OWNER_MEMBERSHIP_DIAGNOSTIC_REQUIRED')
                return result
        if phase == 'final':
            c.execute("""SELECT t.tgname, t.tgenabled FROM pg_trigger t
                 JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY t.tgname""")
            found = dict(c.fetchall())
            expected = graph.get('stage7_expected_triggers', [])
            if not expected or any(found.get(name) not in ('O', 'A') for name in expected):
                raise Stop('REQUIRED_TRIGGER_MISSING_OR_DISABLED')
            result['expected_triggers_present_enabled'] = len(expected)
            result['physical_schema_validation'] = 'PARTIAL_REGISTRY_AND_TRIGGER_NAMES_ONLY'
    budget()
    result['status'] = 'PASS'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=('baseline', 'pre0023', 'pre0032', 'final'))
    parser.add_argument('--service', required=True, help='Protected libpq service name, not a connection URL')
    parser.add_argument('--host', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--user', required=True, help='Approved read-only audit role, no superuser/BYPASSRLS')
    parser.add_argument('--max-rows', type=int, default=1000000)
    parser.add_argument('--budget-seconds', type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.max_rows <= 1000000 or not 1 <= args.budget_seconds <= 300:
        parser.error('Invalid resource budget')
    conn = None
    try:
        import psycopg2
        graph = json.loads(Path(__file__).with_name('preflight-graph.json').read_text())
        conn = psycopg2.connect(service=args.service, host=args.host, dbname=args.database,
            user=args.user, sslmode='verify-full', connect_timeout=5,
            application_name='elevage_stage7_read_only',
            options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=15000 -c search_path=pg_catalog,public -c row_security=off')
        conn.set_session(readonly=True, isolation_level='REPEATABLE READ', autocommit=False)
        # SET row_security=off causes an error rather than silently filtering rows;
        # it does not grant bypass privileges. The role itself is also checked.
        with conn.cursor() as c:
            c.execute('SELECT current_database(), current_user')
            if c.fetchone() != (args.database, args.user):
                raise Stop('DATABASE_IDENTITY_MISMATCH')
        result = diagnostics(conn, args.phase, args.max_rows, args.budget_seconds, graph)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] == 'PASS' else 2
    except Stop as exc:
        print(json.dumps({'status': 'STOP', 'reason': str(exc)}))
        return 2
    except Exception:
        # Do not expose driver errors, addresses, credentials or individual rows.
        print(json.dumps({'status': 'BLOCKED', 'reason': 'READ_ONLY_DIAGNOSTIC_UNAVAILABLE'}))
        return 3
    finally:
        if conn is not None:
            try:
                conn.rollback()
            finally:
                conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
