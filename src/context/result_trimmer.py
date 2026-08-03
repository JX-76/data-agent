# -*- coding: utf-8 -*-
"""Result trimming primitives for safe context injection."""
from __future__ import unicode_literals

import json

try:
    string_types = (basestring,)
except NameError:  # pragma: no cover - Python 3 runtime
    string_types = (str,)


class ResultTrimmer(object):
    """Trim SQL/tool result rows before injecting them into LLM context."""

    DEFAULT_SAMPLE_ROWS = 3
    DEFAULT_MAX_CHARS = 500
    DEFAULT_MAX_COLUMNS = 8

    @classmethod
    def trim_rows(cls, rows, sample_rows=None, max_chars=None):
        if not rows:
            return {'row_count': 0, 'sample': [], 'columns': []}

        sample_rows = sample_rows or cls.DEFAULT_SAMPLE_ROWS
        max_chars = max_chars or cls.DEFAULT_MAX_CHARS

        result = {'row_count': len(rows)}

        columns = list(rows[0].keys())
        if len(columns) > cls.DEFAULT_MAX_COLUMNS:
            result['columns'] = columns[:cls.DEFAULT_MAX_COLUMNS]
            result['hidden_columns'] = len(columns) - cls.DEFAULT_MAX_COLUMNS
        else:
            result['columns'] = columns

        sample = []
        for row in rows[:sample_rows]:
            trimmed_row = {}
            for key, value in row.items():
                if isinstance(value, string_types) and len(value) > 50:
                    trimmed_row[key] = value[:47] + '...'
                elif isinstance(value, float):
                    trimmed_row[key] = round(value, 2)
                else:
                    trimmed_row[key] = value
            sample.append(trimmed_row)
        result['sample'] = sample

        sample_str = json.dumps(result, ensure_ascii=False)
        if len(sample_str) > max_chars:
            result['sample'] = result['sample'][:max(1, sample_rows // 2)]
            result['truncated'] = True

        return result

    @classmethod
    def extract_key_stats(cls, rows, metric_column=None):
        if len(rows) <= 5:
            return {}

        numeric_cols = []
        if rows and rows[0]:
            for key, value in rows[0].items():
                if isinstance(value, (int, float)):
                    numeric_cols.append(key)

        stats = {}
        for col in numeric_cols[:3]:
            values = [row[col] for row in rows if isinstance(row.get(col), (int, float))]
            if values:
                stats[col] = {
                    'min': round(min(values), 2),
                    'max': round(max(values), 2),
                    'avg': round(sum(values) / len(values), 2),
                }

        return stats
