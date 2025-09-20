import argparse

from .edinet.fetch import fetch_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codexcli", description="NFAL command line tools")
    subparsers = parser.add_subparsers(dest="command")

    edinet_parser = subparsers.add_parser("edinet", help="EDINET related utilities")
    edinet_subparsers = edinet_parser.add_subparsers(dest="edinet_command")

    fetch_parser = edinet_subparsers.add_parser("fetch", help="Fetch securities reports")
    fetch_parser.add_argument("--edinet", required=True, help="EDINET code (e.g. E05907)")
    fetch_parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    fetch_parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    fetch_parser.add_argument(
        "--prefer",
        choices=["consolidated", "separate"],
        default="consolidated",
        help="Preferred filing type when both consolidated and separate exist",
    )
    fetch_parser.add_argument(
        "--outdir",
        default="output",
        help="Base directory to store fetched data",
    )
    fetch_parser.set_defaults(func=fetch_command)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "func"):
        return args.func(args)
    parser.print_help()
    return 1
