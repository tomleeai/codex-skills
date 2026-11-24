"""List published SWR tags for a vLLM-Ascend A5 openEuler build date."""

import argparse
import json
import re
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REGISTRY = "swr.cn-north-12.myhuaweicloud.com"
REPOSITORY = "atlas_inference/vllm-ascend"


def read_json(url, headers=None):
    request = Request(url, headers=headers or {})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def fetch_tags():
    registry_url = f"https://{REGISTRY}"
    query = urlencode({"service": "dockyard", "scope": f"repository:{REPOSITORY}:pull"})
    auth = read_json(f"{registry_url}/swr/auth/v2/registry/auth/?{query}")
    token = auth.get("token") or auth.get("access_token")
    if not token:
        raise ValueError("SWR did not issue an anonymous pull token.")

    headers = {"Authorization": f"Bearer {token}"}
    index = read_json(f"{registry_url}/v2/{REPOSITORY}/tags/list", headers)
    return index.get("tags") or []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", type=date.fromisoformat, help="tag date (YYYY-MM-DD)")
    parser.add_argument("--branch", default="main", help="published branch (default: main)")
    parser.add_argument("--run-number", type=int, help="restrict to this GitHub Actions run")
    args = parser.parse_args()

    branch = re.escape(args.branch.replace("/", "-"))
    tag_date = args.date.strftime("%Y%m%d")
    pattern = re.compile(rf"nightly-ci-{branch}-a5-openeuler-cann[\w.-]+-{tag_date}-r(\d+)")
    try:
        tags = fetch_tags()
    except (OSError, ValueError) as error:
        parser.exit(2, f"SWR query failed: {error}\n")

    matches = []
    for tag in tags:
        match = pattern.fullmatch(tag)
        if match:
            run_number = int(match[1])
            if args.run_number is None or args.run_number == run_number:
                matches.append((run_number, tag))

    if not matches:
        parser.exit(1, f"No published A5 openEuler tag found for {args.branch} on {tag_date}.\n")

    for _, tag in sorted(matches, reverse=True):
        print(f"{REGISTRY}/{REPOSITORY}:{tag}")


if __name__ == "__main__":
    main()
