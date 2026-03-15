from __future__ import annotations

from pathlib import Path

from castline.validation.evaluate import ValidationComparison
from castline.validation.types import ComparisonSummary


def render_markdown_report(comparison: ValidationComparison, source_path: str | Path) -> str:
    return "\n".join(
        [
            "# CASTLINE Phase 0 Validation Summary",
            "",
            f"Source dataset: `{Path(source_path)}`",
            "",
            "## Result",
            f"- Judgment: **{comparison.judgment}**",
            f"- R² improvement vs baseline: **{comparison.r2_improvement_pct:.2f}%**",
            f"- RMSE reduction vs baseline: **{comparison.rmse_reduction_pct:.2f}%**",
            "",
            "## Baseline model",
            f"- Features: {', '.join(comparison.baseline.feature_names)}",
            f"- R²: {comparison.baseline.r2:.4f}",
            f"- RMSE: {comparison.baseline.rmse:.4f}",
            f"- MAE: {comparison.baseline.mae:.4f}",
            f"- Decile lift: {comparison.baseline.decile_lift:.2f}x",
            "",
            "## Environmental model",
            f"- Features: {', '.join(comparison.enriched.feature_names)}",
            f"- R²: {comparison.enriched.r2:.4f}",
            f"- RMSE: {comparison.enriched.rmse:.4f}",
            f"- MAE: {comparison.enriched.mae:.4f}",
            f"- Decile lift: {comparison.enriched.decile_lift:.2f}x",
            "",
            "## Interpretation rubric",
            "- <5% R² improvement => weak",
            "- 5-15% R² improvement => viable",
            "- >15% R² improvement => strong",
        ]
    ) + "\n"



def write_validation_summary(summary: ComparisonSummary, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# CASTLINE Phase 0 Validation Summary",
        "",
        "## Result",
        f"- Judgment: **{summary.thesis_rating}**",
        f"- Validation rows in assembled dataset: **{summary.row_count}**",
        f"- Fully usable comparison rows: **{summary.usable_row_count}**",
    ]
    if summary.withheld_reason:
        lines.extend(
            [
                "- Thesis decision: **withheld pending more data**",
                f"- Reason: {summary.withheld_reason}",
            ]
        )
    else:
        lines.extend(
            [
                f"- Baseline R²: **{summary.baseline_r2:.4f}**",
                f"- Full-model R²: **{summary.full_r2:.4f}**",
                f"- Improvement vs baseline: **{summary.improvement_pct:.2f}%**",
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation rubric",
            "- <5% improvement => weak",
            "- 5-15% improvement => viable",
            "- >15% improvement => strong",
        ]
    )
    output_path.write_text("\n".join(lines) + "\n")
    return output_path
