from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Sequence


@dataclass
class LinearRegressionModel:
    feature_names: list[str]
    coefficients: list[float]
    intercept: float

    def predict(self, rows: Sequence[Sequence[float]]) -> list[float]:
        predictions: list[float] = []
        for row in rows:
            value = self.intercept
            for coefficient, feature in zip(self.coefficients, row):
                value += coefficient * feature
            predictions.append(value)
        return predictions


def fit_linear_regression(matrix: Sequence[Sequence[float]], targets: Sequence[float], *, learning_rate: float = 0.01, epochs: int = 4000) -> LinearRegressionModel:
    if not matrix:
        raise ValueError("matrix must not be empty")
    feature_count = len(matrix[0])
    coefficients = [0.0 for _ in range(feature_count)]
    intercept = 0.0
    sample_count = len(matrix)

    normalized, means, scales = _normalize(matrix)

    for _ in range(epochs):
        gradient = [0.0 for _ in range(feature_count)]
        intercept_gradient = 0.0
        for row, target in zip(normalized, targets):
            prediction = intercept + sum(weight * value for weight, value in zip(coefficients, row))
            error = prediction - target
            intercept_gradient += error
            for index, value in enumerate(row):
                gradient[index] += error * value

        intercept -= learning_rate * (2.0 / sample_count) * intercept_gradient
        for index in range(feature_count):
            coefficients[index] -= learning_rate * (2.0 / sample_count) * gradient[index]

    denormalized_coefficients: list[float] = []
    adjusted_intercept = intercept
    for coefficient, mean, scale in zip(coefficients, means, scales):
        actual = coefficient / scale
        denormalized_coefficients.append(actual)
        adjusted_intercept -= actual * mean

    return LinearRegressionModel(feature_names=[], coefficients=denormalized_coefficients, intercept=adjusted_intercept)


def rmse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    if len(actual) != len(predicted):
        raise ValueError("actual and predicted length mismatch")
    return sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))


def mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    if len(actual) != len(predicted):
        raise ValueError("actual and predicted length mismatch")
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def r2(actual: Sequence[float], predicted: Sequence[float]) -> float:
    mean_actual = sum(actual) / len(actual)
    ss_res = sum((a - p) ** 2 for a, p in zip(actual, predicted))
    ss_tot = sum((a - mean_actual) ** 2 for a in actual)
    if ss_tot == 0:
        return 1.0
    return 1.0 - (ss_res / ss_tot)


def decile_lift(actual: Sequence[float], predicted: Sequence[float]) -> float:
    paired = sorted(zip(predicted, actual), key=lambda item: item[0])
    if len(paired) < 2:
        raise ValueError("Need at least 2 rows for decile analysis")
    decile_size = max(1, len(paired) // 10)
    if decile_size >= len(paired):
        decile_size = max(1, len(paired) // 2)
    bottom = paired[:decile_size]
    top = paired[-decile_size:]
    bottom_mean = sum(value for _, value in bottom) / len(bottom)
    top_mean = sum(value for _, value in top) / len(top)
    if bottom_mean == 0:
        return float("inf")
    return top_mean / bottom_mean


def _normalize(matrix: Sequence[Sequence[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    feature_count = len(matrix[0])
    means: list[float] = []
    scales: list[float] = []
    for index in range(feature_count):
        column = [row[index] for row in matrix]
        mean = sum(column) / len(column)
        variance = sum((value - mean) ** 2 for value in column) / len(column)
        scale = sqrt(variance) or 1.0
        means.append(mean)
        scales.append(scale)

    normalized: list[list[float]] = []
    for row in matrix:
        normalized.append([(value - mean) / scale for value, mean, scale in zip(row, means, scales)])
    return normalized, means, scales
