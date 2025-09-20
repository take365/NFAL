from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

BASE_URL = "https://api.edinet-fsa.go.jp/api/v2"
USER_AGENT = "codexcli-edinet-fetch/0.1"
DOC_TYPE_YUHO = "120"
DEFAULT_YEARS_BACK = 3
DOCUMENT_LIST_TYPE = "2"
SECTION_KEYWORDS = {
    "managementPolicy": [
        "経営方針",
        "経営方針及び経営環境",
        "Management Policy",
        "Management's Policy",
    ],
    "businessRisk": [
        "事業等のリスク",
        "事業等のリスクについて",
        "Business Risk",
        "Risks Related to Business",
    ],
    "kam": [
        "監査上の主要な検討事項",
        "監査上主要な検討事項",
        "Key Audit Matters",
    ],
    "governance": [
        "コーポレートガバナンス",
        "コーポレート・ガバナンス",
        "Corporate Governance",
    ],
    "esg": [
        "サステナビリティ",
        "サステナビリティに関する事項",
        "ESG",
        "Sustainability",
    ],
}

TRUTHY_VALUES = {"1", "true", "t", "yes", "y", "on"}


@dataclass
class Filing:
    doc_id: str
    period_start: Optional[dt.date]
    period_end: Optional[dt.date]
    submit_time: Optional[dt.datetime]
    consolidated: str
    raw: Dict[str, object]


class FetchError(RuntimeError):
    pass


