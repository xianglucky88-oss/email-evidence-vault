#!/usr/bin/env python3
"""Read-only audit harness for the email evidence index.

This tool never changes index rules or evidence. It:

* runs the documented sample cases through the current pipeline;
* compares results with the frozen baseline in docs/samples/baseline/v0.1.0;
* derives attachment content hashes independently of the engine;
* classifies a candidate rule change as HASH / THREAD / HOLD / ENVELOPE /
  IDENTICAL, and fails when only the rule fingerprint changed.

See docs/evidence-index-maintenance-manual.md and docs/index-change-register.md.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
DEFAULT_CASES = ROOT / "docs" / "samples" / "cases"
DEFAULT_BASELINE = ROOT / "docs" / "samples" / "baseline" / "v0.1.0"
MANIFEST_SCHEMA = "evidence-index-manifest/v1"
RUN_SCHEMA = "evidence-index-run/v1"
RUN_SIDECAR = "run-meta.json"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from email_evidence_vault.pipeline import run as run_pipeline  # noqa: E402


class AuditError(Exception):
    """An input or harness problem that prevents a trustworthy comparison."""


def canonical(value):
    """Canonical JSON used for every fingerprint and equality check."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha_hex(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read JSON from {path}: {exc}")


def case_files(cases_dir):
    if not cases_dir.is_dir():
        raise AuditError(f"cases directory does not exist: {cases_dir}")
    files = sorted(cases_dir.glob("*.json"))
    if not files:
        raise AuditError(f"no case files found in {cases_dir}")
    return files


def expected_for(case_path, baseline_dir):
    return baseline_dir / f"{case_path.stem}.expected.json"


def result_for_case(case_path):
    payload = load_json(case_path)
    try:
        result = run_pipeline(payload)
    except Exception as exc:  # the current pipeline exposes raw exceptions
        raise AuditError(
            f"pipeline failed for {case_path.name}: {type(exc).__name__}: {exc}"
        )
    return payload, result


def attachment_digests(payload):
    """Independent oracle for the documented attachment addressing rule.

    SHA-256 over the UTF-8 bytes of ``content``; no Unicode normalization,
    no MIME decoding, no filename participation. Mirrors only the public
    documented rule, not engine internals.
    """
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        raise AuditError("messages must be a list")
    digests = []
    for index, row in enumerate(messages):
        if not isinstance(row, dict):  # parser.load_records drops these
            continue
        attachments = row.get("attachments", [])
        if attachments is None:
            attachments = []
        if not isinstance(attachments, list):
            raise AuditError(f"messages[{index}].attachments must be a list")
        for att_index, attachment in enumerate(attachments):
            where = f"messages[{index}].attachments[{att_index}]"
            if not isinstance(attachment, dict):
                raise AuditError(f"{where} must be an object")
            if "content" not in attachment:
                raise AuditError(f"{where}.content is required")
            content = attachment["content"]
            if not isinstance(content, str):
                raise AuditError(f"{where}.content must be a string")
            digests.append(hashlib.sha256(content.encode("utf-8")).hexdigest())
    return sorted(digests)


def message_rows(payload):
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        raise AuditError("messages must be a list")
    return [row for row in messages if isinstance(row, dict)]


def message_id(row, index=0):
    if "id" not in row:
        raise AuditError(f"messages[{index}] has no id")
    return row["id"]


def last_ref(row):
    """The reference the current engine treats as the parent.

    A list uses its final element; a bare string is indexed by position, so
    its final character is used. Empty/missing refs mean self-parenting.
    """
    refs = row.get("refs")
    if isinstance(refs, list) and refs:
        return refs[-1]
    if isinstance(refs, str) and refs:
        return refs[-1]
    return None


def all_refs(row):
    refs = row.get("refs")
    if isinstance(refs, list):
        return list(refs)
    if isinstance(refs, str) and refs:
        return [refs[-1]]
    return []


def parent_graph(payload):
    graph = {}
    for index, row in enumerate(message_rows(payload)):
        key = message_id(row, index)
        ref = last_ref(row)
        graph[key] = key if ref is None else ref
    return graph


