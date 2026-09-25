#!/usr/bin/env python3
"""Package the tracked source tree into a zip for a host that cannot git clone.

Why this exists
---------------
Some servers (notably mainland-China hosts) can reach GitHub over HTTP but
cannot hold a git connection open, so `git clone` times out. The BaoTa file
manager can upload a zip instead, and `scripts/deploy_server.sh --build`
works fine on a directory unpacked from a zip -- it does not require `.git`.

Usage
-----
    python scripts/make_deploy_zip.py [output.zip]

Default output is ``uh-deploy-src.zip`` in the current directory. Only files
tracked by git are included, so ``node_modules``, ``dist`` and local caches
never leak in, and ``.git`` itself is skipped (it is ~150 MB and unnecessary).
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile

DEFAULT_OUT = 'uh-deploy-src.zip'

# Files that decide whether deploy_server.sh tries to download from GitHub.
# `resolve_repo_root()` (deploy_server.sh:184-201) checks exactly these; when
# all four exist the script uses the current directory and never downloads.
GUARD_FILES = (
    'docker-compose.release.yml',
    'database/00-schema.sql',
    'database/02-bootstrap-tenant-template.sh',
    'database/templates/tenant_template.sql',
)

# What `--build` actually compiles.
BUILD_FILES = (
    'Dockerfile.server',
    'Dockerfile.web',
    'Dockerfile.web.dockerignore',
    '.dockerignore',
    'backend/requirements.txt',
    'frontend/package.json',
    'frontend/package-lock.json',
    'nginx/nginx.conf',
    'nginx/proxy_params.conf',
    'nginx/snippets/security_headers.conf',
)

# Markers that prove this is the fork and not upstream.
FORK_MARKERS = (
    ('backend/app/services/course/chaoxing/learning_manager.py', 'dispatch_scheduled_tasks'),
    ('backend/app/api/v1/admin.py', 'admin_overview'),
    ('frontend/src/pages/Admin.jsx', 'admin/overview'),
    ('backend/app/services/course/task_store.py', 'api_key'),
)


def tracked_files() -> list[str]:
    """Return tracked paths, using -z so non-ASCII names survive intact.

    Plain `git ls-files` applies core.quotepath and escapes non-ASCII names as
    octal (e.g. a Chinese filename becomes \\351\\203\\250...), which silently
    fails any later string comparison. -z emits raw NUL-separated bytes.
    """
    raw = subprocess.run(['git', 'ls-files', '-z'], capture_output=True, check=True).stdout
    return [p for p in raw.decode('utf-8', 'replace').split('\0') if p]


def build(out_path: str) -> int:
    files = [p for p in tracked_files() if os.path.isfile(p)]
    if not files:
        print('error: no tracked files found -- run this from the repo root', file=sys.stderr)
        return 1

    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            zf.write(path, path)

    size_mb = os.path.getsize(out_path) / 1048576
    print('wrote %s' % os.path.abspath(out_path))
    print('  %.2f MB   %d files' % (size_mb, len(files)))

    problems: list[str] = []

    print('\n=== files that stop the script from downloading (GUARD) ===')
    names = set(files)
    for f in GUARD_FILES:
        ok = f in names
        problems += [] if ok else [f]
        print('  [%s] %s' % ('OK ' if ok else 'MISS', f))

    print('\n=== files --build compiles ===')
    for f in BUILD_FILES:
        ok = f in names
        problems += [] if ok else [f]
        print('  [%s] %s' % ('OK ' if ok else 'MISS', f))

    print('\n=== fork markers inside the zip ===')
    with zipfile.ZipFile(out_path) as zf:
        for path, needle in FORK_MARKERS:
            try:
                text = zf.read(path).decode('utf-8', 'replace')
            except KeyError:
                problems.append(path)
                print('  [MISS] %s (absent)' % path)
                continue
            ok = needle in text
            problems += [] if ok else [path]
            print('  [%s] %-58s %r' % ('OK ' if ok else 'MISS', path, needle))

    print('\n=== marketing assets (should be absent) ===')
    leftovers = [p for p in files if p.startswith(('promo/', 'site/')) or 'university-helper-promo' in p]
    if leftovers:
        problems += leftovers
    print('  ', leftovers if leftovers else 'none -- clean')

    if problems:
        print('\nFAILED: %d problem(s): %s' % (len(problems), ', '.join(problems)), file=sys.stderr)
        return 1
    print('\nAll checks passed. Upload this zip to the server and unzip it, then run:')
    print('  bash scripts/deploy_server.sh --build --host <SERVER_IP> --admin-email <EMAIL> -y')
    return 0


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    return build(out_path)


if __name__ == '__main__':
    raise SystemExit(main())
