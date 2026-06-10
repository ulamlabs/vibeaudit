#!/usr/bin/env python3
"""Convert PEM file to single-line env var format with escaped newlines."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Convert PEM file to single-line env var format with escaped newlines",
        epilog="Example: python3 pem_to_env.py backend/secrets/github-app.pem | pbcopy"
    )
    parser.add_argument("pem_file", help="Path to PEM file")
    
    args = parser.parse_args()
    pem_path = Path(args.pem_file)
    
    if not pem_path.exists():
        parser.error(f"File not found: {pem_path}")
    
    content = pem_path.read_text()
    escaped = content.replace('\n', '\\n')
    print(escaped)


if __name__ == "__main__":
    main()
