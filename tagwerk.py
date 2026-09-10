#!/usr/bin/python3 -I
import argparse
import sys


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tagwerk", description="Passive work-hours ledger for one Linux desktop.")
    parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
