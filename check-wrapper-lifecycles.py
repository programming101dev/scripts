#!/usr/bin/env python3
"""Run the executable 11x model-based wrapper lifecycle laboratory."""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import subprocess
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent
CONTRACT_PATH = SCRIPT_DIR / "wrapper-lifecycle-contract.json"
LAB_DIR = SCRIPT_DIR / "wrapper-lab"


def built_directory(repo: Path) -> Path | None:
    marker = repo / ".last-build-dir"
    if marker.is_file():
        candidate = repo / marker.read_text(encoding="utf-8").strip()
        if candidate.is_dir():
            return candidate
    return next((path for path in sorted(repo.glob("build-*")) if path.is_dir()), None)


def p101_paths() -> tuple[list[Path], list[Path]]:
    includes: list[Path] = []
    links: list[Path] = []
    for repo in sorted((WORKSPACE / "libraries").glob("lib_*")):
        include = repo / "include"
        if include.is_dir():
            includes.append(include)
        build = built_directory(repo)
        if build is not None:
            links.append(build)
    return includes, links


def find_program(repo: Path, name: str) -> Path:
    build = built_directory(repo)
    if build is not None:
        candidate = build / name
        if candidate.is_file():
            return candidate
    for candidate in sorted(repo.glob(f"build-*/{name}"), reverse=True):
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"build {repo.name} before running the lifecycle lab")


