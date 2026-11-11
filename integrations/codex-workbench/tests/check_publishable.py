"""Inspect the exact Git-index source, without printing sensitive matches."""
from pathlib import Path, PurePosixPath
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'literal_user_home': re.compile(r'(?:[A-Za-z]:[\\/]+Users[\\/]+[^\s\"\'<>]+|\x2fUsers\x2f[^/\s]+\x2f|\x2fhome\x2f[^/\s]+\x2f)'),
    'saved_conversation_id': re.compile(r'\b01[0-9a-f]{6}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', re.I),
    'credential_value': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)'),
    'personal_email': re.compile(r'[A-Za-z0-9_.+\-]+@(?!(?:example\.(?:com|org|invalid))\b)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}'),
}
FORBIDDEN_DIRS = {'state','workspace','node_modules','__pycache__','.drawio-codex','artifacts'}
FORBIDDEN_SUFFIXES = {'.png','.jpg','.jpeg','.ico','.drawio','.lnk','.log','.exe','.dll','.pyc'}


def inspect_files(files):
    findings=[]
    for name,data in files.items():
        path=PurePosixPath(name)
        if FORBIDDEN_DIRS.intersection(path.parts) or path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name.startswith('.env') or path.name.endswith(('-result.json','-summary.json','-verification.json')):
            findings.append({'file':name,'kind':'runtime_or_private_artifact'})
        try:text=data.decode('utf-8')
        except UnicodeDecodeError:
            findings.append({'file':name,'kind':'unexpected_binary'})
            continue
        for number,line in enumerate(text.splitlines(),1):
            for kind,pattern in PATTERNS.items():
                if pattern.search(line):findings.append({'file':name,'line':number,'kind':kind})
    return findings


def main():
    repo=Path(subprocess.check_output(['git','rev-parse','--show-toplevel'],cwd=ROOT,text=True,encoding='utf-8').strip())
    prefix=ROOT.relative_to(repo).as_posix()
    names=subprocess.check_output(['git','ls-files','-z','--',prefix],cwd=repo).decode('utf-8').split('\0')
    files={name:subprocess.check_output(['git','show',':'+name],cwd=repo) for name in names if name}
    if not files:raise SystemExit('No tracked integration source. Stage the intended source before auditing.')
    findings=inspect_files(files)
    print(json.dumps({'checked_files':len(files),'passed':not findings,'findings':findings},indent=2))
    return 1 if findings else 0


if __name__=='__main__':raise SystemExit(main())
