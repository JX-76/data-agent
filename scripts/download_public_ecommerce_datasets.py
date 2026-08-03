# -*- coding: utf-8 -*-
"""Download and validate public ecommerce/retail datasets for local RAG/SQL evaluation."""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "public_ecommerce"

DATASETS = [
    {
        "dataset": "sample_superstore",
        "file": "global_super_store_orders.tsv",
        "url": "https://raw.githubusercontent.com/plotly/datasets/master/global_super_store_orders.tsv",
        "description": "Global Superstore orders dataset mirrored by Plotly datasets repository.",
        "license_note": "Public sample dataset mirror; verify upstream terms before production redistribution.",
    },
    {
        "dataset": "online_retail_github",
        "file": "supermarket_sales.csv",
        "url": "https://raw.githubusercontent.com/plotly/datasets/master/supermarket_Sales.csv",
        "description": "Supermarket sales sample dataset mirrored by Plotly datasets repository.",
        "license_note": "Public sample dataset mirror; verify upstream terms before production redistribution.",
    },
    {
        "dataset": "seaborn_tips",
        "file": "tips.csv",
        "url": "https://raw.githubusercontent.com/mwaskom/seaborn-data/master/tips.csv",
        "description": "Small tips dataset from seaborn-data for smoke tests and schema sanity checks.",
        "license_note": "seaborn-data public repository; verify upstream terms before production redistribution.",
    },
]

UCI_SOURCE = {
    "dataset": "uci_online_retail",
    "url": "https://archive.ics.uci.edu/dataset/352/online+retail",
    "description": "Original UCI Online Retail dataset source page. It is documented as an optional upstream reference; local evaluation uses directly downloadable public mirrors.",
    "license_note": "Consult UCI repository citation and terms before production redistribution.",
}


def _download(url, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 100:
        print("reuse existing %s (%s bytes)" % (out, out.stat().st_size))
        return
    req = urllib.request.Request(url, headers={"User-Agent": "data-agent-mvp-dataset-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = resp.read()
    if len(payload) < 100:
        raise RuntimeError("downloaded payload too small for %s" % url)
    out.write_bytes(payload)


def _profile_csv(path):
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",\t;").delimiter
    except csv.Error:
        pass
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        header = next(reader)
        rows = sum(1 for _ in reader)
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rows": rows,
        "columns": header,
        "delimiter": delimiter,
        "encoding": "utf-8-or-replacement",
    }


def _write_source(dataset_dir, item, profile):
    content = """# {dataset}\n\nSource URL: {url}\n\nDescription: {description}\n\nRows: {rows}\nBytes: {bytes}\nSHA256: {sha256}\nColumns: {columns}\nDelimiter: {delimiter}\n\nLicense note: {license_note}\n\nDownloaded for local RAG/SQL evaluation. Verify upstream license/citation before production redistribution.\n""".format(
        dataset=item["dataset"],
        url=item["url"],
        description=item["description"],
        rows=profile["rows"],
        bytes=profile["bytes"],
        sha256=profile["sha256"],
        columns=", ".join(profile["columns"]),
        delimiter=repr(profile["delimiter"]),
        license_note=item["license_note"],
    )
    (dataset_dir / "SOURCE.md").write_text(content, encoding="utf-8")


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    manifest = []
    for item in DATASETS:
        out = BASE / item["dataset"] / item["file"]
        print("download %s -> %s" % (item["url"], out))
        _download(item["url"], out)
        profile = _profile_csv(out)
        if profile["rows"] <= 0:
            raise RuntimeError("no data rows in %s" % out)
        _write_source(out.parent, item, profile)
        record = dict(item)
        record.update(profile)
        record["file"] = str(out.relative_to(ROOT)).replace("\\", "/")
        manifest.append(record)

    uci_dir = BASE / UCI_SOURCE["dataset"]
    uci_dir.mkdir(parents=True, exist_ok=True)
    (uci_dir / "SOURCE.md").write_text(
        "# {dataset}\n\nSource URL: {url}\n\nDescription: {description}\n\nLicense note: {license_note}\n\nThis directory documents the original UCI reference. Local evaluation mirrors are tracked in sibling dataset directories.\n".format(**UCI_SOURCE),
        encoding="utf-8",
    )
    manifest.append(dict(UCI_SOURCE, file=None, local_mirror="data/public_ecommerce/online_retail_github/supermarket_sales.csv"))

    (BASE / "DATASET_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
