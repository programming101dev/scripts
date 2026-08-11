#!/usr/bin/env python3
"""Run the executable 10x wrapper conformance contract.

The checked API and unit-test manifests admit the wrapper surface and fixtures.
Clang-derived instrumentation supplies fault/resource capabilities. Each
library test suite is replayed with call logging enabled, and the canonical
lib_tool_event model command validates and normalizes the protocol before this
checker evaluates per-wrapper runtime obligations.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import cast

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT / "runtime"))

from wrapper_fault_contract import (  # noqa: E402
    current_platform_key,
    fault_domain,
    injected_fault_cases,
    load_contract,
)


WORKSPACE = SCRIPTS_ROOT.parent
CONTRACT_PATH = SCRIPTS_ROOT / "contracts" / "wrapper-conformance-contract.json"
INSTRUMENTATION_PATH = SCRIPTS_ROOT / "contracts" / "instrumentation-contract.json"
FAULT_CONTRACT_PATH = (
    SCRIPTS_ROOT / "contracts" / "wrapper-platform-faults.json"
)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def active_libraries() -> dict[str, Path]:
    roles = json.loads(INSTRUMENTATION_PATH.read_text(encoding="utf-8"))[
        "library_roles"
    ]
    return {
        name: WORKSPACE / "libraries" / name
        for name, role in sorted(roles.items())
        if role != "infrastructure"
    }


def find_program(repo: Path, name: str, environment_name: str) -> Path:
    configured = os.environ.get(environment_name)
    if configured:
        candidate = Path(configured)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        raise RuntimeError(f"{environment_name} is not an executable file: {candidate}")
    marker = repo / ".last-build-dir"
    candidates: list[Path] = []
    if marker.is_file():
        candidates.append(repo / marker.read_text(encoding="utf-8").strip() / name)
    candidates.extend(sorted(repo.glob(f"build-*/{name}"), reverse=True))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError(f"cannot find a built {name}; build {repo.name} first")


def normalize_events(
    event_model: Path, resources: Path, calls: Path, output: Path
) -> None:
    result = subprocess.run(
        [
            str(event_model),
            "-r",
            str(resources),
            "-c",
            str(calls),
            "-o",
            str(output),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=600,
    )
    if result.returncode < 0:
        raise RuntimeError(
            f"p101-event-model terminated by signal {-result.returncode} while "
            f"normalizing {calls}; rebuild {event_model.parent.parent.name} "
            "against the current p101 libraries"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"p101-event-model rejected the test event streams: "
            f"{result.stderr.strip()}"
        )


def call_evidence(path: Path) -> tuple[Counter[str], Counter[str], set[str], set[str]]:
    enters: Counter[str] = Counter()
    exits: Counter[str] = Counter()
    arguments: set[str] = set()
    results: set[str] = set()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read normalized event model: {error}") from error
    if document.get("schema") != "p101-run-model-v1":
        raise RuntimeError("normalized event model has an unsupported schema")
    nodes = document.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("normalized event model has no node list")
    for node in nodes:
        if not isinstance(node, dict) or node.get("domain") != "call":
            continue
        name = node.get("name")
        kind = node.get("kind")
        if not isinstance(name, str):
            raise RuntimeError("normalized call node has no name")
        if kind == "call-enter":
            enters[name] += 1
            if node.get("arguments") not in {None, "", "-"}:
                arguments.add(name)
        elif kind == "call-exit":
            exits[name] += 1
            if node.get("result") not in {None, "", "-"}:
                results.add(name)
    return enters, exits, arguments, results


def failure_output(output: str) -> list[str]:
    """Return every line emitted by a failed repository test suite."""
    lines = output.rstrip().splitlines()
    if not lines:
        return ["(test.sh produced no output)"]
    return lines


def print_failure_output(summary: str, output: str) -> None:
    """Print an actionable failure summary without hiding subprocess output."""
    print(summary)
    for line in failure_output(output):
        print(f"  | {line}")


def conformance_run_id(library: str) -> str:
    """Return the shared protocol identity for one library test campaign."""
    return f"p101-wrapper-conformance-{library.replace('_', '-')}"


def run_library_suite(
    library: str, repo: Path, output: Path
) -> tuple[str, subprocess.CompletedProcess[str]]:
    call_log = output / f"{library}.calls.log"
    resource_log = output / f"{library}.resources.log"
    outcome_log = output / f"{library}.outcomes.tsv"
    call_log.unlink(missing_ok=True)
    resource_log.unlink(missing_ok=True)
    outcome_log.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "P101_EVENT_RUN_ID": conformance_run_id(library),
            "P101_WRAPPER_CONFORMANCE": "1",
            "P101_CALL_LOG": str(call_log),
            "P101_CALL_LOG_ARGS": "1",
            "P101_CALL_LOG_RESULT": "1",
            "P101_RESOURCE_LOG": str(resource_log),
            "P101_WRAPPER_OUTCOME_LOG": str(outcome_log),
        }
    )
    result = subprocess.run(
        ["./test.sh"],
        cwd=repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=600,
    )
    (output / f"{library}.test.log").write_text(
        result.stdout, encoding="utf-8"
    )
    return library, result


def run_library_suites(
    selected: dict[str, Path], output: Path, jobs: int
) -> dict[str, subprocess.CompletedProcess[str]]:
    workers = min(jobs, max(1, len(selected)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        completed = executor.map(
            lambda item: run_library_suite(item[0], item[1], output),
            selected.items(),
        )
        return dict(completed)


def write_unit_evidence(
    path: Path, receipts: list[dict[str, object]]
) -> None:
    """Publish the stronger test.sh result for repository-test reuse."""
    path.write_text(
        "library\tstatus\n"
        + "".join(
            f"{item['library']}\t"
            f"{'PASS' if item['passed'] else 'FAIL'}\n"
            for item in sorted(receipts, key=lambda value: str(value["library"]))
        ),
        encoding="utf-8",
    )


def fault_outcome_evidence(
    path: Path,
) -> tuple[
    dict[tuple[str, str, str, str, str], tuple[int, str]],
    list[str],
]:
    """Parse direct generated-test receipts without accepting partial lines."""
    outcomes: dict[
        tuple[str, str, str, str, str],
        tuple[int, str],
    ] = {}
    failures: list[str] = []
    if not path.is_file():
        return outcomes, failures
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 10 or fields[:3] != [
                "P101WRAPPER",
                "1",
                "FAULT",
            ]:
                failures.append(
                    f"{path.name}:{line_number}: malformed outcome record"
                )
                continue
            _magic, _version, _kind, platform_name, library = fields[:5]
            wrapper, domain, symbol, code_text, status = fields[5:]
            try:
                code = int(code_text)
            except ValueError:
                failures.append(
                    f"{path.name}:{line_number}: invalid numeric code "
                    f"{code_text!r}"
                )
                continue
            key = (platform_name, library, wrapper, domain, symbol)
            if key in outcomes:
                failures.append(
                    f"{path.name}:{line_number}: duplicate outcome "
                    f"{library}:{wrapper}:{domain}:{symbol}"
                )
                continue
            if status not in {"PASS", "FAIL"}:
                failures.append(
                    f"{path.name}:{line_number}: invalid status {status!r}"
                )
                continue
            outcomes[key] = (code, status)
    return outcomes, failures


def compare_fault_outcomes(
    expected: set[tuple[str, str, str, str, str]],
    observed: dict[
        tuple[str, str, str, str, str],
        tuple[int, str],
    ],
) -> list[str]:
    """Reject missing, unexpected, or explicitly failed direct outcomes."""
    failures: list[str] = []
    observed_keys = set(observed)
    for missing_outcome in sorted(expected - observed_keys):
        (
            _outcome_platform,
            library,
            wrapper,
            domain,
            symbol,
        ) = missing_outcome
        failures.append(
            f"{library}:{wrapper}: missing direct {domain}:{symbol} "
            "outcome receipt"
        )
    for unexpected_outcome in sorted(observed_keys - expected):
        (
            _outcome_platform,
            library,
            wrapper,
            domain,
            symbol,
        ) = unexpected_outcome
        failures.append(
            f"{library}:{wrapper}: unexpected direct outcome "
            f"{domain}:{symbol}"
        )
    for outcome in sorted(expected & observed_keys):
        _code, status = observed[outcome]
        if status != "PASS":
            (
                _outcome_platform,
                library,
                wrapper,
                domain,
                symbol,
            ) = outcome
            failures.append(
                f"{library}:{wrapper}: direct {domain}:{symbol} "
                "outcome failed"
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run per-wrapper runtime conformance checks."
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "p101-wrapper-conformance",
    )
    parser.add_argument("--library", action="append", default=[])
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=min(4, max(1, os.cpu_count() or 1)),
        help="maximum concurrent independent library suites (default: up to 4)",
    )
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be a positive integer")

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    fault_contract = load_contract(FAULT_CONTRACT_PATH)
    platform_key = current_platform_key()
    if contract.get("schema") != "p101-wrapper-conformance-contract-v4":
        print("FAIL: unsupported wrapper conformance contract")
        return 2
    all_libraries = active_libraries()
    selected = all_libraries
    if args.library:
        requested = set(args.library)
        unknown = requested - selected.keys()
        if unknown:
            print(f"FAIL: unknown libraries: {', '.join(sorted(unknown))}")
            return 2
        selected = {name: selected[name] for name in sorted(requested)}

    args.output.mkdir(parents=True, exist_ok=True)
    event_model = find_program(
        WORKSPACE / "libraries" / "lib_tool_event",
        "p101-event-model",
        "P101_EVENT_MODEL",
    )
    instrumentation_receipt = args.output / "instrumentation.json"
    instrumentation = subprocess.run(
        [
            str(SCRIPTS_ROOT / "checks" / "check-p101-instrumentation.py"),
            "--receipt",
            str(instrumentation_receipt),
        ],
        cwd=SCRIPTS_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=600,
    )
    (args.output / "instrumentation.log").write_text(
        instrumentation.stdout, encoding="utf-8"
    )
    if instrumentation.returncode != 0:
        print_failure_output(
            "FAIL: static instrumentation contract failed",
            instrumentation.stdout,
        )
        return 1
    capability_rows = json.loads(
        instrumentation_receipt.read_text(encoding="utf-8")
    )["function_capabilities"]
    capabilities = {
        (row["library"], row["usr"]): row for row in capability_rows
    }
    failures: list[str] = []
    failure_details: list[dict[str, object]] = []
    receipts: list[dict[str, object]] = []
    required_argument_usrs = set(
        contract["logging"]["argument_wrapper_usrs"]
    )
    required_result_usrs = set(
        contract["logging"]["result_wrapper_usrs"]
    )
    workspace_api_usrs = {
        row["function_usr"]
        for repo in all_libraries.values()
        for row in rows(repo / "api-manifest.tsv")
    }
    unknown_logging_usrs = (
        required_argument_usrs | required_result_usrs
    ) - workspace_api_usrs
    if unknown_logging_usrs:
        print(
            "FAIL: logging contract names unknown declaration identities: "
            + ", ".join(sorted(unknown_logging_usrs))
        )
        return 2
    fault_bindings_by_usr = {
        binding.get("function_usr"): binding
        for binding in fault_contract.get("wrappers", {}).values()
        if isinstance(binding, dict) and binding.get("function_usr")
    }
    if len(fault_bindings_by_usr) != len(
        fault_contract.get("wrappers", {})
    ):
        print(
            "FAIL: platform-fault contract has missing or duplicate "
            "declaration identities"
        )
        return 2

    suite_results = run_library_suites(selected, args.output, args.jobs)

    for library, repo in selected.items():
        library_failure_start = len(failures)
        api_by_usr = {
            row["function_usr"]: row["function"]
            for row in rows(repo / "api-manifest.tsv")
        }
        tests_by_usr = {
            row["function_usr"]: row
            for row in rows(repo / "test" / "unit-test-manifest.tsv")
        }
        missing_test_usrs = set(api_by_usr) - set(tests_by_usr)
        extra_test_usrs = set(tests_by_usr) - set(api_by_usr)
        for wrapper_usr in sorted(missing_test_usrs):
            failures.append(
                f"{library}:{wrapper_usr}: public API has no unit-test identity"
            )
        for wrapper_usr in sorted(extra_test_usrs):
            failures.append(
                f"{library}:{wrapper_usr}: unit test has no public API identity"
            )
        api = set(api_by_usr.values())
        fault_case_count = 0
        fault_cases_by_wrapper: dict[str, int] = {}
        expected_outcomes: set[tuple[str, str, str, str, str]] = set()
        receipt_platform = platform_key or "posix"
        for wrapper_usr, test_row in tests_by_usr.items():
            if wrapper_usr not in api_by_usr:
                continue
            if test_row["test_kind"] != "fault":
                continue
            name = api_by_usr[wrapper_usr]
            binding = fault_bindings_by_usr[wrapper_usr]
            function = binding.get("function")
            symbols = injected_fault_cases(
                fault_contract,
                function,
                platform_key,
            )
            wrapper_case_count = len(symbols)
            fault_cases_by_wrapper[name] = wrapper_case_count
            fault_case_count += wrapper_case_count
            domain = fault_domain(fault_contract, function)
            expected_outcomes.update(
                (
                    receipt_platform,
                    library,
                    name,
                    domain,
                    symbol,
                )
                for symbol in symbols
            )
        call_log = args.output / f"{library}.calls.log"
        resource_log = args.output / f"{library}.resources.log"
        normalized = args.output / f"{library}.run-model.json"
        outcome_log = args.output / f"{library}.outcomes.tsv"
        result = suite_results[library]
        if result.returncode != 0:
            failures.append(f"{library}: test.sh failed")
            failure_details.append(
                {
                    "library": library,
                    "phase": "test.sh",
                    "exit_status": result.returncode,
                    "log": str(args.output / f"{library}.test.log"),
                    "output_lines": failure_output(result.stdout),
                }
            )
            receipts.append(
                {
                    "library": library,
                    "apis": len(api),
                    "trace_applicable": 0,
                    "invoked": 0,
                    "balanced": 0,
                    "fault_tests": sum(
                        row["test_kind"] == "fault"
                        for row in tests_by_usr.values()
                    ),
                    "fault_cases": fault_case_count,
                    "fault_outcomes_observed": 0,
                    "fault_outcome_log": str(outcome_log),
                    "arguments_logged": 0,
                    "results_logged": 0,
                    "non_injected_apis_observed": 0,
                    "non_injected_invocations": 0,
                    "passed": False,
                }
            )
            continue

        observed_outcomes, outcome_failures = fault_outcome_evidence(
            outcome_log
        )
        failures.extend(f"{library}: {item}" for item in outcome_failures)
        observed_keys = set(observed_outcomes)
        failures.extend(
            compare_fault_outcomes(expected_outcomes, observed_outcomes)
        )
        if not call_log.is_file():
            failures.append(f"{library}: tests emitted no call log")
            continue
        try:
            normalize_events(event_model, resource_log, call_log, normalized)
            enters, exits, arguments, results = call_evidence(normalized)
        except RuntimeError as exc:
            failures.append(f"{library}: {exc}")
            continue

        trace_api = {
            name
            for wrapper_usr, name in api_by_usr.items()
            if capabilities.get((library, wrapper_usr), {}).get("has_env")
        }
        for wrapper_usr in sorted(api_by_usr):
            if (library, wrapper_usr) not in capabilities:
                failures.append(
                    f"{library}:{wrapper_usr}: no instrumentation identity"
                )
        missing_calls = sorted(name for name in trace_api if enters[name] == 0)
        unbalanced = sorted(
            name for name in trace_api if enters[name] != exits[name]
        )
        missing_failure = sorted(
            api_by_usr[wrapper_usr]
            for wrapper_usr, test_row in tests_by_usr.items()
            if wrapper_usr in api_by_usr
            if test_row["test_kind"] == "fault"
            and enters[api_by_usr[wrapper_usr]] == 0
        )
        insufficient_fault_cases = sorted(
            (
                name,
                fault_cases_by_wrapper[name],
                enters[name],
            )
            for name in fault_cases_by_wrapper
            if enters[name] < fault_cases_by_wrapper[name]
        )
        required_arguments = {
            api_by_usr[wrapper_usr]
            for wrapper_usr in required_argument_usrs
            if wrapper_usr in api_by_usr
        }
        required_results = {
            api_by_usr[wrapper_usr]
            for wrapper_usr in required_result_usrs
            if wrapper_usr in api_by_usr
        }
        missing_arguments = sorted(required_arguments - arguments)
        missing_results = sorted(required_results - results)
        non_injected_calls = {
            name: max(0, enters[name] - fault_cases_by_wrapper.get(name, 0))
            for name in api
        }
        non_injected_apis = {
            name for name, count in non_injected_calls.items() if count > 0
        }
        for name in missing_calls:
            failures.append(f"{library}:{name}: no runtime success/failure invocation")
        for name in unbalanced:
            failures.append(
                f"{library}:{name}: ENTER={enters[name]} EXIT={exits[name]}"
            )
        for name in missing_failure:
            failures.append(f"{library}:{name}: fault test emitted no call")
        for name, expected_count, actual_count in insufficient_fault_cases:
            failures.append(
                f"{library}:{name}: fault test emitted {actual_count} call(s), "
                f"expected at least {expected_count} platform fault cases"
            )
        for name in missing_arguments:
            failures.append(f"{library}:{name}: required arguments were not logged")
        for name in missing_results:
            failures.append(f"{library}:{name}: required result was not logged")
        receipts.append(
            {
                "library": library,
                "apis": len(api),
                "trace_applicable": len(trace_api),
                "invoked": len(trace_api - set(missing_calls)),
                "balanced": len(trace_api - set(unbalanced)),
                "fault_tests": sum(
                    row["test_kind"] == "fault"
                    for row in tests_by_usr.values()
                ),
                "fault_cases": fault_case_count,
                "fault_outcomes_observed": len(
                    expected_outcomes & observed_keys
                ),
                "fault_outcome_log": str(outcome_log),
                "arguments_logged": len(required_arguments & arguments),
                "results_logged": len(required_results & results),
                "non_injected_apis_observed": len(non_injected_apis),
                "non_injected_invocations": sum(
                    non_injected_calls.values()
                ),
                "passed": not (
                    missing_calls
                    or unbalanced
                    or missing_failure
                    or insufficient_fault_cases
                    or missing_arguments
                    or missing_results
                    or len(failures) != library_failure_start
                ),
            }
        )

    receipt = {
        "schema": "p101-wrapper-conformance-receipt-v3",
        "contract": str(CONTRACT_PATH),
        "platform_fault_contract": str(FAULT_CONTRACT_PATH),
        "libraries": receipts,
        "public_apis": sum(int(item["apis"]) for item in receipts),
        "fault_cases": sum(int(item["fault_cases"]) for item in receipts),
        "fault_outcomes_observed": sum(
            int(item["fault_outcomes_observed"]) for item in receipts
        ),
        "non_injected_apis_observed": sum(
            int(item["non_injected_apis_observed"]) for item in receipts
        ),
        "failures": failures,
        "failure_details": failure_details,
        "passed": not failures,
    }
    write_unit_evidence(args.output / "unit-tests.tsv", receipts)
    (args.output / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"wrapper conformance: {receipt['public_apis']} APIs, "
        f"{receipt['fault_outcomes_observed']}/"
        f"{receipt['fault_cases']} direct platform fault outcomes, "
        f"{len(receipts)} libraries"
    )
    print(
        "non-injected behavior evidence: "
        f"{receipt['non_injected_apis_observed']}/"
        f"{receipt['public_apis']} APIs "
        "(not automatically classified as native success)"
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        for detail in failure_details:
            output_lines = cast(list[str], detail["output_lines"])
            print(
                f"\n--- {detail['library']} {detail['phase']} "
                f"(exit {detail['exit_status']}; complete output) ---"
            )
            for line in output_lines:
                print(f"| {line}")
            print(f"Full log: {detail['log']}")
        return 1
    print(f"wrapper conformance passed: {args.output / 'receipt.json'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired as error:
        print(
            f"FAIL: wrapper conformance subprocess timed out after "
            f"{error.timeout} seconds: "
            + " ".join(map(str, error.cmd))
        )
        raise SystemExit(2) from error