def cycle_ids(payload):
    """Every id that sits on a directed cycle of the parent graph."""
    graph = parent_graph(payload)
    done = {}
    on_cycle = set()
    for start in graph:
        path = []
        seen = {}
        current = start
        while True:
            if current in done:
                break
            if current in seen:
                on_cycle.update(path[seen[current]:])
                break
            seen[current] = len(path)
            path.append(current)
            nxt = graph.get(current)
            if nxt is None or nxt == current:
                break
            current = nxt
        for node in path:
            done[node] = True
    return sorted(on_cycle, key=canonical)


def self_reference_ids(payload):
    """Ids whose own explicit refs point back at themselves (not plain roots)."""
    explicit = {}
    for index, row in enumerate(message_rows(payload)):
        key = message_id(row, index)
        if last_ref(row) is not None:
            explicit[key] = last_ref(row)
    return sorted(
        (key for key, value in explicit.items() if key == value), key=canonical
    )


def order_sensitive(payload):
    """Does reversing input order change the index? ``None`` means untestable."""
    rows = message_rows(payload)
    holds = payload.get("holds", [])
    forward = {"messages": [dict(row) for row in rows], "holds": holds}
    backward = {"messages": [dict(row) for row in reversed(rows)], "holds": holds}
    try:
        return canonical(run_pipeline(forward)) != canonical(run_pipeline(backward))
    except Exception:
        return None


def anomalies(payload, result):
    rows = message_rows(payload)
    ids = [message_id(row, index) for index, row in enumerate(rows)]
    id_keys = {canonical(item) for item in ids}
    by_key = {}
    for item in ids:
        by_key.setdefault(canonical(item), item)
    counts = {}
    for key in by_key:
        counts[key] = sum(1 for item in ids if canonical(item) == key)
    duplicates = sorted(
        (value for key, value in by_key.items() if counts[key] > 1), key=canonical
    )

    missing_targets = {}
    for row in rows:
        for ref in all_refs(row):
            if canonical(ref) not in id_keys:
                missing_targets[canonical(ref)] = ref
    missing = sorted(missing_targets.values(), key=canonical)

    threads = result.get("threads", {})
    if not isinstance(threads, dict):
        raise AuditError("threads must be an object")
    phantom_roots = sorted(
        (root for root in threads if canonical(root) not in id_keys), key=canonical
    )

    holds = payload.get("holds", [])
    if holds is None:
        holds = []
    if isinstance(holds, str):
        holds = [holds]
    if not isinstance(holds, list):
        raise AuditError("holds must be a list")
    unmatched = {}
    for item in holds:
        if canonical(item) not in id_keys:
            unmatched[canonical(item)] = item
    holds_not_matched = sorted(unmatched.values(), key=canonical)

    names_by_digest = {}
    digests_by_name = {}
    display_name = {}
    for row in rows:
        attachments = row.get("attachments") or []
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if not isinstance(attachment, dict) or "content" not in attachment:
                continue
            content = attachment["content"]
            if not isinstance(content, str):
                continue
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            name = attachment.get("name")
            name_key = canonical(name)
            names_by_digest.setdefault(digest, {})[name_key] = name
            digests_by_name.setdefault(name_key, {})[digest] = digest
            display_name.setdefault(name_key, name)
    conflicts = {
        digest: [names[key] for key in sorted(names)]
        for digest, names in names_by_digest.items()
        if len(names) > 1
    }
    name_reuse = {
        display_name[name_key]: sorted(digests)
        for name_key, digests in digests_by_name.items()
        if len(digests) > 1
    }

    ignored_fields = sorted(
        {key for row in rows for key in row if key not in ("id", "refs", "attachments")}
    )
    return {
        "duplicate_ids": duplicates,
        "missing_ref_targets": missing,
        "phantom_roots": phantom_roots,
        "cycle_ids": cycle_ids(payload),
        "self_reference_ids": self_reference_ids(payload),
        "holds_not_matched": holds_not_matched,
        "attachment_digest_name_conflicts": conflicts,
        "attachment_name_digest_reuse": name_reuse,
        "ignored_message_fields": ignored_fields,
        "order_sensitive": order_sensitive(payload),
    }




