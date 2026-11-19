#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-release syntax gate for PowerUp Toolbox (used by CI and build_installer.sh).

1. Every inline <script> in index.html must parse (`node --check`). The front end is one large inline
   script: a single SyntaxError (v2.8.7: a duplicate top-level `let`) stops ALL of it, so the app shows
   its UI but cannot open any file.
2. The project's Python modules must compile.

Usage:  python3 tools/check_syntax.py [--require-node]
Without --require-node a missing node only prints a warning (the JS check is skipped).
Exit code 0 = OK, 1 = a check failed.
"""
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_FILES = ["server.py", "local_albums.py", "gphotos.py", "winpath.py", "winshell.py",
            os.path.join("windows", "launcher.py"), os.path.join("windows", "win_support.py")]
# inline scripts only: skip <script src=...> and JSON data blocks
SCRIPT_RE = re.compile(r'<script(?![^>]*\bsrc\s*=)(?![^>]*\btype\s*=\s*"?application/(?:ld\+)?json)[^>]*>(.*?)</script>',
                       re.I | re.S)


def check_js(require_node):
    node = shutil.which("node")
    if not node:
        msg = "node not found: index.html script syntax check skipped"
        if require_node:
            print("ERROR: " + msg)
            return False
        print("WARNING: " + msg)
        return True
    with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
        html = f.read()
    blocks = SCRIPT_RE.findall(html)
    if not blocks:
        print("ERROR: index.html has no inline <script>")
        return False
    ok = True
    for i, body in enumerate(blocks, 1):
        fd, tmp = tempfile.mkstemp(suffix=".js", prefix="powerup_inline_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(body)
            r = subprocess.run([node, "--check", tmp], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                ok = False
                print("ERROR: index.html inline <script> #%d has a syntax error:\n%s" % (i, (r.stderr or r.stdout).strip()))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
    if ok:
        print("index.html: %d inline script(s) OK" % len(blocks))
    return ok


def check_py():
    ok = True
    tmpdir = tempfile.mkdtemp(prefix="powerup_pyc_")
    try:
        for rel in PY_FILES:
            path = os.path.join(ROOT, rel)
            if not os.path.isfile(path):
                continue
            try:
                py_compile.compile(path, cfile=os.path.join(tmpdir, "x.pyc"), doraise=True)
            except py_compile.PyCompileError as e:
                ok = False
                print("ERROR: %s\n%s" % (rel, e.msg))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if ok:
        print("python: %d file(s) OK" % len(PY_FILES))
    return ok


def main():
    require_node = "--require-node" in sys.argv[1:]
    results = [check_js(require_node), check_py()]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
