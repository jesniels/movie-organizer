"""Static checks of the front-end scripts in web/static/js/ (no JS engine — Python only).

The scripts are classic (non-module) scripts sharing one global scope, loaded in the order of
UI_SCRIPTS in py/ui/app.py. These checks catch the mistakes a split into several files invites:
- UI_SCRIPTS and the files in web/static/js/ match exactly; core.js loads first, main.js last;
- every script starts with 'use strict';
- no top-level name is declared in two scripts (a duplicate `let`/`const` is a SyntaxError that
  stops the whole second script from running);
- every function called from an inline handler (onclick="…" etc. in index.html or in HTML built
  by the scripts) is declared at the top level of some script.

Run from the repo root:  venv\\Scripts\\python.exe py\\test\\test_static_js.py
Exit code 0 = all checks passed.
"""
import ast
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parents[2]
JS_DIR = ROOT / "web" / "static" / "js"
APP_PY = ROOT / "py" / "ui" / "app.py"
INDEX_HTML = ROOT / "web" / "templates" / "index.html"

TOP_LEVEL_DECL = re.compile(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)|^(?:let|const|var|class)\s+([A-Za-z_$][\w$]*)")
INLINE_HANDLER = re.compile(r"\bon[a-z]+=\"([^\"]*)\"")
CALL = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*\(")
# Callable names an inline handler may use without a declaration in our scripts
JS_BUILTINS = {"if", "for", "while", "switch", "return", "function", "typeof", "parseInt", "parseFloat",
               "String", "Number", "Boolean", "encodeURIComponent", "decodeURIComponent"}

failures: List[str] = []


def check(cond: bool, message: str) -> None:
    """Record a failed check (all checks run; the exit code reports the result)."""
    if not cond:
        failures.append(message)


def ui_scripts() -> List[str]:
    """UI_SCRIPTS from py/ui/app.py, read with ast (importing the app is not needed)."""
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == "UI_SCRIPTS":
            return list(ast.literal_eval(node.value))
    raise AssertionError("UI_SCRIPTS not found in py/ui/app.py")


def top_level_names(text: str) -> List[str]:
    """Names declared at column 0 (function / let / const / var / class) of a script."""
    return [m.group(1) or m.group(2) for line in text.splitlines() if (m := TOP_LEVEL_DECL.match(line))]


def main() -> int:
    scripts = ui_scripts()
    on_disk = sorted(p.name for p in JS_DIR.glob("*.js"))
    check(sorted(scripts) == on_disk, f"UI_SCRIPTS {sorted(scripts)} != files in web/static/js {on_disk}")
    check(len(scripts) == len(set(scripts)), "UI_SCRIPTS lists a script twice")
    check(scripts[:1] == ["core.js"], "core.js must load first")
    check(scripts[-1:] == ["main.js"], "main.js must load last")
    check(not (ROOT / "web" / "static" / "app.js").exists(), "old web/static/app.js still exists")

    declared: Dict[str, str] = {}
    texts: Dict[str, str] = {}
    for name in on_disk:
        text = (JS_DIR / name).read_text(encoding="utf-8")
        texts[name] = text
        check(text.lstrip().startswith("'use strict';"), f"{name}: does not start with 'use strict'")
        for ident in top_level_names(text):
            check(ident not in declared, f"{ident} declared in both {declared.get(ident)} and {name}")
            declared.setdefault(ident, name)

    sources = {"index.html": INDEX_HTML.read_text(encoding="utf-8"), **texts}
    called: Set[str] = set()
    for src_name, text in sources.items():
        for handler in INLINE_HANDLER.findall(text):
            for ident in CALL.findall(handler):
                if ident in JS_BUILTINS:
                    continue
                called.add(ident)
                check(ident in declared, f"{src_name}: inline handler calls undeclared {ident}() — {handler!r}")

    print(f"{len(on_disk)} scripts, {len(declared)} top-level names, {len(called)} functions used by inline handlers")
    for f in failures:
        print("FAIL:", f)
    print("OK" if not failures else f"{len(failures)} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
