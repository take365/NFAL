from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ..edinet.fetch import FetchError


DEFAULT_QUERIES = (
    "{company} 公開買付け {ym_jp}",
    "{company} TOB {ym_jp}",
    "{company} 第三者委員会 調査報告書",
    "{company} 特別調査委員会 調査報告書",
    "{company} 内部統制 報告書 {ym_jp}",
    "TDnet {company} {ym_jp}",
    "{company} 事業計画 説明資料 {ym_jp}",
)


def collect_command(args: argparse.Namespace) -> int:
    json_path = Path(args.json).resolve()
    if not json_path.exists():
        raise FetchError(f"JSON not found: {json_path}")

    meta = _load_meta(json_path)

    outdir = _resolve_output_dir(args.output, json_path, edinet=meta.edinet_code)
    outdir.mkdir(parents=True, exist_ok=True)
    output_path = outdir / "sources.md"

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()
    ym = _to_ym_jp(meta.submit_datetime) or _to_ym_jp(dt.datetime.now())

    queries = [q.format(company=meta.company, code=meta.security_code or meta.edinet_code, ym_jp=ym) for q in DEFAULT_QUERIES]

    records: List[Dict[str, str]] = []
    for q in queries:
        results = _duckduckgo_search(q, max_results=max(1, int(args.max_per_query or 5)))
        for url, title in results:
            records.append(
                {
                    "query": q,
                    "url": url,
                    "title": title,
                    "points": _auto_points_from_title(title),
                    "consistency": _initial_consistency(url),
                }
            )

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(f"# External Sources ({meta.edinet_code} / {meta.company})\n\n")
        fh.write(f"- 取得日時: {now}\n\n")
        for i, rec in enumerate(records, 1):
            fh.write(f"{i}) クエリ: {rec['query']}\n")
            fh.write(f"- URL: {rec['url']}\n")
            fh.write(f"- タイトル: {rec['title']}\n")
            fh.write(f"- 要点: {rec['points']}\n")
            fh.write(f"- 有報との整合: {rec['consistency']}\n\n")
        fh.write("注意\n")
        fh.write("- PDFやニュースは将来的にURL変更の可能性があるため、取得日時とURLを併記。\n")
        fh.write("- 詳細は一次資料PDF本文を参照し、sources.mdの要点・整合を必要に応じて更新。\n")

    return 0


@dataclass(frozen=True)
class FilingMeta:
    edinet_code: str
    company: str
    doc_id: str
    submit_datetime: Optional[dt.datetime]
    security_code: Optional[str]


def _load_meta(json_path: Path) -> FilingMeta:
    try:
        with open(json_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError as exc:
        raise FetchError(f"JSON の読み込みに失敗しました: {json_path}: {exc}") from exc

    meta = payload.get("meta", {})
    edinet = str(meta.get("edinetCode")) if meta.get("edinetCode") else None
    company = str(meta.get("filerName")) if meta.get("filerName") else None
    doc_id = str(meta.get("docID")) if meta.get("docID") else None
    submit_dt = _parse_dt(meta.get("submitDateTime"))

    # Security code may be embedded in extracted public doc hashes; best effort
    security_code = None
    try:
        # pick first 5-digit numeric in JSON text as potential code
        text = json.dumps(payload, ensure_ascii=False)
        m = re.search(r"\b(\d{4,5})\b", text)
        if m:
            security_code = m.group(1)
    except Exception:
        pass

    if not edinet or not company or not doc_id:
        raise FetchError("JSON meta is missing required fields (edinetCode/filerName/docID)")
    return FilingMeta(edinet_code=edinet, company=company, doc_id=doc_id, submit_datetime=submit_dt, security_code=security_code)


def _parse_dt(value: object) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt_obj = dt.datetime.strptime(text, fmt)
            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=dt.timezone.utc)
            return dt_obj
        except ValueError:
            continue
    return None


def _to_ym_jp(value: Optional[dt.datetime]) -> Optional[str]:
    if not value:
        return None
    jst = value.astimezone(dt.timezone(dt.timedelta(hours=9)))
    return f"{jst.year}年{jst.month}月"


def _resolve_output_dir(output: Optional[str], json_path: Path, *, edinet: str) -> Path:
    if output:
        return Path(output)
    parent = json_path.parent
    if parent.name.upper() == edinet.upper():
        return parent / "external"
    # fallback
    return parent / "external"


def _duckduckgo_search(query: str, *, max_results: int = 5) -> List[Tuple[str, str]]:
    import urllib.parse
    import urllib.request

    url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            html_text = r.read().decode("utf-8", errors="ignore")
    except Exception as exc:  # pragma: no cover - network
        return []

    results: List[Tuple[str, str]] = []
    pattern = re.compile(r'<a[^>]*class=".*?result__a.*?"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
    for m in pattern.finditer(html_text):
        href = m.group(1)
        title = re.sub("<.*?>", "", m.group(2))
        url = _resolve_ddg_redirect(href)
        if not url:
            continue
        results.append((url, html.unescape(title).strip()))
        if len(results) >= max_results:
            break
    return results


def _resolve_ddg_redirect(href: str) -> Optional[str]:
    import urllib.parse

    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urllib.parse.urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            qs = urllib.parse.parse_qs(parsed.query)
            target = qs.get("uddg", [None])[0]
            if target:
                return urllib.parse.unquote(target)
        return href
    except Exception:
        return None


def _auto_points_from_title(title: str) -> str:
    t = title.lower()
    if "公開買付" in title or "tob" in t:
        return "TOB関連の可能性。一次資料で条件確認。"
    if "内部統制" in title:
        return "内部統制報告書の可能性。結論・不備有無を確認。"
    if "説明資料" in title or "プレゼン" in title:
        return "IR資料の可能性。KPI/計画の整合確認。"
    return "要点要約は要本文確認。"


def _initial_consistency(url: str) -> str:
    preferred = ("nikkei.com/nkd/disclosure/tdnr", "nikkei.com/nkd/disclosure/ednr", "release.tdnet.info", "tdnet-pdf.", "aidemy.co.jp", "accenture.jp", "kabutan.jp")
    if any(p in url for p in preferred):
        return "△"
    return "△"
