"""Assemble or clean an RPM environment on a provisioned guest. Never invoke tmt."""

import argparse
import json
import signal
from pathlib import Path

from tmt_rpmtest.environment.lifecycle import finish, prepare
from tmt_rpmtest.environment.plan import settings_from_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "finish"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--cache", type=Path, help="Prepared-root cache for this run on this guest."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--settings", help="Preparation settings as JSON.")
    source.add_argument("--plan", type=Path, help="A local fmf plan with an RPM prepare phase.")
    parser.add_argument("--artifact", action="append", default=[])
    args = parser.parse_args()
    if args.action == "finish":
        finish(args.directory)
        return
    root = Path.cwd()
    if args.plan:
        try:
            settings, root = settings_from_plan(args.plan)
        except ValueError as error:
            parser.error(str(error))
    elif args.settings:
        settings = json.loads(args.settings)
    else:
        parser.error("Preparation requires --settings or --plan.")
    if args.artifact:
        settings["artifacts"] = args.artifact

    def interrupt(signum, frame):
        raise KeyboardInterrupt("RPM preparation was interrupted.")

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGHUP, interrupt)
    prepare(settings, args.directory, root, cache=args.cache)


if __name__ == "__main__":
    main()