def thread_fingerprint(result):
    threads = result.get("threads", {})
    if not isinstance(threads, dict):
        raise AuditError("threads must be an object")
    items = []
    for root, members in threads.items():
        if not isinstance(members, list):
            raise AuditError(f"thread {root!r} must contain a list")
        items.append([str(root), sorted(canonical(member) for member in members)])
    items.sort(key=lambda item: item[0])
    return sha_hex(canonical(items))


def hold_fingerprint(result):
    held = result.get("held_messages", [])
    if not isinstance(held, list):
        raise AuditError("held_messages must be a list")
    return sha_hex(canonical(sorted((canonical(item) for item in held))))


def output_fingerprint(result):
    return sha_hex(canonical(result))


def rule_fingerprint():
    """SHA-256 over the indexing source files, sorted by package-relative path.

    The sample outputs deliberately expose only counts and thread membership,
    so a change such as sha256 -> sha1 that preserves dedup membership is
    invisible in the output. This fingerprint is the guard for that class.
    """
    package = SRC / "email_evidence_vault"
    if not package.is_dir():
        raise AuditError(f"package directory does not exist: {package}")
    parts = []
    for path in sorted(package.glob("*.py")):
        parts.append(path.name)
        parts.append(hashlib.sha256(path.read_bytes()).hexdigest())
    if not parts:
        raise AuditError(f"no source files found in {package}")
    return sha_hex("\n".join(parts))


def case_summary(payload, result, case_path):
    digests = attachment_digests(payload)
    unique_digests = sorted(set(digests))
    engine_count = result.get("attachment_count")
    summary = {
        "input_sha256": sha_hex(case_path.read_bytes()),
        "output_sha256": output_fingerprint(result),
        "thread_fingerprint": thread_fingerprint(result),
        "attachment_fingerprint": sha_hex("\n".join(unique_digests)),
        "hold_fingerprint": hold_fingerprint(result),
        "attachment_digests": unique_digests,
        "attachment_count": engine_count,
        "oracle_attachment_count": len(unique_digests),
        "thread_count": len(result.get("threads", {})),
        "held_count": len(result.get("held_messages", [])),
        "count_consistent": engine_count == len(unique_digests),
        "anomalies": anomalies(payload, result),
    }
    return summary


def build_manifest(cases_dir, label):
    cases = {}
    for case_path in case_files(cases_dir):
        payload, result = result_for_case(case_path)
        cases[case_path.stem] = case_summary(payload, result, case_path)
    return {
        "schema": MANIFEST_SCHEMA,
        "baseline": label,
        "rules_sha256": rule_fingerprint(),
        "cases": cases,
    }


def load_result_dir(directory):
    if not directory.is_dir():
        raise AuditError(f"result directory does not exist: {directory}")
    results = {}
    for path in sorted(directory.glob("*.json")):
        if path.name in ("manifest.json", RUN_SIDECAR):
            continue
        is_expected = path.name.endswith(".expected.json")
        case_id = (
            path.name[: -len(".expected.json")] if is_expected else path.stem
        )
        if case_id in results and not is_expected:
            continue
        results[case_id] = load_json(path)
    if not results:
        raise AuditError(f"no result files found in {directory}")
    return results


def thread_diff(before, after):
    before_threads = before.get("threads", {})
    after_threads = after.get("threads", {})
    before_roots = set(before_threads)
    after_roots = set(after_threads)
    changed = {}
    for root in sorted(before_roots & after_roots):
        if canonical(before_threads[root]) != canonical(after_threads[root]):
            changed[root] = {
                "before": before_threads[root],
                "after": after_threads[root],
            }
    return {
        "changed": bool(
            before_roots != after_roots or changed
        ),
        "added_roots": sorted(after_roots - before_roots),
        "removed_roots": sorted(before_roots - after_roots),
        "changed_roots": changed,
    }


