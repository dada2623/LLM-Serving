"""Check Git publication candidates and optionally export a reviewed ZIP.

Uses only the Python standard library. Findings never print credential values.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(
    r'(?:sk-[A-Za-z0-9_-]{12,}|hf_[A-Za-z0-9]{15,}|'
    r'gh[pousr]_[A-Za-z0-9]{15,}|github_pat_[A-Za-z0-9_]{15,}|'
    r'AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)'
)
DATE = re.compile(r'(?<!\d)(?:19|20)\d{2}-\d{2}-\d{2}|20\d{6}T\d{6}Z|\b\d{2}:\d{2}:\d{2}\b')
PERSONAL_PATH = re.compile(r'(?i)[a-z]:[\\/]+Users[\\/]+[^\\/\s]+|/mnt/[a-z]/Users/[^/\s]+|/ho' r'me/[^/\s]+')
PRIVATE_NAME = re.compile(r'(?i)\b(?:DESKTOP|LAPTOP)-[A-Z0-9]+\b|GPU-[a-f0-9-]{20,}')


def candidates():
    result = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT
    )
    return sorted(set(name for name in result.decode('utf-8').split('\0') if name))


def local_secrets():
    path = ROOT / '.env'
    if not path.exists():
        return []
    values = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        value = value.strip().strip('\"\'')
        if re.search(r'(?i)(?:key|token|password|secret)', key) and len(value) >= 6:
            if value.lower() not in {'change-me-before-use', 'local-dev-key-change-me', 'redacted'}:
                values.append(value)
    return values


def private_field(key):
    key = key.lower()
    return (
        key.endswith('_utc') or key in {
            'utc', 'timestamp', 'timestamp_ns', 'timestamp_ms', 'monotonic_ns',
            'start_ns', 'end_ns', 'send_ns', 'stop_offer_ns', 'drain_end_ns',
            'host_before_ns', 'host_after_ns', 'container_ns', 'startedat',
            'finishedat', 'createdat', 'hostname', 'computername', 'gpu_uuid',
            'production_env_sha256', 'production_compose_sha256',
        } or bool(re.search(r'(?:api[_-]?key|password|passwd|auth[_-]?token|hf_token|secret)', key))
    )


def audit(names):
    findings = []
    secrets = local_secrets()
    username = os.environ.get('USERNAME', '')
    def report(name, category, line=None):
        findings.append({'file': name, 'category': category, **({'line': line} if line else {})})
    for name in names:
        path = ROOT / name
        parts = Path(name).parts
        if path.name in {'INTERVIEW-QA.md', 'RESUME-MATERIAL.md'}:
            report(name, 'personal interview or resume notes')
        if (path.name.startswith('.env') and path.name != '.env.example') or path.suffix.lower() in {'.key', '.pem', '.pfx', '.p12'}:
            report(name, 'private credential file')
        if parts[0] in {'.git', '.tools', '.cache', '.python', '.venv', 'models', 'outputs', '.private'}:
            report(name, 'local-only file')
        if name.startswith(('benchmark/runs/', 'benchmark/configs/')) or 'sessions' in parts or 'monitoring' in parts:
            report(name, 'raw capture')
        if not path.is_file() or path.is_symlink():
            report(name, 'missing file or symbolic link')
            continue
        data = path.read_bytes()
        if any(secret.encode('utf-8') in data for secret in secrets):
            report(name, 'matches local credential')
        if path.suffix.lower() == '.png':
            continue
        try:
            text = data.decode('utf-8-sig')
        except UnicodeError:
            report(name, 'unreviewed binary file')
            continue
        for index, line in enumerate(text.splitlines(), 1):
            if TOKEN.search(line):
                report(name, 'credential pattern', index)
            if PERSONAL_PATH.search(line) or PRIVATE_NAME.search(line):
                report(name, 'machine identity', index)
            if len(username) >= 3 and re.search(r'(?i)(?<![\w-])' + re.escape(username) + r'(?![\w-])', line):
                report(name, 'local username', index)
            # Source may contain protocol versions and time-collection logic.
            if path.suffix.lower() in {'.md', '.json', '.csv', '.svg', '.lock'}:
                without_urls = re.sub(r'https?://[^\s\"<>]+', '', line)
                if DATE.search(without_urls):
                    report(name, 'calendar date or timestamp', index)
        if path.suffix.lower() == '.json':
            def walk(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if private_field(key):
                            report(name, 'private metadata field')
                        walk(item)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)
            walk(json.loads(text))
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write the checked candidates to a new ZIP')
    args = parser.parse_args()
    names = candidates()
    findings = audit(names)
    print(json.dumps({'files_checked': len(names), 'findings': findings}, indent=2))
    if findings:
        raise SystemExit(1)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                # A fixed ZIP format epoch avoids exposing filesystem times.
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, (ROOT / name).read_bytes())
        print(output)


if __name__ == '__main__':
    main()
