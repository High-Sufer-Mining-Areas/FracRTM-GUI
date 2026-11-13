from __future__ import annotations

import json
import sys

from fracrtm.config import ContinuumConfig
from fracrtm.dfn.continuum_mapping_service import ContinuumMappingService
from fracrtm.messages import argument_parser, configure_cli_language, prepare_json, render_record
from fracrtm.messages import message as msg


def main(argv: list[str] | None = None) -> int:
    argv = configure_cli_language(argv)
    parser = argument_parser(msg("text.run.dfnworks.udfm.mapping"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--ncpu", type=int, default=1)
    parser.add_argument("--stage", choices=("octree", "upscale", "all"), default="all")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = ContinuumConfig.load_json(args.config)
    except Exception as exc:
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

    result = ContinuumMappingService(config).run(
        args.job_dir, ncpu=args.ncpu, stage=args.stage, overwrite=args.overwrite, progress=progress
    )
    print(json.dumps(prepare_json(render_record(result.to_dict())), ensure_ascii=False), flush=True)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
