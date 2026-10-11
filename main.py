#!/usr/bin/env python3
"""
bank-txn-pipeline – CLI entry point.

Usage
-----
    python main.py                          # default config
    python main.py --config config/pipeline_config.yaml
    python main.py --log-level DEBUG        # verbose
    python main.py --input-dir /other/dir  # override input directory

The pipeline is fully idempotent: running it multiple times with the
same input files produces identical output.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _configure_logging(level: str) -> None:
    fmt = "%(asctime)s [%(name)-28s] %(levelname)-8s %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bank Transaction Data Ingestion Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/pipeline_config.yaml"),
        help="Path to YAML pipeline configuration file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _configure_logging(args.log_level)

    # Imports deferred so logging is configured before any module imports
    from src.config import PipelineConfig
    from src.pipeline.orchestrator import PipelineOrchestrator

    logger = logging.getLogger(__name__)

    base_dir = Path(__file__).parent.resolve()
    config_path = (
        args.config if args.config.is_absolute() else (base_dir / args.config)
    )

    if not config_path.exists():
        logger.error("Config file not found: %s", config_path)
        return 1

    logger.info("=" * 65)
    logger.info("  Bank Transaction Ingestion Pipeline  v1.0")
    logger.info("=" * 65)
    logger.info("Config : %s", config_path)

    config = PipelineConfig.from_yaml(config_path, base_dir)
    logger.info("Input  : %s", config.paths.input_dir)
    logger.info("Output : %s", config.paths.output_dir)

    orchestrator = PipelineOrchestrator(config)
    report = orchestrator.run()

    print("\n" + report.to_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
