"""Bounded public probe. Run only after approval to wake the target service."""
import argparse
import json
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--origin', required=True)
    a = p.parse_args()
    u = urlsplit(a.origin)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
        p.error('Use an explicit HTTPS origin with no credentials/path/query')
    opener = urllib.request.build_opener(NoRedirect)
    try:
        for method in ('GET', 'HEAD'):
            request = urllib.request.Request(a.origin + '/api/health/', method=method)
            with opener.open(request, timeout=5) as response:
                body = response.read(1025)
                if response.status != 200 or len(body) > 1024:
                    raise ValueError()
                if method == 'GET' and json.loads(body) != {'status': 'ok'}:
                    raise ValueError()
                if method == 'HEAD' and body:
                    raise ValueError()
        print('PASS HTTP_HEALTH_ONLY; schema and business readiness remain separate gates')
        return 0
    except Exception:
        print('STOP HTTP_HEALTH_FAILED (details suppressed)')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
