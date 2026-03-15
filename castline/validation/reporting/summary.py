from __future__ import annotations

from pathlib import Path

from castline.validation.evaluate import ValidationComparison


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
