#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print a concise summary for rackpatch JSON reports.")
    parser.add_argument("--kind", choices=["docker", "package"], required=True)
    parser.add_argument("--input", required=True)
    return parser.parse_args()


def print_docker(report: dict) -> None:
    print("Docker Summary")
    print(f"window={report.get('window', 'all')}")
    print(
        f"stacks={report.get('stack_count', 0)} outdated_stacks={report.get('outdated_stacks', 0)} outdated_images={report.get('outdated_images', 0)}"
    )
    for stack in report.get("stacks", []):
        status = stack.get("status", "unknown")
        if status == "up-to-date":
            print(f"- {stack['name']}: up-to-date")
            continue
        if status == "outdated":
            print(f"- {stack['name']}: {stack.get('outdated_count', 0)}/{stack.get('image_count', 0)} outdated")
            for image in stack.get("images", []):
                if image.get("status") == "outdated":
                    print(
                        f"  {image['ref']} {image.get('local_short', 'unknown')} -> {image.get('remote_short', 'unknown')}"
                    )
            continue
        print(f"- {stack['name']}: {status}")


def print_package(report: dict) -> None:
    print("Package Summary")
    print(
        f"hosts={report.get('host_count', 0)} outdated_hosts={report.get('hosts_outdated', 0)} reboot_hosts={report.get('reboot_hosts', 0)} total_packages={report.get('total_packages', 0)}"
    )
    for host in report.get("hosts", []):
        status = host.get("status", "unknown")
        if status == "up-to-date":
            print(f"- {host['name']}: up-to-date")
            continue
        if status == "reboot-required":
            print(f"- {host['name']}: reboot-required")
            continue
        if status == "outdated":
            reboot = "yes" if host.get("reboot_required") else "no"
            print(f"- {host['name']}: {host.get('package_count', 0)} packages reboot={reboot}")
            for package in host.get("packages", [])[:5]:
                print(f"  {package}")
            if host.get("package_count", 0) > 5:
                print(f"  ... +{host['package_count'] - 5} more")
            continue
        print(f"- {host['name']}: error {host.get('error', status)}")


def main() -> int:
    args = parse_args()
    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.kind == "docker":
        print_docker(report)
    else:
        print_package(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
