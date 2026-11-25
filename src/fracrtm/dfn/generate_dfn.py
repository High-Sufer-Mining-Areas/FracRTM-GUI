from __future__ import annotations

import argparse
import json
import sys

from fracrtm.config.dfn_config import DfnConfig, DfnConfigError
from fracrtm.dfn.dfn_generation_service import DfnGenerationService
from fracrtm.messages import argument_parser, configure_cli_language, prepare_json, render_record
from fracrtm.messages import message as msg


def build_parser() -> argparse.ArgumentParser:
    parser = argument_parser(msg("text.generate.a.dfn.using.pydfnworks"))
    parser.add_argument("--config", required=True, help=msg("text.dfnconfig.json.file").render())
    parser.add_argument(
        "--job-dir", required=True, help=msg("text.wsl.dfn.output.directory").render()
    )
    parser.add_argument("--ncpu", type=int, default=1, help=msg("text.number.of.cpus").render())
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=msg("text.allow.pydfnworks.to.replace.a.non.empty.job.directory").render(),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = configure_cli_language(argv)
    args = build_parser().parse_args(argv)
    try:
        config = DfnConfig.load_json(args.config)
    except (DfnConfigError, ValueError) as exc:
        print(
            json.dumps(
                prepare_json(
                    render_record(
                        {
                            "success": False,
                            "stage": "loading",
                            "message": msg("config.load_failed", path=args.config, reason=exc),
                        }
                    )
                ),
                ensure_ascii=False,
            )
        )
        return 2

    def progress(stage: str, message: str) -> None:
        print(
            json.dumps(
                prepare_json(
                    render_record({"event": "progress", "stage": stage, "message": message})
                ),
                ensure_ascii=False,
            ),
            flush=True,
        )

    result = DfnGenerationService(config).generate(
        args.job_dir, ncpu=args.ncpu, overwrite=args.overwrite, progress=progress
    )
    print(json.dumps(prepare_json(render_record(result.to_dict())), ensure_ascii=False), flush=True)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
