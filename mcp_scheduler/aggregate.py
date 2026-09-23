"""Правило-агрегация точек данных: `count`, `sum`, `avg`, `min`, `max`.

`aggregate(points, op)` считает агрегат по числовым полям всех payload-словарей.
`count` — просто количество точек. Для `sum/avg/min/max` — по каждому числовому
ключу. Пустой список → `None` (нет данных для отчёта).
"""

from __future__ import annotations

from typing import Any

SUPPORTED_OPS = ("count", "sum", "avg", "min", "max")


def _numeric_fields(points: list[dict[str, object]]) -> dict[str, list[float]]:
    """Числовые значения по ключам (исключая bool — это не метрика)."""
    fields: dict[str, list[float]] = {}
    for point in points:
        for key, value in point.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            fields.setdefault(key, []).append(float(value))
    return fields


def aggregate(
    points: list[dict[str, object]], op: str
) -> dict[str, object] | None:
    """Агрегат по числовым полям точек; пусто → None; неизвестный `op` → ValueError."""
    if op not in SUPPORTED_OPS:
        raise ValueError(f"неизвестный агрегат '{op}' (доступно: {', '.join(SUPPORTED_OPS)})")
    if not points:
        return None
    if op == "count":
        return {"count": len(points)}

    fields = _numeric_fields(points)
    result: dict[str, object] = {"count": len(points)}
    for key, values in fields.items():
        if op == "sum":
            result[key] = sum(values)
        elif op == "avg":
            result[key] = sum(values) / len(values)
        elif op == "min":
            result[key] = min(values)
        else:  # max
            result[key] = max(values)
    return result


def numeric(points: list[dict[str, object]]) -> list[dict[str, Any]]:
    """Точки, у которых есть хотя бы одно числовое поле (для отображения отчёта)."""
    return [point for point in points if _numeric_fields([point])]
