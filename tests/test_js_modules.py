"""Static checks on the ES module graph in static/js/ (no browser, no Node).

A browser reports a missing import only when the code path that needs it runs (a ReferenceError on, say,
deleting a location), and a wrong export breaks the whole page before anything renders. node --check sees
neither. These tests do, with plain-text rules that match how the modules are written: one
`import { … } from "./x.js";` per source module, one `export { … };` list at the end, top-level declarations
at the module's base indent.
"""
import json
import re

import pytest
from conftest import JS_DIR, JS_MODULES

STATIC = JS_DIR.parent
IMPORT_RE = re.compile(r'^import\s*\{([^}]*)\}\s*from\s*"\./([\w.-]+)";', re.M)
EXPORT_RE = re.compile(r"^export\s*\{([^}]*)\};", re.M)
DECL_RE = re.compile(r"^(?:async\s+)?(?:function\*?|const|let|var|class)\s+([\w$]+)")
LEAVES = ("core.js", "icons.js")


def _sources():
    return {name: (JS_DIR / name).read_text(encoding="utf-8") for name in JS_MODULES}


def _names(group):
    return [n.strip() for n in group.split(",") if n.strip()]


def imports_of(text):
    """{source module: [names]} for one module's import statements."""
    out = {}
    for names, target in IMPORT_RE.findall(text):
        out.setdefault(target, []).extend(_names(names))
    return out


def exports_of(text):
    return [n for group in EXPORT_RE.findall(text) for n in _names(group)]


def code_lines(text):
    """Lines that are code: no comment lines, no import/export statements."""
    text = IMPORT_RE.sub("", EXPORT_RE.sub("", text))
    return [line for line in text.split("\n") if not line.lstrip().startswith(("//", "/*", "*"))]


def top_level(text):
    """{name: kind} declared at the module's base indent (2 spaces for code cut from the old IIFE)."""
    lines = code_lines(text)
    decls = [(len(line) - len(line.lstrip()), line.strip()) for line in lines if DECL_RE.match(line.strip())]
    if not decls:
        return {}
    base = min(indent for indent, _ in decls)
    found = {}
    for indent, stripped in decls:
        if indent == base:
            kind = stripped.split()[0] if not stripped.startswith("async") else "function"
            found[DECL_RE.match(stripped).group(1)] = kind
    return found


def _uses(name, code):
    return re.search(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", code) is not None


def missing_imports(sources):
    """[(module, name, declaring module)] for every top-level name used in another module but not imported."""
    decls = {mod: top_level(text) for mod, text in sources.items()}
    problems = []
    for mod, text in sources.items():
        code = "\n".join(code_lines(text))
        imported = {(src, n) for src, names in imports_of(text).items() for n in names}
        for other, names in decls.items():
            if other == mod:
                continue
            for name in names:
                if name not in decls[mod] and _uses(name, code) and (other, name) not in imported:
                    problems.append((mod, name, other))
    return problems


def bad_imports(sources):
    """[(module, name, target)] for imports of a module that does not exist or does not export the name."""
    exported = {mod: set(exports_of(text)) for mod, text in sources.items()}
    return [(mod, name, target) for mod, text in sources.items()
            for target, names in imports_of(text).items() for name in names
            if name not in exported.get(target, set())]


def assigned_imports(sources):
    """[(module, name)] where a module assigns to a binding it imported (a TypeError at runtime)."""
    out = []
    for mod, text in sources.items():
        code = "\n".join(code_lines(text))
        for names in imports_of(text).values():
            for n in names:
                e = re.escape(n)
                if re.search(rf"(?<![\w$.]){e}\s*(?:=(?![=>])|\+\+|--|[-+*/%|&^]=)|(?:\+\+|--){e}(?![\w$])", code):
                    out.append((mod, n))
    return out


# ------------------------------------------------------------------------------------------------ the real modules

def test_module_set_matches_conftest():
    assert sorted(p.name for p in JS_DIR.glob("*.js")) == sorted(JS_MODULES)


def test_js_package_json_marks_modules():
    # Without it, node --check on a .js file with `import` exits 0 even when the file has a syntax error.
    assert json.loads((JS_DIR / "package.json").read_text()) == {"type": "module"}


def test_index_loads_main_as_module():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="/static/js/main.js"></script>' in html
    assert "app.js" not in html and not (STATIC / "app.js").exists()


def test_top_level_names_are_unique():
    seen = {}
    for mod, text in _sources().items():
        for name in top_level(text):
            assert name not in seen, f"{name} is declared in both {seen[name]} and {mod}"
            seen[name] = mod


def test_imports_match_exports():
    assert bad_imports(_sources()) == []


def test_every_cross_module_reference_is_imported():
    problems = missing_imports(_sources())
    assert problems == [], "\n".join(f"{m} uses {n} from {o} without importing it" for m, n, o in problems)


def test_no_module_assigns_an_imported_binding():
    assert assigned_imports(_sources()) == []


def test_core_and_icons_are_leaves():
    # router.js reads core.js's isAdmin while the modules evaluate (const mePromise = isAdmin ? …). That is only
    # safe because core.js imports nothing, so it always finishes evaluating before any module that imports it.
    src = _sources()
    for leaf in LEAVES:
        assert imports_of(src[leaf]) == {}, f"{leaf} must not import other modules"


def test_every_module_reachable_from_main():
    src, seen, todo = _sources(), set(), ["main.js"]
    while todo:
        mod = todo.pop()
        if mod not in seen:
            seen.add(mod)
            todo.extend(imports_of(src[mod]))
    assert seen == set(JS_MODULES)


def test_every_export_is_imported_somewhere():
    src = _sources()
    used = {n for text in src.values() for names in imports_of(text).values() for n in names}
    for mod, text in src.items():
        assert set(exports_of(text)) <= used, f"{mod} exports names nothing imports"


# -------------------------------------------------------------------- the checks catch what they are meant to

@pytest.fixture
def src():
    return _sources()


def test_check_catches_a_dropped_import(src):
    src["locations.js"] = src["locations.js"].replace("import { closeModal } from \"./forms.js\";\n", "")
    assert ("locations.js", "closeModal", "forms.js") in missing_imports(src)


def test_check_catches_a_dropped_export(src):
    head, tail = src["forms.js"].split("export {")
    src["forms.js"] = head + "export {" + tail.replace("closeModal, ", "")
    assert ("router.js", "closeModal", "forms.js") in bad_imports(src)


def test_check_catches_an_import_from_a_missing_module(src):
    src["about.js"] = 'import { state } from "./kore.js";\n' + src["about.js"].split("\n", 2)[2]
    assert ("about.js", "state", "kore.js") in bad_imports(src)


def test_check_catches_assigning_an_imported_binding(src):
    src["grid.js"] += "\n  function oops() { state = {}; }\n"
    assert ("grid.js", "state") in assigned_imports(src)


def test_check_ignores_comparisons_and_property_writes(src):
    src["grid.js"] += "\n  function fine() { return state == null || state.x === 1; }\n"
    assert assigned_imports(src) == []
