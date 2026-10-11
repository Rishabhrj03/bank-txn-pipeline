"""
src.config
~~~~~~~~~~
Typed configuration model loaded from YAML.

Follows the Open/Closed principle: to support a new path or validation rule,
extend the relevant dataclass rather than changing pipeline logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PathsConfig:
    input_dir: Path
    output_dir: Path
    quarantine_dir: Path
    state_dir: Path


@dataclass
class InputConfig:
    file_pattern: str = "*.csv"
    late_correction_pattern: str = "*late_correction*"
    excluded_files: List[str] = field(default_factory=list)
    column_aliases: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class ValidationConfig:
    date_formats: List[str] = field(
        default_factory=lambda: ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"]
    )
    year_min: int = 2000
    year_max: int = 2100
    required_columns: List[str] = field(
        default_factory=lambda: ["account_number", "txn_date", "ref_no"]
    )


@dataclass
class DeduplicationConfig:
    primary_key: str = "ref_no"
    composite_key_columns: List[str] = field(default_factory=list)


@dataclass
class OutputConfig:
    canonical_file: str = "canonical_transactions.csv"
    quarantine_file: str = "quarantine.csv"
    reconciliation_report: str = "reconciliation_report"


@dataclass
class PipelineConfig:
    """Aggregated, typed configuration for the entire pipeline."""

    paths: PathsConfig
    input: InputConfig
    validation: ValidationConfig
    deduplication: DeduplicationConfig
    output: OutputConfig

    @classmethod
    def from_yaml(cls, config_path: Path, base_dir: Path) -> "PipelineConfig":
        """
        Load and parse a YAML config file.

        Paths that are relative in YAML are resolved relative to base_dir
        (usually the project root), not the CWD — so the pipeline works
        regardless of where it is invoked from.
        """
        with open(config_path, "r", encoding="utf-8") as fh:
            raw: dict = yaml.safe_load(fh)

        def resolve(p: str) -> Path:
            path = Path(p)
            return path if path.is_absolute() else (base_dir / path).resolve()

        p = raw.get("paths", {})
        paths = PathsConfig(
            input_dir=resolve(p.get("input_dir", "data/input")),
            output_dir=resolve(p.get("output_dir", "data/output")),
            quarantine_dir=resolve(p.get("quarantine_dir", "data/quarantine")),
            state_dir=resolve(p.get("state_dir", "data/state")),
        )

        inp = raw.get("input", {})
        input_conf = InputConfig(
            file_pattern=inp.get("file_pattern", "*.csv"),
            late_correction_pattern=inp.get("late_correction_pattern", "*late_correction*"),
            excluded_files=inp.get("excluded_files", []),
            column_aliases=inp.get("column_aliases", {}),
        )

        val = raw.get("validation", {})
        validation_conf = ValidationConfig(
            date_formats=val.get(
                "date_formats", ["%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"]
            ),
            year_min=val.get("year_min", 2000),
            year_max=val.get("year_max", 2100),
            required_columns=val.get(
                "required_columns", ["account_number", "txn_date", "ref_no"]
            ),
        )

        ded = raw.get("deduplication", {})
        dedup_conf = DeduplicationConfig(
            primary_key=ded.get("primary_key", "ref_no"),
            composite_key_columns=ded.get("composite_key_columns", []),
        )

        out = raw.get("output", {})
        output_conf = OutputConfig(
            canonical_file=out.get("canonical_file", "canonical_transactions.csv"),
            quarantine_file=out.get("quarantine_file", "quarantine.csv"),
            reconciliation_report=out.get("reconciliation_report", "reconciliation_report"),
        )

        logger.debug("PipelineConfig loaded from %s", config_path)
        return cls(
            paths=paths,
            input=input_conf,
            validation=validation_conf,
            deduplication=dedup_conf,
            output=output_conf,
        )