def compare_case(payload, case_path, before, after):
    before_summary = case_summary(payload, before, case_path)
    after_summary = case_summary(payload, after, case_path)

    threads = thread_diff(before, after)
    digests_before = set(before_summary["attachment_digests"])
    digests_after = set(after_summary["attachment_digests"])
    attachment_changed = (
        before_summary["attachment_fingerprint"]
        != after_summary["attachment_fingerprint"]
        or before_summary["attachment_count"]
        != after_summary["attachment_count"]
        or not before_summary["count_consistent"]
        or not after_summary["count_consistent"]
    )
    attachment = {
        "changed": attachment_changed,
        "count_before": before_summary["attachment_count"],
        "count_after": after_summary["attachment_count"],
        "oracle_count_before": before_summary["oracle_attachment_count"],
        "oracle_count_after": after_summary["oracle_attachment_count"],
        "digests_added": sorted(digests_after - digests_before),
        "digests_removed": sorted(digests_before - digests_after),
        "count_consistent_before": before_summary["count_consistent"],
        "count_consistent_after": after_summary["count_consistent"],
    }
    held_before = before.get("held_messages", [])
    held_after = after.get("held_messages", [])
    holds = {
        "changed": canonical(held_before) != canonical(held_after),
        "before": held_before,
        "after": held_after,
    }
    envelope_changed = before_summary["output_sha256"] != after_summary["output_sha256"]

    anomaly_before = before_summary["anomalies"]
    anomaly_after = after_summary["anomalies"]
    anomaly_delta = {
        key: {"before": anomaly_before.get(key), "after": anomaly_after.get(key)}
        for key in sorted(set(anomaly_before) | set(anomaly_after))
        if canonical(anomaly_before.get(key)) != canonical(anomaly_after.get(key))
    }

    classes = []
    if attachment_changed:
        classes.append("HASH")
    if threads["changed"]:
        classes.append("THREAD")
    if holds["changed"]:
        classes.append("HOLD")
    if envelope_changed and not classes:
        classes.append("ENVELOPE")
    if not classes:
        classes = ["IDENTICAL"]

    return {
        "case": case_path.stem,
        "classification": classes,
        "threads": threads,
        "attachments": attachment,
        "holds": holds,
        "envelope_changed": envelope_changed,
        "anomalies": anomaly_delta,
        "fingerprints": {
            "before": {
                "thread": before_summary["thread_fingerprint"],
                "attachment": before_summary["attachment_fingerprint"],
                "hold": before_summary["hold_fingerprint"],
                "output": before_summary["output_sha256"],
            },
            "after": {
                "thread": after_summary["thread_fingerprint"],
                "attachment": after_summary["attachment_fingerprint"],
                "hold": after_summary["hold_fingerprint"],
                "output": after_summary["output_sha256"],
            },
        },
    }


def print_check_report(report, as_json):
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return
    for item in report["cases"]:
        status = "OK" if item["ok"] else "DRIFT"
        detail = f" ({'; '.join(item['problems'])})" if item["problems"] else ""
        print(f"{status:5s} {item['case']}{detail}")
    print(
        f"{len(report['cases'])} cases, {report['drift_count']} drift, "
        f"manifest={'verified' if report['manifest_ok'] else 'NOT verified'}, "
        f"rules={'unchanged' if not report['rules_changed'] else 'CHANGED'}"
    )


