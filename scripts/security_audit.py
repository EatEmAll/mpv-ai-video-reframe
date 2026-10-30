"""Query OSV for every locked PyPI version, including other platforms/groups.

No dependency installation, resolution, footage, local paths or credentials sent.
OSV coverage is advisory data, not a proof that binaries or dependencies are safe.
"""

import hashlib
import json
import sys
import tomllib
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def main():
    lock = tomllib.loads(Path("uv.lock").read_text(encoding="utf-8"))
    packages = sorted(
        {
            (p["name"], p["version"].split("+")[0])
            for p in lock["package"]
            if "registry" in p.get("source", {})
        }
    )
    payload = {
        "queries": [
            {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
            for name, version in packages
        ]
    }
    request = urllib.request.Request(
        "https://api.osv.dev/v1/querybatch",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        results = json.load(response)["results"]
    if len(results) != len(packages):
        raise RuntimeError("Incomplete advisory response")
    report = {
        "lock_sha256_lf": hashlib.sha256(
            Path("uv.lock").read_text(encoding="utf-8").encode()
        ).hexdigest(),
        "queried_at": datetime.now(UTC).isoformat(),
        "source": "https://api.osv.dev/v1/querybatch",
        "scope": "all locked registry packages; CUDA local version suffix normalized",
        "packages": [
            {
                "name": name,
                "version": version,
                "advisories": [v["id"] for v in result.get("vulns", [])],
            }
            for (name, version), result in zip(packages, results, strict=True)
        ],
    }
    print(json.dumps(report, indent=2))
    return int(any(p["advisories"] for p in report["packages"]))


if __name__ == "__main__":
    sys.exit(main())
