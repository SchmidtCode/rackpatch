#!/usr/bin/env python3
import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description='Update or insert an env-backed image reference.')
    parser.add_argument('env_file')
    parser.add_argument('key')
    parser.add_argument('value')
    args = parser.parse_args()

    env_path = Path(args.env_file)
    lines = env_path.read_text(encoding='utf-8').splitlines()
    updated = False

    for index, line in enumerate(lines):
        if line.startswith(f'{args.key}='):
            lines[index] = f'{args.key}={args.value}'
            updated = True
            break

    if not updated:
        if lines and lines[-1] != '':
            lines.append('')
        lines.append(f'{args.key}={args.value}')

    env_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
