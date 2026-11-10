# LIFESPAN: permanent (the repo's own mutation check, run by hand and by CI on the pure modules)
"""Mutation check: remove or flip one guard at a time and require a failing test for each.

Two kinds of mutant, both written into a COPY of the repository so the working tree is never touched:

1. Mutants derived from the syntax tree of each Python module, so a renamed variable or a new branch changes the mutant set instead of breaking a hand written table:
   comparison operators flipped, `and` and `or` swapped, a `not` removed, an if / while / assert / conditional test negated, an `if` that ends in raise / return / continue / break
   replaced by `pass` (the guard removed), a call statement replaced by `pass`, `return x` turned into `return None`, integer constants moved by one, True and False swapped,
   + and - swapped, and upper case rule codes (EXACT_KEY, UNIT_CONFLICT ...) emptied.
2. Named guards: one hand written text change per guard the project depends on, including the ones that live in SQL (the row level security policy, FORCE, the column grants)
   and the ones a syntax tree cannot name (the unit rule, the distance rule, the threshold, email lower casing, phone normalisation, the rerun guards, the reconcile orphan check).

A mutant is KILLED when pytest fails (or hangs past the timeout) and SURVIVED when the suite stays green. A survivor means a line no test pins: strengthen a test, do not delete the
mutant. A line that cannot matter is marked `# no-mutate: <why>` in the source. A guard whose text is no longer in the file is an error, never a pass.

    python tests/mutation_check.py --module normalise              all syntax tree mutants of parcelkey/normalise.py
    python tests/mutation_check.py --guards                        every named guard
    python tests/mutation_check.py --list --module match           show the mutants without running them
    python tests/mutation_check.py --summary [--write docs/mutation-results.txt]

Stage 1 runs the tests that cover the module; a survivor is run again against the whole suite before it is reported. Database tests need PostgreSQL (`python tools/pg.py up`);
each worker gets its own database (pk_mut_...) on the same server so workers do not trample each other.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / ".mutation" / "results.json"
COPY = ("parcelkey", "scripts", "tools", "tests", "docs", "brand", "pytest.ini", "LICENSE", "pyproject.toml", "requirements.txt", "README.md")

# module -> (pytest arguments for stage 1, timeout seconds, needs the database)
TARGETS = {
    "normalise": (["tests/test_normalise.py", "tests/test_match_unit.py"], 120, False),
    "match": (["tests/test_match_unit.py", "tests/test_pipeline.py"], 300, True),
    "master": (["tests/test_pipeline.py", "tests/test_reconcile.py"], 300, True),
    "block": (["tests/test_pipeline.py"], 300, True),
    "contacts": (["tests/test_contacts.py"], 300, True),
    "reconcile": (["tests/test_reconcile.py"], 300, True),
    "freshness": (["tests/test_freshness.py"], 300, True),
    "ingest": (["tests/test_ingest.py"], 300, True),
    "records": (["tests/test_ingest.py", "tests/test_normalise.py"], 300, True),
    "evaluate": (["tests/test_evaluate.py"], 300, True),
    "gnaf": (["tests/test_gnaf.py"], 300, True),
    "quality": (["tests/test_quality_cli.py", "-k", "quality"], 300, True),
    "db": (["tests/test_db.py", "tests/test_tenant.py", "tests/test_ingest.py"], 300, True),
}
FILES = {name: f"parcelkey/{name}.py" for name in TARGETS}
DEFAULT_MODULES = tuple(TARGETS)

CMP_SWAP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.LtE: ast.Gt, ast.Gt: ast.LtE, ast.GtE: ast.Lt, ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In}
ARITH_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add}
CODE = re.compile(r"[A-Z][A-Z_]{3,}")
EXITS = (ast.Raise, ast.Return, ast.Continue, ast.Break)

# name -> (file, exact text to find, replacement, pytest arguments that must fail)
GUARDS = {
    "rls_policy_always_true": ("parcelkey/migrations/003_rls.sql", "USING (agency_id = current_setting(''app.agency_id'', true)) WITH CHECK (agency_id = current_setting(''app.agency_id'', true))",
                               "USING (true) WITH CHECK (true)", ["tests/test_tenant.py"]),
    "rls_policy_read_side_open": ("parcelkey/migrations/003_rls.sql", "USING (agency_id = current_setting(''app.agency_id'', true)) WITH CHECK", "USING (true) WITH CHECK", ["tests/test_tenant.py"]),
    "rls_policy_write_side_open": ("parcelkey/migrations/003_rls.sql", "WITH CHECK (agency_id = current_setting(''app.agency_id'', true))", "WITH CHECK (true)", ["tests/test_tenant.py"]),
    "rls_policy_matches_other_agencies": ("parcelkey/migrations/003_rls.sql", "USING (agency_id = current_setting", "USING (agency_id <> current_setting", ["tests/test_tenant.py"]),
    "rls_not_forced": ("parcelkey/migrations/003_rls.sql", "FORCE ROW LEVEL SECURITY", "NO FORCE ROW LEVEL SECURITY", ["tests/test_tenant.py"]),
    "rls_not_enabled": ("parcelkey/migrations/003_rls.sql", "ENABLE ROW LEVEL SECURITY", "DISABLE ROW LEVEL SECURITY", ["tests/test_tenant.py"]),
    "rls_missing_on_one_table": ("parcelkey/migrations/003_rls.sql", "'contact_link', 'contact_merge'", "'contact_merge'", ["tests/test_tenant.py"]),
    "raw_row_updatable": ("parcelkey/db.py", "GRANT UPDATE ({}) ON source_record TO {}", "GRANT UPDATE ON source_record TO {}", ["tests/test_ingest.py"]),
    "raw_update_never_revoked": ("parcelkey/db.py", 'conn.execute(sql.SQL("REVOKE UPDATE ON source_record FROM {}").format(app))', "pass", ["tests/test_ingest.py"]),
    "reference_writable_by_app": ("parcelkey/db.py", 'conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(t), app))\n        for t in MASTER_TABLES',
                                  'conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(sql.Identifier(t), app))\n        for t in MASTER_TABLES', ["tests/test_tenant.py"]),
    "master_property_editable_by_app": ("parcelkey/db.py", 'conn.execute(sql.SQL("GRANT SELECT, INSERT ON {} TO {}").format(sql.Identifier(t), app))',
                                        'conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(sql.Identifier(t), app))', ["tests/test_tenant.py"]),
    "unsafe_role_accepted": ("parcelkey/db.py", "if row is None or row[1] or row[2]:", "if row is None:", ["tests/test_tenant.py"]),
    "unit_rule_removed": ("parcelkey/match.py", "    if a.unit != b.unit:\n        return True\n", "    if False:\n        return True\n", ["tests/test_match_unit.py"]),
    "level_rule_removed": ("parcelkey/match.py", "    return bool(a.level and b.level and a.level != b.level)", "    return False", ["tests/test_match_unit.py"]),
    "unit_rule_missing_in_the_lot_free_path": ("parcelkey/match.py", "compatible = [c for c in same_number if not units_conflict(rec, c.addr)]", "compatible = list(same_number)", ["tests/test_match_unit.py"]),
    "distance_rule_removed": ("parcelkey/match.py", "c.dist_m is not None and c.dist_m <= cfg.lot_distance_m", "True", ["tests/test_match_unit.py"]),
    "distance_limit_off_by_inclusive": ("parcelkey/match.py", "c.dist_m <= cfg.lot_distance_m", "c.dist_m < cfg.lot_distance_m", ["tests/test_match_unit.py"]),
    "lot_without_geocode_links": ("parcelkey/match.py", "        if lat is None or lon is None:\n            return _set_aside(\"LOT_NO_GEOCODE\"", "        if False:\n            return _set_aside(\"LOT_NO_GEOCODE\"", ["tests/test_match_unit.py"]),
    "threshold_ignored": ("parcelkey/match.py", "if top.sim >= cfg.threshold:", "if True:", ["tests/test_match_unit.py"]),
    "threshold_exclusive": ("parcelkey/match.py", "if top.sim >= cfg.threshold:", "if top.sim > cfg.threshold:", ["tests/test_match_unit.py"]),
    "tie_margin_ignored": ("parcelkey/match.py", "            if len(tied) > 1:\n                return _set_aside(\"AMBIGUOUS_TIE\", {\"candidates\": _cand_detail(tied)})",
                           "            if False:\n                return _set_aside(\"AMBIGUOUS_TIE\", {\"candidates\": _cand_detail(tied)})", ["tests/test_match_unit.py"]),
    "transposition_guard_removed": ("parcelkey/match.py", "    if swapped:\n", "    if False:\n", ["tests/test_match_unit.py"]),
    "email_not_lower_cased": ("parcelkey/normalise.py", "    lowered = text.lower()", "    lowered = text", ["tests/test_normalise.py"]),
    "phone_trunk_zero_kept": ("parcelkey/normalise.py", '    if digits.startswith("0"):\n        digits = digits[1:]', '    if False:\n        digits = digits[1:]', ["tests/test_normalise.py"]),
    "phone_country_code_kept": ("parcelkey/normalise.py", 'elif digits.startswith("61") and (had_plus or len(digits) >= 11):', "elif False:", ["tests/test_normalise.py"]),
    "phone_any_digits_accepted": ("parcelkey/normalise.py", 'if re.fullmatch(r"4\\d{8}|[2378]\\d{8}", digits):', "if digits:", ["tests/test_normalise.py"]),
    "canonical_key_includes_postcode": ("parcelkey/normalise.py", "self.street_key, self.locality, self.state))", "self.street_key, self.locality, self.state, self.postcode))", ["tests/test_normalise.py"]),
    "st_never_saint": ("parcelkey/normalise.py", '        elif w == "ST" and (saint_everywhere or i < len(words) - 1):', "        elif False:", ["tests/test_normalise.py"]),
    "rerun_decides_linked_records_again": ("parcelkey/match.py", "             AND NOT EXISTS (SELECT 1 FROM property_link l WHERE l.agency_id = r.agency_id AND l.record_id = r.record_id)\n", "", ["tests/test_pipeline.py"]),
    "quarantine_rewritten_on_every_run": ("parcelkey/match.py", "           WHERE (quarantine.reason_code, quarantine.detail) IS DISTINCT FROM (EXCLUDED.reason_code, EXCLUDED.detail)\"\"\"", "\"\"\"", ["tests/test_pipeline.py"]),
    "quarantine_never_resolved": ("parcelkey/match.py", "UPDATE quarantine SET status = 'resolved', resolved_at = now() WHERE agency_id = %s AND record_id = %s AND status = 'open'", "SELECT 1 WHERE %s <> %s", ["tests/test_pipeline.py"]),
    "ingest_same_file_loaded_twice": ("parcelkey/ingest.py", "    if cur.execute(\"SELECT 1 FROM source_load WHERE agency_id = %s AND source = %s AND file_sha256 = %s\", (agency, source, sha)).fetchone():\n        return IngestResult(source, Path(path).name, \"skipped\")",
                                      "    if False:\n        return IngestResult(source, Path(path).name, \"skipped\")", ["tests/test_ingest.py"]),
    "reconcile_orphan_check_removed": ("parcelkey/reconcile.py", "    if orphan_link_record or orphan_link_property or orphan_q:", "    if False:", ["tests/test_reconcile.py"]),
    "reconcile_accounting_check_removed": ("parcelkey/reconcile.py", "    if neither:", "    if False:", ["tests/test_reconcile.py"]),
    "reconcile_one_id_check_removed": ("parcelkey/reconcile.py", "        if expected != property_id:", "        if False:", ["tests/test_reconcile.py"]),
    "contact_cap_removed": ("parcelkey/contacts.py", "if _cluster_size(cur, agency, ids) + 1 > max_cluster:", "if False:", ["tests/test_contacts.py"]),
    "contact_merge_on_name": ("parcelkey/contacts.py", "        idents = [(k, v) for k, v in ((\"email\", email), (\"phone\", phone)) if v]", "        idents = [(k, v) for k, v in ((\"email\", email), (\"phone\", phone), (\"name\", name)) if v]", ["tests/test_contacts.py"]),
    "freshness_never_stale": ("parcelkey/freshness.py", '"STALE" if age > max_age[source] else "FRESH"', '"FRESH"', ["tests/test_freshness.py"]),
    "freshness_missing_is_fine": ("parcelkey/freshness.py", 'return 1 if any(r.status != "FRESH" for r in rows) else 0', 'return 1 if any(r.status == "STALE" for r in rows) else 0', ["tests/test_freshness.py"]),
}


def _skipped_lines(src: str) -> set[int]:
    return {i for i, ln in enumerate(src.splitlines(), 1) if re.search(r"#\s*no-mutate", ln)}


def _protected(tree: ast.AST) -> set[int]:
    """ids of nodes that must not be mutated: docstrings, f-string parts, annotations, dict keys and subscript keys, __all__, the main guard."""
    out: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant):
            out.add(id(n.body[0].value))
        if isinstance(n, ast.JoinedStr):
            out.update(id(v) for v in ast.walk(n))
        if isinstance(n, ast.arg) and n.annotation is not None:
            out.update(id(v) for v in ast.walk(n.annotation))
        if isinstance(n, ast.FunctionDef) and n.returns is not None:
            out.update(id(v) for v in ast.walk(n.returns))
        if isinstance(n, ast.AnnAssign):
            out.update(id(v) for v in ast.walk(n.annotation))
        if isinstance(n, ast.Dict):
            out.update(id(k) for k in n.keys if k is not None)
        if isinstance(n, ast.Subscript):
            out.update(id(v) for v in ast.walk(n.slice))
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__":
            out.update(id(v) for v in ast.walk(n))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "field":
            out.update(id(v) for v in ast.walk(n))
    return out


def preorder(node: ast.AST):
    """Nodes in the order NodeTransformer visits them, so a site index found here names the same node when the mutation is applied."""
    yield node
    for child in ast.iter_child_nodes(node):
        yield from preorder(child)


def sites(src: str) -> list[tuple[int, str, int]]:
    """(walk index, kind, line) for every mutable site, in a stable order."""
    tree = ast.parse(src)
    skip, prot = _skipped_lines(src), _protected(tree)
    out: list[tuple[int, str, int]] = []
    for idx, n in enumerate(preorder(tree)):
        line = getattr(n, "lineno", 0)
        if line in skip or id(n) in prot:
            continue
        if isinstance(n, ast.Compare):
            out += [(idx, f"cmp{k}", line) for k, op in enumerate(n.ops) if type(op) in CMP_SWAP]
        elif isinstance(n, ast.BoolOp):
            out.append((idx, "boolop", line))
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            out.append((idx, "not", line))
        elif isinstance(n, (ast.If, ast.While)):
            out.append((idx, "negate", line))
            if isinstance(n, ast.If) and not n.orelse and n.body and isinstance(n.body[-1], EXITS):
                out.append((idx, "drop_guard", line))
        elif isinstance(n, (ast.Assert, ast.IfExp)):
            out.append((idx, "negate", line))
        elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call):
            out.append((idx, "drop_call", line))
        elif isinstance(n, ast.Return) and n.value is not None and not (isinstance(n.value, ast.Constant) and n.value.value is None):
            out.append((idx, "ret_none", line))
        elif isinstance(n, ast.Constant):
            if isinstance(n.value, bool):
                out.append((idx, "bool", line))
            elif isinstance(n.value, int):
                out.append((idx, "int", line))
            elif isinstance(n.value, str) and CODE.fullmatch(n.value):
                out.append((idx, "code", line))
        elif isinstance(n, ast.BinOp) and type(n.op) in ARITH_SWAP:
            out.append((idx, "arith", line))
    return out


class _Apply(ast.NodeTransformer):
    def __init__(self, target_idx: int, kind: str) -> None:
        self.target, self.kind, self.i = target_idx, kind, -1

    def visit(self, node):
        self.i += 1
        here = self.i
        node = self.generic_visit(node)
        if here != self.target:
            return node
        k = self.kind
        if k.startswith("cmp"):
            pos = int(k[3:])
            node.ops[pos] = CMP_SWAP[type(node.ops[pos])]()
        elif k == "boolop":
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        elif k == "not":
            return node.operand
        elif k == "negate":
            if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
                node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
        elif k == "drop_guard" or k == "drop_call":
            return ast.copy_location(ast.Pass(), node)
        elif k == "ret_none":
            node.value = ast.Constant(value=None)
        elif k == "bool":
            node.value = not node.value
        elif k == "int":
            node.value = node.value + 1
        elif k == "code":
            node.value = ""
        elif k == "arith":
            node.op = ARITH_SWAP[type(node.op)]()
        return node


def mutate(src: str, idx: int, kind: str) -> str:
    tree = ast.parse(src)
    new = _Apply(idx, kind).visit(copy.deepcopy(tree))
    ast.fix_missing_locations(new)
    return ast.unparse(new)


def mutants(module: str) -> list[dict]:
    src = (ROOT / FILES[module]).read_text(encoding="utf-8")
    base = ast.unparse(ast.parse(src))
    out = []
    for idx, kind, line in sites(src):
        if mutate(src, idx, kind) == base:
            continue                                                 # a mutant identical to the original is no mutant
        out.append({"id": f"{module}:{line}:{kind}#{idx}", "module": module, "idx": idx, "kind": kind, "line": line})
    return out


# ---- running ------------------------------------------------------------------------------------------------------------------------------------

def _names(worker: int, label: str) -> str:
    return f"pk_mut_{re.sub('[^a-z0-9]', '_', label.lower())}_{worker}"


def _worker_env(name: str) -> dict[str, str]:
    from parcelkey import db
    return {"PARCELKEY_ADMIN_DSN": db.retarget(db.default_admin_dsn(), dbname=name), "PARCELKEY_DSN": db.retarget(db.default_dsn(), dbname=name),
            **{v: db.password(v) for v in ("PARCELKEY_ADMIN_PASSWORD", "PARCELKEY_OWNER_PASSWORD", "PARCELKEY_APP_PASSWORD")}}


def _prepare_db(name: str) -> None:
    import psycopg
    from parcelkey import db
    with psycopg.connect(db.default_admin_dsn(), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')
    db.init_db(db.retarget(db.default_admin_dsn(), dbname=name))


def _drop_db(name: str) -> None:
    import psycopg
    from parcelkey import db
    with psycopg.connect(db.default_admin_dsn(), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _copy_repo(dest: Path) -> None:
    for name in COPY:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".hypothesis", "out"))
        elif src.exists():
            shutil.copy2(src, dest / name)


def _pytest_run(work: Path, args: list[str], timeout: int, env: dict[str, str]) -> tuple[bool, str]:
    """(tests passed, the tail of what pytest printed). A hang past the timeout counts as a failure."""
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *args], cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout} s"
    return p.returncode == 0, (p.stdout + p.stderr)[-1500:]


def _run_batch(worker: int, label: str, batch: list[dict], full_suite: bool, args: list[str], timeout: int, needs_db: bool) -> list[dict]:
    """Each item of the batch has either idx and kind (syntax tree mutant of item['file']) or find and replace (named guard)."""
    name = _names(worker, label)
    env = {**os.environ, "PYTHONHASHSEED": "0", **(_worker_env(name) if needs_db or full_suite else {})}
    results: list[dict] = []
    work = Path(tempfile.mkdtemp(prefix=f"pk_mut_{worker}_"))
    try:
        _copy_repo(work)
        ok, tail = _pytest_run(work, args, timeout, env)
        assert ok, f"stage 1 tests are not green on the unmutated {label}:\n{tail}"
        for m in batch:
            target = work / m["file"]
            original = target.read_text(encoding="utf-8")
            if "find" in m:
                assert m["find"] in original, f"guard {m['id']}: the text to change is not in {m['file']}"
                target.write_text(original.replace(m["find"], m["replace"], 1), encoding="utf-8")
            else:
                target.write_text(mutate(original, m["idx"], m["kind"]), encoding="utf-8")
            passed, _ = _pytest_run(work, m.get("args", args), timeout, env)
            if passed and full_suite:
                passed, _ = _pytest_run(work, [], 1500, env)
            target.write_text(original, encoding="utf-8")
            results.append({**m, "status": "SURVIVED" if passed else "KILLED"})
            print(f"  {results[-1]['status']:8} {m['id']}", flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if needs_db or full_suite:
            _drop_db(name)
    return results


def _load() -> dict:
    return json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}


def _save(data: dict) -> None:
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def summary(write: Path | None = None) -> int:
    data = _load()
    if not data:
        print("no recorded results; run with --module or --guards first")
        return 1
    lines = ["Mutation check results", "Command: python tests/mutation_check.py --module <name>   and   python tests/mutation_check.py --guards   then   --summary",
             "Each mutant changes one guard or value in a copy of the code; a mutant counts as killed when at least one test fails.", ""]
    total = killed = 0
    survivors = []
    for module in sorted(data):
        rs = list(data[module].values())
        k = sum(1 for r in rs if r["status"] == "KILLED")
        total, killed = total + len(rs), killed + k
        survivors += [r for r in rs if r["status"] != "KILLED"]
        label = "named guards" if module == "guards" else module
        lines.append(f"{label:14} {len(rs):4} mutants  {k:4} killed  {len(rs) - k:3} survived")
    lines.append(f"{'total':14} {total:4} mutants  {killed:4} killed  {total - killed:3} survived")
    for s in survivors:
        lines.append(f"  SURVIVOR {s['id']}")
    stale = [m for m in FILES if m in data and {x["id"] for x in mutants(m)} != set(data[m])]
    if stale:
        lines.append(f"results are out of date for: {', '.join(stale)} (the source changed since they were recorded; rerun)")
    if "guards" in data and set(data["guards"]) != {f"guard:{n}" for n in GUARDS}:
        lines.append("results are out of date for: guards (the guard list changed; rerun)")
        stale.append("guards")
    print("\n".join(lines))
    if write:
        write.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if survivors or stale else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", choices=sorted(FILES))
    ap.add_argument("--guards", action="store_true", help="run the named guards")
    ap.add_argument("--part", help="i/n: run the i-th of n slices of the mutants")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-full-suite", action="store_true", help="skip the whole-suite rerun of survivors")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--write", help="with --summary, also write the text to this file")
    ap.add_argument("--ids", help="run only these mutant ids (comma separated) and merge the outcome into the recorded results")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    if a.summary:
        return summary(Path(a.write) if a.write else None)
    if a.guards:
        label, ms = "guards", [{"id": f"guard:{n}", "file": f, "find": find, "replace": rep, "args": args, "kind": "guard", "line": 0} for n, (f, find, rep, args) in GUARDS.items()]
        args, timeout, needs_db = ["tests/test_normalise.py"], 300, True
        gone = [n for n, (f, find, _, _) in GUARDS.items() if find not in (ROOT / f).read_text(encoding="utf-8")]
        if gone:
            print(f"these guards name text that is not in the file any more: {', '.join(gone)}", file=sys.stderr)
            return 2
    elif a.module:
        label = a.module
        args, timeout, needs_db = TARGETS[a.module]
        ms = [{**m, "file": FILES[a.module]} for m in mutants(a.module)]
    else:
        ap.error("--module or --guards is required")
    if a.ids:
        wanted = set(a.ids.split(","))
        ms = [m for m in ms if m["id"] in wanted]
    if a.part:
        i, n = (int(x) for x in a.part.split("/"))
        ms = [m for k, m in enumerate(ms) if k % n == i - 1]
    if a.list:
        for m in ms:
            print(m["id"])
        print(f"{len(ms)} mutants")
        return 0
    print(f"{label}: {len(ms)} mutants, {a.jobs} workers")
    batches = [ms[w::a.jobs] for w in range(a.jobs)]
    for w, b in enumerate(batches):
        if b and (needs_db or not a.no_full_suite):
            _prepare_db(_names(w, label))                           # one at a time: two inits at once race on the role settings
    results: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = [ex.submit(_run_batch, w, label, b, not a.no_full_suite, args, timeout, needs_db) for w, b in enumerate(batches) if b]
        for f in futs:
            results += f.result()
    data = _load()
    current = {m["id"] for m in (mutants(label) if label != "guards" else [])} if label != "guards" else {f"guard:{n}" for n in GUARDS}
    rec = {k: v for k, v in data.get(label, {}).items() if k in current}          # drop what an older version of the source recorded
    data[label] = rec
    for r in results:
        rec[r["id"]] = {k: r[k] for k in ("id", "kind", "line", "status")}
    _save(data)
    survived = [r for r in results if r["status"] != "KILLED"]
    print(f"{label}: {len(results)} run, {len(results) - len(survived)} killed, {len(survived)} survived")
    for r in survived:
        if "file" in r and "find" not in r:
            src = (ROOT / r["file"]).read_text(encoding="utf-8").splitlines()
            print(f"  SURVIVED {r['id']}: {src[r['line'] - 1].strip()[:110]}")
        else:
            print(f"  SURVIVED {r['id']}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
