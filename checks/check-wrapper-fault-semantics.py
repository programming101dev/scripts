#!/usr/bin/env python3
"""Check the identity-bound contract for wrapper fault outcomes."""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = SCRIPTS_ROOT.parent
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

from c_facts import CFactError, acquire  # noqa: E402


CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-fault-semantics.json"
FAILURE_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-failure-contract.json"
LIFECYCLE_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-lifecycle-contract.json"
ENV_HEADER = WORKSPACE / "libraries" / "lib_env" / "include" / "p101_env" / "env.h"
IO_SOURCE = WORKSPACE / "libraries" / "lib_io" / "src" / "unistd.c"
IO_BEHAVIOR = WORKSPACE / "libraries" / "lib_io" / "test" / "test_behavior.c"
IO_MANIFEST = WORKSPACE / "libraries" / "lib_io" / "api-manifest.tsv"
def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_identities() -> dict[str, str]:
    with IO_MANIFEST.open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    by_usr = {row["function_usr"]: row["function"] for row in rows}
    return by_usr


def validate(
    contract: dict[str, Any],
    failure: dict[str, Any],
    lifecycle: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    expected_modes = {"error", "eintr", "timeout", "short", "uncertain"}
    modes = contract.get("modes", {})
    if contract.get("schema") != "p101-wrapper-fault-semantics-v3":
        failures.append("unsupported fault-semantics schema")
    if set(modes) != expected_modes:
        failures.append("fault mode inventory drifted")

    try:
        facts = acquire(WORKSPACE, (ENV_HEADER, IO_SOURCE, IO_BEHAVIOR))
    except CFactError as error:
        return [str(error)]
    enum_usrs = {
        str(fact.get("usr", ""))
        for fact in facts
        if fact["kind"] == "ENUMERATOR"
    }
    calls_by_caller: dict[str, set[str]] = defaultdict(set)
    roles: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        caller_usr = str(fact.get("caller_usr", ""))
        if fact["kind"] == "CALL" and caller_usr:
            calls_by_caller[caller_usr].add(str(fact.get("usr", "")))
        elif fact["kind"] == "NOTE" and caller_usr:
            value = str(fact.get("value", ""))
            if value.startswith("SEMANTIC_ROLE:"):
                roles[value.removeprefix("SEMANTIC_ROLE:")].add(caller_usr)

    expected = {
        "error": (
            "c:@EA@p101_env_fault_kind@P101_ENV_FAULT_ERROR",
            "before-call",
            "retry-safe",
        ),
        "eintr": (
            "c:@EA@p101_env_fault_kind@P101_ENV_FAULT_ERROR",
            "before-call",
            "retry-safe",
        ),
        "timeout": (
            "c:@EA@p101_env_fault_kind@P101_ENV_FAULT_ERROR",
            "before-call",
            "retry-safe",
        ),
        "short": (
            "c:@EA@p101_env_fault_kind@P101_ENV_FAULT_SHORT",
            "after-partial-progress",
            "progress-known",
        ),
        "uncertain": (
            "c:@EA@p101_env_fault_kind@P101_ENV_FAULT_UNCERTAIN",
            "after-dispatch",
            "outcome-uncertain",
        ),
    }
    base_fields = {
        "kind_usr",
        "phase",
        "disposition",
        "operation_effect",
        "retry_rule",
        "supported_wrapper_usrs",
    }
    for mode, values in expected.items():
        record = modes.get(mode, {})
        required_fields = set(base_fields)
        if mode == "uncertain":
            required_fields.update(("evidence_role", "evidence_wrapper_usr"))
        if set(record) != required_fields:
            failures.append(f"{mode}: semantic fields drifted")
            continue
        observed = (
            record["kind_usr"],
            record["phase"],
            record["disposition"],
        )
        if observed != values:
            failures.append(f"{mode}: phase or disposition drifted")
        if record["kind_usr"] not in enum_usrs:
            failures.append(
                f"{mode}: fault-kind identity is absent from lib_env: "
                f"{record['kind_usr']}"
            )

    by_usr = manifest_identities()
    short_usrs = modes.get("short", {}).get("supported_wrapper_usrs")
    uncertain_usrs = modes.get("uncertain", {}).get(
        "supported_wrapper_usrs"
    )
    if (
        not isinstance(short_usrs, list)
        or not isinstance(uncertain_usrs, list)
        or short_usrs != uncertain_usrs
        or any(usr not in by_usr for usr in short_usrs)
    ):
        failures.append("partial/uncertain wrapper identities are invalid")
        admitted_usrs: set[str] = set()
    else:
        admitted_usrs = set(short_usrs)

    mechanism = contract.get("mechanism", {})
    if set(mechanism) != {
        "hard_selector_usr",
        "action_selector_usr",
        "action_recorder_usr",
        "entry_trace_usr",
    }:
        failures.append("fault mechanism identities drifted")
        action_selector_usr = ""
        action_recorder_usr = ""
    else:
        action_selector_usr = mechanism["action_selector_usr"]
        action_recorder_usr = mechanism["action_recorder_usr"]
    action_callers = {
        caller_usr
        for caller_usr, callees in calls_by_caller.items()
        if action_selector_usr in callees
    }
    if action_callers != admitted_usrs:
        failures.append(
            "fault-action implementation identities drifted: "
            f"expected={sorted(admitted_usrs)} observed={sorted(action_callers)}"
        )
    record_callers = {
        caller_usr
        for caller_usr, callees in calls_by_caller.items()
        if action_recorder_usr in callees
    }
    if record_callers != admitted_usrs:
        failures.append(
            "after-dispatch record identities drifted: "
            f"expected={sorted(admitted_usrs)} observed={sorted(record_callers)}"
        )

    short_failure_usrs = {
        record.get("function_usr")
        for record in failure.get("wrappers", {}).values()
        if "short" in record.get("fault_modes", [])
        and record.get("function_usr") in by_usr
    }
    if short_failure_usrs != admitted_usrs:
        failures.append("generated short-I/O wrapper identities drifted")
    lifecycle_usrs = {
        record.get("fault_usr")
        for record in lifecycle.get("scenarios", {}).values()
        if "short" in record.get("fault_modes", [])
        and record.get("fault_usr") in by_usr
    }
    if lifecycle_usrs != admitted_usrs:
        failures.append("short-I/O lifecycle evidence identities drifted")

    uncertain = modes.get("uncertain", {})
    role = uncertain.get("evidence_role")
    evidence_usr = uncertain.get("evidence_wrapper_usr")
    evidence_functions = roles.get(role, set()) if isinstance(role, str) else set()
    if len(evidence_functions) != 1:
        failures.append("uncertain-outcome semantic evidence is not unique")
    else:
        evidence_function = next(iter(evidence_functions))
        if evidence_usr not in calls_by_caller.get(evidence_function, set()):
            failures.append(
                "uncertain-outcome semantic evidence does not call its "
                "declared wrapper identity"
            )

    if uncertain.get("retry_rule") != "automatic-retry-forbidden":
        failures.append("uncertain outcomes must not authorize automatic retry")
    if (
        failure.get("semantics", {}).get("fault_boundary")
        != "after-entry-trace-before-native-work"
    ):
        failures.append("generated early-failure boundary drifted")
    return failures


def main() -> int:
    failures = validate(
        load(CONTRACT_PATH),
        load(FAILURE_PATH),
        load(LIFECYCLE_PATH),
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(
        "wrapper fault semantics: 5 modes, "
        "4 identity-bound after-dispatch/partial-progress wrappers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
