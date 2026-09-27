"""Write the public community config (community_public.json) for the Docker image.

Same validation as the PyInstaller bundle (build_exe.write_community_public): public values only, secret keys and
placeholders are refused, COMMUNITY_CONFIG_REQUIRED=1 fails when a value is missing. Without values (local image
builds) nothing is written and the runtime reads env / config.ini instead.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_exe import write_community_public  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    path = write_community_public(out=args.out)
    print(f"community public config: {path or 'not bundled'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