def configure_driver(output: Path, cc: str) -> Path:
    includes, links = p101_paths()
    build = output / "build"
    command = [
        "cmake",
        "-S",
        str(LAB_DIR),
        "-B",
        str(build),
        f"-DCMAKE_C_COMPILER={cc}",
        f"-DP101_PUBLIC_INCLUDE_DIRS={' '.join(map(str, includes))}",
        f"-DP101_PUBLIC_LINK_DIRS={' '.join(map(str, links))}",
    ]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    subprocess.run(
        ["cmake", "--build", str(build), "--parallel"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return build / "p101-wrapper-lifecycle-driver"


def generated_replays(specification: dict[str, Any], count: int, maximum: int, seed: int) -> list[list[str]]:
    rng = random.Random(seed)
    initial = specification["initial"]
    terminal = specification["terminal"]
    transitions = specification["transitions"]
    replays: list[list[str]] = []
    for _ in range(count):
        state = initial
        replay: list[str] = []
        target_steps = rng.randint(2, maximum)
        while len(replay) < target_steps:
            choices = [item for item in transitions if item["from"] == state]
            if not choices:
                break
            transition = rng.choice(choices)
            replay.append(transition["operation"])
            state = transition["to"]
        while state != terminal:
            choices = [
                item
                for item in transitions
                if item["from"] == state
                and (
                    item["to"] == terminal
                    or any(
                        next_item["from"] == item["to"]
                        for next_item in transitions
                    )
                )
            ]
            if not choices:
                raise RuntimeError(f"scenario cannot return {state} to {terminal}")
            transition = choices[-1]
            replay.append(transition["operation"])
            state = transition["to"]
            if len(replay) > maximum * 3:
                raise RuntimeError("scenario cleanup path did not converge")
        replays.append(replay)
    return replays


def run_case(
    driver: Path,
    event_model: Path,
    resource_tracker: Path,
    output: Path,
    scenario: str,
    replay: list[str],
    mode: str,
    fault_index: int,
) -> tuple[bool, dict[str, object]]:
    case_name = f"{scenario}-{mode}-{fault_index}-{'-'.join(replay)}"
    case_dir = output / "cases" / case_name[:180]
    case_dir.mkdir(parents=True, exist_ok=True)
    calls = case_dir / "calls.log"
    resources = case_dir / "resources.log"
    fault = case_dir / "fault.log"
    model = case_dir / "model.json"
    for stale in (
        calls,
        resources,
        fault,
        model,
        case_dir / "stdout.txt",
        case_dir / "stderr.txt",
        case_dir / "resource-report.txt",
    ):
        stale.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "P101_CALL_LOG": str(calls),
            "P101_CALL_LOG_ARGS": "1",
            "P101_CALL_LOG_RESULT": "1",
            "P101_RESOURCE_LOG": str(resources),
            "P101_FAULT_LOG": str(fault),
        }
    )
    if mode != "none":
        environment["P101_FAULT_CALL"] = str(fault_index)
        environment["P101_FAULT_MODE"] = mode
        environment["P101_FAULT_REPEAT"] = "1"
        if mode == "short":
            environment["P101_FAULT_NAME"] = "p101_write"
            environment["P101_FAULT_AMOUNT"] = "2"
    result = subprocess.run(
        [str(driver), scenario, ",".join(replay)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    (case_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (case_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    model_result = subprocess.run(
        [
            str(event_model),
            "-r",
            str(resources),
            "-c",
            str(calls),
            "-o",
            str(model),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    resource_result = subprocess.run(
        [str(resource_tracker), "-q", str(resources)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    (case_dir / "resource-report.txt").write_text(
        resource_result.stdout + resource_result.stderr, encoding="utf-8"
    )
    fault_hit = fault.is_file() and fault.stat().st_size > 0
    passed = (
        result.returncode == 0
        and model_result.returncode == 0
        and resource_result.returncode == 0
    )
    receipt: dict[str, object] = {
        "scenario": scenario,
        "replay": replay,
        "fault_mode": mode,
        "fault_index": fault_index,
        "fault_hit": fault_hit,
        "driver_status": result.returncode,
        "event_model_status": model_result.returncode,
        "resource_tracker_status": resource_result.returncode,
        "case_directory": str(case_dir),
        "passed": passed,
    }
    return passed, receipt


def minimize_failure(
    driver: Path,
    event_model: Path,
    resource_tracker: Path,
    output: Path,
    receipt: dict[str, object],
) -> list[str]:
    replay = list(receipt["replay"])
    changed = True
    while changed and len(replay) > 1:
        changed = False
        for index in range(len(replay)):
            candidate = replay[:index] + replay[index + 1 :]
            passed, _ = run_case(
                driver,
                event_model,
                resource_tracker,
                output / "minimize",
                str(receipt["scenario"]),
                candidate,
                str(receipt["fault_mode"]),
                int(receipt["fault_index"]),
            )
            if not passed:
                replay = candidate
                changed = True
                break
    return replay


def main() -> int:
    parser = argparse.ArgumentParser(description="Run model-based p101 wrapper lifecycle cases.")
    parser.add_argument("-o", "--output", type=Path, default=Path(tempfile.gettempdir()) / "p101-wrapper-lifecycles")
    parser.add_argument("-c", "--compiler", default=os.environ.get("CC", "cc"))
    parser.add_argument("--seed", type=int)
    parser.add_argument("--cases", type=int)
    parser.add_argument("--max-steps", type=int)
    args = parser.parse_args()

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract.get("schema") != "p101-wrapper-lifecycle-contract-v1":
        print("FAIL: unsupported lifecycle contract")
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        driver = configure_driver(args.output, args.compiler)
        event_model = find_program(WORKSPACE / "libraries" / "lib_tool_event", "p101-event-model")
        resource_tracker = find_program(
            WORKSPACE / "programs" / "p101-resource-tracker",
            "p101-resource-tracker",
        )
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}")
        return 2

    seed = contract["seed"] if args.seed is None else args.seed
    count = contract["generated_cases_per_scenario"] if args.cases is None else args.cases
    maximum = contract["maximum_steps"] if args.max_steps is None else args.max_steps
    maximum_fault_calls = contract["maximum_fault_calls"]
    receipts: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for scenario_index, (scenario, specification) in enumerate(contract["scenarios"].items()):
        replays = generated_replays(specification, count, maximum, seed + scenario_index)
        modes = specification.get("fault_modes", contract["fault_modes"])
        for replay in replays:
            passed, receipt = run_case(driver, event_model, resource_tracker, args.output, scenario, replay, "none", 0)
            receipts.append(receipt)
            if not passed:
                failures.append(receipt)
        fault_replay = min(replays, key=len)
        for mode in modes:
            exhausted = False
            mode_failed = False
            for fault_index in range(1, maximum_fault_calls + 1):
                passed, receipt = run_case(driver, event_model, resource_tracker, args.output, scenario, fault_replay, mode, fault_index)
                receipts.append(receipt)
                if not passed:
                    failures.append(receipt)
                    mode_failed = True
                    break
                if not receipt["fault_hit"]:
                    exhausted = True
                    break
            if not exhausted and not mode_failed:
                receipt["passed"] = False
                receipt["reason"] = "fault schedule did not exhaust"
                failures.append(receipt)

    for failure in failures:
        failure["minimized_replay"] = minimize_failure(driver, event_model, resource_tracker, args.output, failure)
    final = {
        "schema": "p101-wrapper-lifecycle-receipt-v1",
        "platform": platform.system(),
        "machine": platform.machine(),
        "compiler": args.compiler,
        "seed": seed,
        "cases": len(receipts),
        "scenarios": sorted(contract["scenarios"]),
        "failures": failures,
        "passed": not failures,
    }
    receipt_path = args.output / "receipt.json"
    receipt_path.write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrapper lifecycle cases: {len(receipts)}")
    print(f"scenarios: {', '.join(final['scenarios'])}")
    if failures:
        for failure in failures:
            print(
                "FAIL: "
                f"{failure['scenario']} {failure['fault_mode']} "
                f"{failure['fault_index']} replay={failure['minimized_replay']}"
            )
        return 1
    print(f"wrapper lifecycle laboratory passed: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