def command_check(args):
    case_paths = case_files(args.cases)
    if not args.baseline.is_dir():
        raise AuditError(f"baseline directory does not exist: {args.baseline}")
    manifest_path = args.baseline / "manifest.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else None

    report = {
        "status": "ok",
        "cases": [],
        "drift_count": 0,
        "manifest_ok": manifest is not None,
        "rules_sha256": rule_fingerprint(),
        "baseline_rules_sha256": (
            manifest.get("rules_sha256") if manifest is not None else None
        ),
    }
    report["rules_changed"] = (
        manifest is not None
        and manifest.get("rules_sha256") != report["rules_sha256"]
    )
    if manifest is not None and manifest.get("schema") != MANIFEST_SCHEMA:
        report["manifest_ok"] = False

    expected_ids = {
        path.name[: -len(".expected.json")]
        for path in args.baseline.glob("*.expected.json")
    }
    observed_ids = set()
    for case_path in case_paths:
        case_id = case_path.stem
        observed_ids.add(case_id)
        problems = []
        expected_path = expected_for(case_path, args.baseline)
        payload, result = result_for_case(case_path)
        if not expected_path.exists():
            problems.append("missing expected baseline file")
        else:
            expected = load_json(expected_path)
            if canonical(expected) != canonical(result):
                problems.append("result differs from expected baseline")
        if manifest is None:
            problems.append("missing manifest.json")
        elif case_id not in manifest.get("cases", {}):
            problems.append("case missing from manifest")
        else:
            computed = case_summary(payload, result, case_path)
            entry = manifest["cases"][case_id]
            mismatched = sorted(
                key for key in computed if canonical(entry.get(key)) != canonical(computed[key])
            )
            if mismatched:
                problems.append("manifest mismatch: " + ", ".join(mismatched))
            if not computed["count_consistent"]:
                problems.append("attachment_count differs from hash oracle")
        report["cases"].append(
            {"case": case_id, "ok": not problems, "problems": problems}
        )

    for extra in sorted(expected_ids - observed_ids):
        report["cases"].append(
            {
                "case": extra,
                "ok": False,
                "problems": ["baseline exists without a sample case"],
            }
        )
    report["drift_count"] = sum(1 for item in report["cases"] if not item["ok"])
    if report["drift_count"]:
        report["status"] = "drift"
    if report["rules_changed"]:
        report["status"] = "rules_changed" if report["status"] == "ok" else "drift"
    print_check_report(report, args.json)
    return 0 if report["status"] == "ok" and report["manifest_ok"] else 1


