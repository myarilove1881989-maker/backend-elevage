"""Local push gate; never prints credential values. No deployment capability."""
import pathlib, re, subprocess, sys

def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + str(pathlib.Path(__file__).resolve().parents[1]), '-C', str(pathlib.Path(__file__).resolve().parents[1]), *args])


def blobs(ref):
    names = [n.decode() for n in git('ls-tree', '-r', '--name-only', '-z', ref).split(b'\0') if n]
    root = pathlib.Path(__file__).resolve().parents[1]
    process = subprocess.Popen(['git', '-c', 'safe.directory=' + str(root), '-C', str(root), 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for name in names:
            process.stdin.write((ref + ':' + name + '\n').encode()); process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3 or header[1] != b'blob':
                raise SystemExit('Unexpected Git object in scan')
            data = process.stdout.read(int(header[2])); process.stdout.read(1)
            yield name, data
    finally:
        process.stdin.close(); process.stdout.close(); process.wait()

def check_tree(ref='HEAD'):
    errors = []
    for name, data in blobs(ref):
        p = pathlib.PurePosixPath(name)
        public_ca = name in {'android/app/src/lab/res/raw/elevage_lab_ca.pem', 'android/app/src/labWifi/res/raw/elevage_lab_ca.pem'}
        if (p.name.startswith('.env') or p.name in {'key.properties', '.netrc', '.pgpass'} or p.suffix.lower() in {'.jks', '.keystore', '.p12', '.pfx', '.key', '.sqlite3', '.db', '.apk', '.aab'} or (p.suffix == '.pem' and not public_ca)):
            errors.append(name + ': private/local file')
            continue
        if b'\0' in data:
            continue
        patterns = [rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\s+[A-Za-z0-9+/=]{32,}', rb'gh[pousr]_[A-Za-z0-9]{30,}', rb'github_pat_[A-Za-z0-9_]{40,}', rb'(?im)^\s*EMAIL_HOST_PASSWORD\s*=\s*[\x22\x27][^\x22\x27\r\n]{8,}[\x22\x27]']
        if any(re.search(pattern, data) for pattern in patterns):
            errors.append(name + ': suspected secret')
    if errors:
        raise SystemExit('Push refused:\n' + '\n'.join(errors))

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--check':
        check_tree()
    else:
        expected = (pathlib.Path(__file__).parent / 'official-repository.txt').read_text().strip()
        actual = sys.argv[2] if len(sys.argv) > 2 else ''
        if actual.removesuffix('.git') != expected.removesuffix('.git'):
            raise SystemExit('Push refused: unexpected remote repository')
        for line in sys.stdin:
            local_ref, local_sha, remote_ref, remote_sha = line.split()
            if remote_ref != 'refs/heads/develop' and not remote_ref.startswith('refs/heads/feature/'):
                raise SystemExit('Push refused: only develop and feature/* are allowed')
            if set(local_sha) == {'0'}:
                raise SystemExit('Push refused: branch deletion requires separate approval')
            check_tree(local_sha)
