"""Close a terminated run by recording every missing scheduled case as not run."""

import argparse
import json
import os
from pathlib import Path


def recover(report: Path):
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    runs = [r for r in records if r.get("record_type") == "run"]
    cases = [r for r in records if r.get("record_type") == "case"]
    if len(runs) != 1:
        raise ValueError("Expected one run record")
    scheduled = runs[0]["scheduled_cases"]
    if [r["id"] for r in cases] != [r["id"] for r in scheduled[: len(cases)]]:
        raise ValueError("Existing cases are not a valid schedule prefix")
    missing = scheduled[len(cases) :]
    with report.open("a", encoding="utf-8", newline="\n") as output:
        for case in missing:
            output.write(
                json.dumps(
                    {
                        "record_type": "case",
                        **case,
                        "status": "not_run",
                        "response_status": "interrupted",
                        "response_text": "",
                        "evidence": [],
                        "diagnostics": {},
                        "latency_seconds": None,
                        "stop_reason": "Run terminated before this case was durably completed.",
                    }
                )
                + "\n"
            )
        output.flush()
        os.fsync(output.fileno())
    return len(missing)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(f"Recorded {recover(args.report)} interrupted cases")


if __name__ == "__main__":
    main()