def command_run(args):
    case_paths = case_files(args.cases)
    targets = []
    for case_path in case_paths:
        targets.append((case_path, args.out / f"{case_path.stem}.json"))
    sidecar = args.out / RUN_SIDECAR
    if not args.force:
        existing = [
            str(path) for _, path in targets if path.exists()
        ] + ([str(sidecar)] if sidecar.exists() else [])
        if existing:
            raise AuditError(
                "refusing to overwrite existing output without --force: "
                + ", ".join(existing)
            )
    args.out.mkdir(parents=True, exist_ok=True)
    inputs = {}
    for case_path, target in targets:
        payload, result = result_for_case(case_path)
        target.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        inputs[case_path.stem] = sha_hex(case_path.read_bytes())
        print(f"wrote {target}")
    sidecar.write_text(
        json.dumps(
            {
                "schema": RUN_SCHEMA,
                "rules_sha256": rule_fingerprint(),
                "cases": inputs,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {sidecar}")
    return 0


def load_run_sidecar(directory):
    path = directory / RUN_SIDECAR
    if not path.exists():
        return None
    sidecar = load_json(path)
    if sidecar.get("schema") != RUN_SCHEMA:
        raise AuditError(f"unsupported run sidecar schema in {path}")
    return sidecar


def command_compare(args):
    before_results = load_result_dir(args.before)
    after_results = load_result_dir(args.after)
    if set(before_results) != set(after_results):
        missing = sorted(set(before_results) - set(after_results))
        extra = sorted(set(after_results) - set(before_results))
        raise AuditError(
            f"result case sets differ; missing after={missing}, extra after={extra}"
        )
    before_sidecar = load_run_sidecar(args.before)
    after_sidecar = load_run_sidecar(args.after)

    report = {"status": "identical", "cases": [], "rules": {}}
    if before_sidecar and after_sidecar:
        same_inputs = before_sidecar.get("cases") == after_sidecar.get("cases")
        if not same_inputs:
            raise AuditError(
                "before/after were produced from different sample inputs; "
                "re-run both directories from the same --cases"
            )
        report["rules"] = {
            "before": before_sidecar.get("rules_sha256"),
            "after": after_sidecar.get("rules_sha256"),
            "changed": before_sidecar.get("rules_sha256")
            != after_sidecar.get("rules_sha256"),
        }

    for case_id in sorted(before_results):
        case_path = args.cases / f"{case_id}.json"
        if not case_path.exists():
            raise AuditError(f"sample case is missing for result set: {case_path}")
        payload = load_json(case_path)
        item = compare_case(
            payload, case_path, before_results[case_id], after_results[case_id]
        )
        report["cases"].append(item)

    changed = [
        item
        for item in report["cases"]
        if item["classification"] != ["IDENTICAL"]
    ]
    rules_changed = bool(report["rules"].get("changed"))
    if changed or rules_changed:
        report["status"] = "changed"

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        if report["rules"]:
            print(
                "rules: "
                f"{report['rules']['before'][:16]} -> "
                f"{report['rules']['after'][:16]}"
                f" ({'CHANGED' if rules_changed else 'unchanged'})"
            )
        for item in report["cases"]:
            print(f"{item['case']:38s} {','.join(item['classification'])}")
            if item["threads"]["changed"]:
                if item["threads"]["added_roots"]:
                    print(f"  threads + roots: {item['threads']['added_roots']}")
                if item["threads"]["removed_roots"]:
                    print(f"  threads - roots: {item['threads']['removed_roots']}")
                for root, change in item["threads"]["changed_roots"].items():
                    print(
                        f"  threads ~ {root}: {change['before']} -> {change['after']}"
                    )
            if item["attachments"]["changed"]:
                print(
                    "  attachments count: "
                    f"{item['attachments']['count_before']} -> "
                    f"{item['attachments']['count_after']}"
                )
                if item["attachments"]["digests_added"]:
                    print(f"  digests +: {item['attachments']['digests_added']}")
                if item["attachments"]["digests_removed"]:
                    print(f"  digests -: {item['attachments']['digests_removed']}")
                if not item["attachments"]["count_consistent_before"]:
                    print("  WARNING: before attachment_count is inconsistent")
                if not item["attachments"]["count_consistent_after"]:
                    print("  WARNING: after attachment_count is inconsistent")
            if item["holds"]["changed"]:
                print(
                    f"  holds: {item['holds']['before']} -> {item['holds']['after']}"
                )
            for key, change in item["anomalies"].items():
                print(f"  anomaly {key}: {change['before']} -> {change['after']}")
            if item["envelope_changed"] and not any(
                [
                    item["threads"]["changed"],
                    item["attachments"]["changed"],
                    item["holds"]["changed"],
                ]
            ):
                print("  envelope changed without thread/hash/hold change")
        print(f"{len(report['cases'])} cases, {len(changed)} changed")
    return 0 if (not changed and not rules_changed) else 1


def command_manifest(args):
    manifest = build_manifest(args.cases, args.label)
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out is None:
        print(text, end="")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def command_inspect(args):
    report = {"status": "ok", "cases": {}}
    for case_path in case_files(args.cases):
        payload, result = result_for_case(case_path)
        report["cases"][case_path.stem] = {
            "anomalies": anomalies(payload, result),
            "thread_count": len(result.get("threads", {})),
            "attachment_count": result.get("attachment_count"),
            "held_messages": result.get("held_messages", []),
        }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    for case_id, entry in report["cases"].items():
        print(case_id)
        for key, value in entry["anomalies"].items():
            print(f"  {key}: {value}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Read-only audit harness for the email evidence index."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser(
        "check", help="compare current pipeline output with the frozen baseline"
    )
    check.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    check.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=command_check)

    run = subparsers.add_parser(
        "run", help="write current pipeline output for every sample case"
    )
    run.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--force", action="store_true")
    run.set_defaults(func=command_run)

    compare = subparsers.add_parser(
        "compare", help="classify differences between two result directories"
    )
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    compare.add_argument("--json", action="store_true")
    compare.set_defaults(func=command_compare)

    manifest = subparsers.add_parser(
        "manifest", help="emit a content/thread/hold manifest for a case set"
    )
    manifest.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    manifest.add_argument("--label", default="v0.1.0")
    manifest.add_argument("--out", type=Path, default=None)
    manifest.set_defaults(func=command_manifest)

    inspect = subparsers.add_parser(
        "inspect", help="report documented anomalies for each sample case"
    )
    inspect.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=command_inspect)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except AuditError as exc:
        print(f"index_audit: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