def fetch_command(args: argparse.Namespace) -> int:
    edinet_code = args.edinet.upper().strip()
    if not re.fullmatch(r"E\d{5}", edinet_code):
        raise FetchError("--edinet must match pattern E\\d{5}")

    api_key = load_api_key()
    date_from = parse_date_arg(args.from_date, default_from())
    date_to = parse_date_arg(args.to_date, dt.date.today())
    if date_from > date_to:
        raise FetchError("--from must be on or before --to")

    results, list_queries = fetch_document_list(api_key, edinet_code, date_from, date_to)
    selected = select_latest_filings(results, prefer=args.prefer)
    if not selected:
        raise FetchError("No securities reports found for given criteria")

    out_base = os.path.join(args.outdir, edinet_code)
    os.makedirs(out_base, exist_ok=True)

    index_entries = []
    list_reference = {
        "base": f"{BASE_URL}/documents.json",
        "dates": [entry["date"] for entry in list_queries],
        "type": DOCUMENT_LIST_TYPE,
    }

    ordered = sorted(selected, key=lambda f: sort_key_for_period(f), reverse=True)
    labels = ["yuho_latest.json", "yuho_previous.json"]
    for filing, filename in zip(ordered, labels):
        doc_dir = os.path.join(out_base, filing.doc_id)
        os.makedirs(doc_dir, exist_ok=True)
        payload, hashes = build_payload(api_key, filing, list_reference, doc_dir)
        output_path = os.path.join(out_base, filename)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        index_entries.append(
            {
                "label": filename,
                "docID": filing.doc_id,
                "periodStart": date_to_iso(filing.period_start),
                "periodEnd": date_to_iso(filing.period_end),
                "submitDateTime": datetime_to_iso(filing.submit_time),
                "consolidatedFlag": filing.consolidated,
                "output": filename,
                "documentDir": filing.doc_id,
                "hashes": hashes,
            }
        )

    index_data = {
        "generatedAt": datetime_to_iso(dt.datetime.now(dt.timezone.utc)),
        "edinetCode": edinet_code,
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "prefer": args.prefer,
        "documents": index_entries,
        "listApiQuery": list_reference,
    }
    with open(os.path.join(out_base, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index_data, fh, ensure_ascii=False, indent=2)

    return 0


def load_api_key(env_path: str = ".env") -> str:
    if not os.path.exists(env_path):
        raise FetchError(".env not found; expected APIKEY entry")
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip().upper() == "APIKEY":
                value = value.strip()
                if not value:
                    break
                return value
    raise FetchError("APIKEY not found in .env")


def default_from() -> dt.date:
    today = dt.date.today()
    try:
        return today.replace(year=today.year - DEFAULT_YEARS_BACK)
    except ValueError:
        # Handles Feb 29 on leap years
        return today.replace(month=2, day=28, year=today.year - DEFAULT_YEARS_BACK)


def parse_date_arg(value: Optional[str], default: dt.date) -> dt.date:
    if not value:
        return default
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise FetchError(f"Invalid date format: {value}")


def iterate_dates_desc(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    current = end
    delta = dt.timedelta(days=1)
    while current >= start:
        yield current
        current -= delta


def fetch_document_list(
    api_key: str,
    edinet_code: str,
    start: dt.date,
    end: dt.date,
) -> Tuple[List[Dict[str, object]], List[Dict[str, str]]]:
    collected: List[Dict[str, object]] = []
    queries: List[Dict[str, str]] = []

    for current_date in iterate_dates_desc(start, end):
        params = {
            "date": current_date.isoformat(),
            "type": DOCUMENT_LIST_TYPE,
        }
        queries.append(params.copy())
        url = f"{BASE_URL}/documents.json?{urllib.parse.urlencode(params)}"
        payload = http_get(url, api_key, accept="application/json")
        data = decode_json(payload)

        for record in data.get("results", []):
            if str(record.get("edinetCode", "")) != edinet_code:
                continue
            collected.append(record)

        if has_enough_periods(collected):
            break

    return collected, queries


def http_get(url: str, api_key: str, *, accept: str) -> bytes:
    request_url = append_subscription_key(url, api_key)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
    }
    if api_key:
        headers["Ocp-Apim-Subscription-Key"] = api_key
        headers["X-API-KEY"] = api_key
    request = urllib.request.Request(request_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 200:
                raise FetchError(f"EDINET API {response.status}: {response.read().decode(errors='ignore')}")
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="ignore") if exc.fp else ""
        raise FetchError(f"HTTP error {exc.code} for {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"Network error calling EDINET API: {exc}") from exc


def append_subscription_key(url: str, api_key: str) -> str:
    if not api_key:
        return url
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key.lower() == "subscription-key" for key, _ in query):
        query.append(("Subscription-Key", api_key))
    new_query = urllib.parse.urlencode(query)
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


def decode_json(payload: bytes) -> Dict[str, object]:
    try:
        return json.loads(payload.decode("utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise FetchError(f"Failed to decode JSON payload: {exc}") from exc


def is_flag_true(value: object) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in TRUTHY_VALUES


def download_optional(api_key: str, doc_id: str, *, doc_type: int, accept: str) -> Optional[bytes]:
    url = f"{BASE_URL}/documents/{doc_id}?type={doc_type}"
    try:
        return http_get(url, api_key, accept=accept)
    except FetchError as exc:
        message = str(exc)
        if "HTTP error 404" in message:
            return None
        raise


def select_latest_filings(records: Iterable[Dict[str, object]], *, prefer: str) -> List[Filing]:
    grouped: Dict[Tuple[Optional[str], Optional[str], str], List[Dict[str, object]]] = {}
    for record in records:
        if str(record.get("docTypeCode")) != DOC_TYPE_YUHO:
            continue
        if str(record.get("withdrawalStatus", "")) == "1":
            continue
        period_start = normalize_date_field(record.get("periodStart"))
        period_end = normalize_date_field(record.get("periodEnd"))
        consolidated = normalize_consolidated(record.get("consolidatedFlag"))
        key = (record.get("periodStart"), record.get("periodEnd"), consolidated)
        grouped.setdefault(key, []).append(record)

    representatives: List[Filing] = []
    for candidate_list in grouped.values():
        latest = sorted(candidate_list, key=lambda r: sort_key_for_submit(r), reverse=True)[0]
        filing = Filing(
            doc_id=str(latest.get("docID")),
            period_start=normalize_date_field(latest.get("periodStart")),
            period_end=normalize_date_field(latest.get("periodEnd")),
            submit_time=normalize_datetime_field(latest.get("submitDateTime")),
            consolidated=normalize_consolidated(latest.get("consolidatedFlag")),
            raw=latest,
        )
        representatives.append(filing)

    if not representatives:
        return []

    per_period: Dict[Tuple[Optional[dt.date], Optional[dt.date]], List[Filing]] = {}
    for filing in representatives:
        key = (filing.period_start, filing.period_end)
        per_period.setdefault(key, []).append(filing)

    chosen: List[Filing] = []
    for filings in per_period.values():
        if len(filings) == 1:
            chosen.append(filings[0])
            continue
        selected = min(
            filings,
            key=lambda f: (
                consolidated_score(f.consolidated, prefer),
                submission_priority(f),
            ),
        )
        chosen.append(selected)

    chosen.sort(key=lambda f: sort_key_for_period(f), reverse=True)
    return chosen[:2]


def has_enough_periods(records: Iterable[Dict[str, object]]) -> bool:
    periods = set()
    for record in records:
        if str(record.get("docTypeCode")) != DOC_TYPE_YUHO:
            continue
        if str(record.get("withdrawalStatus", "")) == "1":
            continue
        periods.add((record.get("periodStart"), record.get("periodEnd")))
        if len(periods) >= 2:
            return True
    return False


def consolidated_score(flag: str, prefer: str) -> int:
    if prefer == "consolidated":
        primary = {"consolidated": 0, "separate": 1}
    else:
        primary = {"separate": 0, "consolidated": 1}
    return primary.get(flag, 2)


def submission_priority(filing: Filing) -> float:
    if not filing.submit_time:
        return float("inf")
    return -filing.submit_time.timestamp()


def sort_key_for_period(filing: Filing) -> Tuple[dt.date, dt.datetime]:
    period = filing.period_end or filing.period_start or dt.date.min
    submit = filing.submit_time or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return (period, submit)


def sort_key_for_submit(record: Dict[str, object]) -> Tuple[dt.datetime, str]:
    submit = normalize_datetime_field(record.get("submitDateTime")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    doc_id = str(record.get("docID", ""))
    return (submit, doc_id)


def normalize_date_field(value: object) -> Optional[dt.date]:
    if not value:
        return None
    text = str(value).strip()
    if not text or text in {"-", "null", "None"}:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_datetime_field(value: object) -> Optional[dt.datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("/", "-")
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            dt_obj = dt.datetime.strptime(text, fmt)
            if dt_obj.tzinfo is None:
                return dt_obj.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
            return dt_obj
        except ValueError:
            continue
    return None


def normalize_consolidated(value: object) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "consolidated", "c"}:
        return "consolidated"
    if text in {"0", "false", "f", "separate", "s"}:
        return "separate"
    return text if text else "unknown"


def build_payload(
    api_key: str,
    filing: Filing,
    list_reference: Dict[str, object],
    doc_dir: str,
) -> Tuple[Dict[str, object], Dict[str, object]]:
    type1_dir = os.path.join(doc_dir, "type1")
    os.makedirs(type1_dir, exist_ok=True)
    zip_bytes = download_zip(api_key, filing.doc_id)
    zip_hash = sha256_hex(zip_bytes)
    zip_path = os.path.join(type1_dir, "document.zip")
    with open(zip_path, "wb") as fh:
        fh.write(zip_bytes)
    extracted_dir = os.path.join(type1_dir, "files")
    sections, extracted_hashes = extract_sections(zip_bytes, extracted_dir)

    pdf_info = None
    if is_flag_true(filing.raw.get("pdfFlag")):
        pdf_bytes = download_optional(api_key, filing.doc_id, doc_type=2, accept="application/pdf")
        if pdf_bytes:
            pdf_path = os.path.join(doc_dir, "document.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(pdf_bytes)
            pdf_info = {
                "path": os.path.relpath(pdf_path, doc_dir),
                "hash": f"sha256:{sha256_hex(pdf_bytes)}",
            }

    attachments_info = None
    if is_flag_true(filing.raw.get("attachDocFlag")):
        attach_bytes = download_optional(api_key, filing.doc_id, doc_type=3, accept="application/zip")
        if attach_bytes:
            attachments_dir = os.path.join(doc_dir, "attachments")
            os.makedirs(attachments_dir, exist_ok=True)
            attach_zip_path = os.path.join(attachments_dir, "attachments.zip")
            with open(attach_zip_path, "wb") as fh:
                fh.write(attach_bytes)
            attach_extract_dir = os.path.join(attachments_dir, "files")
            attachments_hashes = extract_zip(attach_bytes, attach_extract_dir)
            attachments_info = {
                "zip": f"sha256:{sha256_hex(attach_bytes)}",
                "zipPath": os.path.relpath(attach_zip_path, doc_dir),
                "baseDir": os.path.relpath(attach_extract_dir, doc_dir),
                "files": {name: f"sha256:{digest}" for name, digest in attachments_hashes.items()},
            }

    meta = {
        "edinetCode": filing.raw.get("edinetCode"),
        "filerName": filing.raw.get("filerName"),
        "docID": filing.doc_id,
        "docTypeCode": filing.raw.get("docTypeCode"),
        "docDescription": filing.raw.get("docDescription"),
        "consolidatedFlag": filing.consolidated,
        "periodStart": date_to_iso(filing.period_start),
        "periodEnd": date_to_iso(filing.period_end),
        "submitDateTime": datetime_to_iso(filing.submit_time),
        "amendFlag": filing.raw.get("amendFlag"),
        "representativeOfPeriod": True,
    }

    payload = {
        "meta": meta,
        "sections": {
            "managementPolicy": sections.get("managementPolicy"),
            "businessRisk": sections.get("businessRisk"),
            "kam": parse_kam(sections.get("kam")),
            "governance": sections.get("governance"),
            "esg": sections.get("esg"),
        },
        "source": {
            "listApiQuery": list_reference,
            "download": {
                "type": 1,
                "url": f"{BASE_URL}/documents/{filing.doc_id}?type=1",
                "saved": os.path.relpath(zip_path, doc_dir),
                "extractedDir": os.path.relpath(extracted_dir, doc_dir),
            },
            "hashes": {
                "zip": f"sha256:{zip_hash}",
                "extractedFiles": {name: f"sha256:{digest}" for name, digest in extracted_hashes.items()},
            },
        },
    }

    if pdf_info:
        payload["source"]["pdf"] = pdf_info
    if attachments_info:
        payload["source"]["attachments"] = attachments_info

    hashes = {
        "zip": f"sha256:{zip_hash}",
        "extractedFiles": {name: f"sha256:{digest}" for name, digest in extracted_hashes.items()},
    }
    if pdf_info:
        hashes["pdf"] = pdf_info["hash"]
    if attachments_info:
        hashes["attachments"] = attachments_info
    return payload, hashes


def download_zip(api_key: str, doc_id: str) -> bytes:
    url = f"{BASE_URL}/documents/{doc_id}?type=1"
    return http_get(url, api_key, accept="application/zip")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_sections(zip_bytes: bytes, target_dir: Optional[str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    sections: Dict[str, str] = {}
    extracted_hashes: Dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                content = zf.read(info.filename)
                extracted_hashes[info.filename] = sha256_hex(content)
                if target_dir:
                    write_file(target_dir, info.filename, content)
                if not is_textual(info.filename):
                    continue
                decoded = decode_bytes(content)
                text = sanitize_text(decoded)
                snippets = find_sections(text)
                for key, snippet in snippets.items():
                    if key not in sections and len(snippet) > 40:
                        sections[key] = snippet
    except zipfile.BadZipFile as exc:
        raise FetchError("Downloaded ZIP is invalid or corrupt") from exc
    return sections, extracted_hashes


def extract_zip(zip_bytes: bytes, target_dir: str) -> Dict[str, str]:
    extracted_hashes: Dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                content = zf.read(info.filename)
                extracted_hashes[info.filename] = sha256_hex(content)
                write_file(target_dir, info.filename, content)
    except zipfile.BadZipFile as exc:
        raise FetchError("Attachment ZIP is invalid or corrupt") from exc
    return extracted_hashes


def write_file(base_dir: str, relative_path: str, content: bytes) -> None:
    target_path = safe_path_join(base_dir, relative_path)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    with open(target_path, "wb") as fh:
        fh.write(content)


def safe_path_join(base_dir: str, relative_path: str) -> str:
    normalized = os.path.normpath(relative_path)
    if normalized.startswith(".."):
        raise FetchError(f"Unsafe path in ZIP entry: {relative_path}")
    target_path = os.path.join(base_dir, normalized)
    base_real = os.path.realpath(base_dir)
    target_real = os.path.realpath(target_path)
    if not target_real.startswith(base_real):
        raise FetchError(f"ZIP entry escapes target directory: {relative_path}")
    return target_path


def is_textual(filename: str) -> bool:
    lowered = filename.lower()
    return lowered.endswith((".htm", ".html", ".xhtml", ".xbrl", ".xml", ".txt"))


def decode_bytes(content: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp932", "shift_jis", "euc_jp"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1", errors="ignore")


def sanitize_text(html_text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html_text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?i)</div>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u3000", " ")
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def find_sections(text: str) -> Dict[str, str]:
    markers: List[Tuple[int, str]] = []
    lowered = text.lower()
    for key, keywords in SECTION_KEYWORDS.items():
        positions = []
        for keyword in keywords:
            idx = lowered.find(keyword.lower())
            if idx != -1:
                positions.append(idx)
        if positions:
            markers.append((min(positions), key))
    markers.sort()
    sections: Dict[str, str] = {}
    for index, (start, key) in enumerate(markers):
        end = markers[index + 1][0] if index + 1 < len(markers) else len(text)
        snippet = text[start:end].strip()
        if snippet:
            sections[key] = snippet
    return sections


def parse_kam(raw: Optional[str]) -> List[Dict[str, str]]:
    if not raw:
        return []
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return []
    matter = lines[0]
    why_parts: List[str] = []
    response_parts: List[str] = []
    target = why_parts
    for line in lines[1:]:
        lowered = line.lower()
        if any(keyword in lowered for keyword in ("対応", "response", "respective actions")):
            target = response_parts
        target.append(line)
    return [
        {
            "matter": matter,
            "why": " ".join(why_parts).strip(),
            "response": " ".join(response_parts).strip(),
    }
    ]


def date_to_iso(value: Optional[dt.date]) -> Optional[str]:
    return value.isoformat() if value else None


def datetime_to_iso(value: Optional[dt.datetime]) -> Optional[str]:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat()
