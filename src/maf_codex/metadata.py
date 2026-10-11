"""Safe SDK summaries and MAF usage from acknowledged session counter deltas."""

COUNTERS = {
    "input_tokens": "input_token_count",
    "output_tokens": "output_token_count",
    "total_tokens": "total_token_count",
    "cached_input_tokens": "cache_read_input_token_count",
    "cache_write_input_tokens": "cache_creation_input_token_count",
    "reasoning_output_tokens": "reasoning_output_token_count",
}


def usage_report(usage) -> dict | None:
    if usage is None:
        return None
    return usage.model_dump(mode="json") if hasattr(usage, "model_dump") else usage


def counter_delta(report: dict | None, previous: dict | None, *, fresh: bool):
    if not isinstance(report, dict) or not isinstance(report.get("total"), dict):
        return None
    if not fresh and not isinstance(previous, dict):
        return None
    delta = {}
    for native, maf in COUNTERS.items():
        current = report["total"].get(native)
        baseline = 0 if fresh else previous.get(native)
        if current is None or baseline is None:
            continue
        if (
            type(current) is not int
            or type(baseline) is not int
            or current < baseline
            or baseline < 0
        ):
            return None
        delta[maf] = current - baseline
    # A partial core report cannot be presented as a complete run usage.
    if not {"input_token_count", "output_token_count", "total_token_count"} <= delta.keys():
        return None
    return delta


def item_summaries(items) -> list[dict]:
    result = []
    for item in items or []:
        item = item.root if hasattr(item, "root") else item
        summary = {}
        for key in (
            "id",
            "type",
            "status",
            "tool",
            "server",
            "success",
            "exit_code",
            "duration_ms",
        ):
            value = item.get(key) if isinstance(item, dict) else getattr(item, key, None)
            if hasattr(value, "value"):
                value = value.value
            if value is not None and isinstance(value, (str, bool, int, float)):
                summary[key] = value
        if summary:
            result.append(summary)
    return result
