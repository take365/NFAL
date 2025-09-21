from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import xml.etree.ElementTree as ET

from ..edinet.fetch import FetchError


NS_XBRLI = "http://www.xbrl.org/2003/instance"
NS_XBRLDI = "http://xbrl.org/2006/xbrldi"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

PREFER_CONSOLIDATED = "consolidated"


@dataclass
class Context:
    id: str
    period_type: str
    start_date: Optional[date]
    end_date: Optional[date]
    instant: Optional[date]
    dimensions: Dict[str, str]


@dataclass
class Fact:
    prefix: str
    name: str
    value: Decimal
    context_ref: str
    unit_ref: Optional[str]
    decimals: Optional[int]


@dataclass
class FactCandidate:
    fact: Fact
    context: Context
    period: date
    dimension_score: int
    unit_score: int
    decimals_score: int
    period_length: int
    id_complexity: int


BALANCE_SHEET_ITEMS: List[Tuple[str, str, str]] = [
    ("総資産", "jppfs_cor", "Assets"),
    ("流動資産", "jppfs_cor", "CurrentAssets"),
    ("固定資産", "jppfs_cor", "NoncurrentAssets"),
    ("負債合計", "jppfs_cor", "Liabilities"),
    ("流動負債", "jppfs_cor", "CurrentLiabilities"),
    ("固定負債", "jppfs_cor", "NoncurrentLiabilities"),
    ("純資産", "jppfs_cor", "NetAssets"),
    ("資本合計", "jppfs_cor", "Equity"),
]

INCOME_STATEMENT_ITEMS: List[Tuple[str, str, str]] = [
    ("売上高", "jppfs_cor", "NetSales"),
    ("売上総利益", "jppfs_cor", "GrossProfit"),
    ("営業利益", "jppfs_cor", "OperatingIncome"),
    ("経常利益", "jppfs_cor", "OrdinaryIncome"),
    ("税引前当期純利益", "jppfs_cor", "ProfitLossBeforeIncomeTax"),
    ("当期純利益", "jppfs_cor", "ProfitLoss"),
]

CASH_FLOW_ITEMS: List[Tuple[str, str, str]] = [
    ("営業活動によるキャッシュ・フロー", "jppfs_cor", "NetCashProvidedByUsedInOperatingActivities"),
    ("投資活動によるキャッシュ・フロー", "jppfs_cor", "NetCashProvidedByUsedInInvestingActivities"),
    ("財務活動によるキャッシュ・フロー", "jppfs_cor", "NetCashProvidedByUsedInFinancingActivities"),
    ("フリー・キャッシュ・フロー", "jpcrp_cor", "FreeCashFlowsSummaryOfBusinessResults"),
    ("現金及び現金同等物の増減額", "jppfs_cor", "NetIncreaseDecreaseInCashAndCashEquivalents"),
]


def report_command(args) -> int:
    document_dir = Path(args.document).resolve()
    if not document_dir.exists():
        raise FetchError(f"指定されたディレクトリが存在しません: {document_dir}")

    output_dir = Path(args.output or (document_dir / "quant"))
    output_dir.mkdir(parents=True, exist_ok=True)

    generator = QuantReportGenerator(
        document_dir=document_dir,
        output_dir=output_dir,
        prefer=args.prefer,
    )
    generator.generate()
    return 0


class QuantReportGenerator:
    def __init__(self, document_dir: Path, output_dir: Path, prefer: str) -> None:
        self.document_dir = document_dir
        self.output_dir = output_dir
        self.prefer = prefer
        self.notes: List[str] = []
        self.summary: Dict[str, object] = {}
        self.contexts: Dict[str, Context] = {}
        self.facts: List[Fact] = []
        self.metadata = self._load_metadata()

    def generate(self) -> None:
        xbrl_path = self._find_primary_xbrl()
        if xbrl_path is None:
            self.notes.append("XBRLファイルが見つからなかったため、数値抽出をスキップしました。")
            self._write_empty_outputs()
            return

        self._load_xbrl(xbrl_path)
        if not self.facts:
            self.notes.append("XBRLファクトを取得できなかったため、数値抽出をスキップしました。")
            self._write_empty_outputs()
            return

        balance_data = self._collect_items(BALANCE_SHEET_ITEMS)
        income_data = self._collect_items(INCOME_STATEMENT_ITEMS)
        cashflow_data = self._collect_items(CASH_FLOW_ITEMS)

        self._write_csv("BalanceSheet.csv", balance_data)
        self._write_csv("IncomeStatement.csv", income_data)
        self._write_csv("CashFlows.csv", cashflow_data)

        checks = self._run_checks(balance_data, cashflow_data)
        metrics = self._calculate_metrics(balance_data, income_data, cashflow_data)

        self._write_report(balance_data, income_data, cashflow_data, checks, metrics)

    def _write_empty_outputs(self) -> None:
        headers = [
            "Label",
            "Concept",
            "CurrentValue",
            "CurrentUnit",
            "CurrentContext",
            "CurrentPeriodStart",
            "CurrentPeriodEnd",
            "CurrentDimensions",
            "CurrentDecimalDigits",
            "PriorValue",
            "PriorUnit",
            "PriorContext",
            "PriorPeriodStart",
            "PriorPeriodEnd",
            "PriorDimensions",
            "PriorDecimalDigits",
            "Diff",
            "YoYPercent",
        ]
        for filename in ("BalanceSheet.csv", "IncomeStatement.csv", "CashFlows.csv"):
            path = self.output_dir / filename
            with open(path, "w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(headers)
        self._write_report([], [], [], [], [])

    def _load_metadata(self) -> Dict[str, object]:
        parent = self.document_dir.parent
        index_path = parent / "index.json"
        if not index_path.exists():
            return {}
        try:
            with open(index_path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError:
            return {}

        entries = payload.get("documents", [])
        for entry in entries:
            if entry.get("documentDir") == self.document_dir.name:
                return entry
        return {}

    def _find_primary_xbrl(self) -> Optional[Path]:
        candidates = list(self.document_dir.glob("type1/files/**/*.xbrl"))
        if candidates:
            def sort_key(path: Path) -> Tuple[int, str]:
                return (0 if "PublicDoc" in path.parts else 1, str(path))

            candidates.sort(key=sort_key)
            return candidates[0]
        return None

    def _load_xbrl(self, path: Path) -> None:
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            raise FetchError(f"XBRLの読み込みに失敗しました: {path}: {exc}")
        root = tree.getroot()

        self.contexts = {}
        for element in root.findall(f"{{{NS_XBRLI}}}context"):
            context = self._parse_context(element)
            self.contexts[context.id] = context

        self.facts = []
        for element in root:
            if element.tag.startswith(f"{{{NS_XBRLI}}}"):
                local = element.tag.split("}", 1)[1]
                if local in {"context", "unit", "schemaRef", "footnote"}:
                    continue
            if element.tag.startswith("{http://www.xbrl.org/2003/linkbase}"):
                continue
            fact = self._parse_fact(element)
            if fact:
                self.facts.append(fact)

    def _parse_context(self, element: ET.Element) -> Context:
        context_id = element.get("id", "")
        period_type = ""
        start_date: Optional[date] = None
        end_date: Optional[date] = None
        instant_date: Optional[date] = None

        period = element.find(f"{{{NS_XBRLI}}}period")
        if period is not None:
            instant = period.find(f"{{{NS_XBRLI}}}instant")
            if instant is not None and instant.text:
                period_type = "instant"
                instant_date = _parse_date(instant.text)
            else:
                start = period.find(f"{{{NS_XBRLI}}}startDate")
                end = period.find(f"{{{NS_XBRLI}}}endDate")
                period_type = "duration"
                if start is not None and start.text:
                    start_date = _parse_date(start.text)
                if end is not None and end.text:
                    end_date = _parse_date(end.text)

        dimensions: Dict[str, str] = {}
        segment = element.find(f"{{{NS_XBRLI}}}entity/{{{NS_XBRLI}}}segment")
        if segment is not None:
            for member in segment.findall(f"{{{NS_XBRLDI}}}explicitMember"):
                dim = member.get("dimension", "")
                val = (member.text or "").strip()
                dimensions[dim] = val

        return Context(
            id=context_id,
            period_type=period_type,
            start_date=start_date,
            end_date=end_date,
            instant=instant_date,
            dimensions=dimensions,
        )

    def _parse_fact(self, element: ET.Element) -> Optional[Fact]:
        tag = element.tag
        if not tag.startswith("{"):
            return None
        namespace, local = tag[1:].split("}", 1)
        prefix = _resolve_prefix(namespace)
        if not prefix:
            return None

        nil_attr = element.get(f"{{{NS_XSI}}}nil")
        if nil_attr and nil_attr.lower() == "true":
            return None
        text = element.text
        if text is None:
            return None
        text = text.strip()
        if not text:
            return None
        try:
            value = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
        unit_ref = element.get("unitRef")
        decimals_attr = element.get("decimals")
        decimals: Optional[int] = None
        if decimals_attr and decimals_attr not in {"INF", "inf"}:
            try:
                decimals = int(decimals_attr)
            except ValueError:
                decimals = None

        context_ref = element.get("contextRef")
        if not context_ref:
            return None
        if context_ref not in self.contexts:
            return None

        return Fact(
            prefix=prefix,
            name=local,
            value=value,
            context_ref=context_ref,
            unit_ref=unit_ref,
            decimals=decimals,
        )

    def _collect_items(self, items: Iterable[Tuple[str, str, str]]) -> List[Dict[str, object]]:
        collected: List[Dict[str, object]] = []
        for label, prefix, name in items:
            current, prior = self._select_fact_pair(prefix, name)
            current_fact, current_context = current if current else (None, None)
            prior_fact, prior_context = prior if prior else (None, None)

            current_value = current_fact.value if current_fact else None
            prior_value = prior_fact.value if prior_fact else None
            current_start = _format_date(_context_period_start(current_context)) if current_context else ""
            current_end = _format_date(_context_period_end(current_context)) if current_context else ""
            prior_start = _format_date(_context_period_start(prior_context)) if prior_context else ""
            prior_end = _format_date(_context_period_end(prior_context)) if prior_context else ""
            diff_value: Optional[Decimal] = None
            yoy_decimal: Optional[Decimal] = None
            if current_value is not None and prior_value is not None:
                diff_value = current_value - prior_value
                if prior_value != 0:
                    yoy_decimal = (diff_value / abs(prior_value)) * Decimal(100)

            collected.append(
                {
                    "Label": label,
                    "Concept": f"{prefix}:{name}",
                    "CurrentValue": current_value,
                    "CurrentUnit": current_fact.unit_ref if current_fact else "",
                    "CurrentContext": current_fact.context_ref if current_fact else "",
                    "CurrentPeriodStart": current_start,
                    "CurrentPeriodEnd": current_end,
                    "CurrentDimensions": _format_dimensions(current_context.dimensions if current_context else {}),
                    "CurrentDecimalDigits": "" if not current_fact or current_fact.decimals is None else str(current_fact.decimals),
                    "PriorValue": prior_value,
                    "PriorUnit": prior_fact.unit_ref if prior_fact else "",
                    "PriorContext": prior_fact.context_ref if prior_fact else "",
                    "PriorPeriodStart": prior_start,
                    "PriorPeriodEnd": prior_end,
                    "PriorDimensions": _format_dimensions(prior_context.dimensions if prior_context else {}),
                    "PriorDecimalDigits": "" if not prior_fact or prior_fact.decimals is None else str(prior_fact.decimals),
                    "DiffValue": diff_value,
                    "YoYDecimal": yoy_decimal,
                }
            )
        return collected

    def _select_fact_pair(
        self, prefix: str, name: str
    ) -> Tuple[Optional[Tuple[Fact, Context]], Optional[Tuple[Fact, Context]]]:
        candidates = [fact for fact in self.facts if fact.prefix == prefix and fact.name == name]
        candidate_map: Dict[str, FactCandidate] = {}
        for fact in candidates:
            context = self.contexts.get(fact.context_ref)
            if not context:
                continue
            period_end = _context_period_end(context)
            if period_end is None:
                continue
            dimension_score = _dimension_score(context.dimensions.values(), self.prefer)
            unit_score = 0 if (fact.unit_ref or "").upper().startswith("JPY") else 1
            decimals_score = abs(fact.decimals) if fact.decimals is not None else 99
            period_length = _context_period_length(context)
            candidate = FactCandidate(
                fact=fact,
                context=context,
                period=period_end,
                dimension_score=dimension_score,
                unit_score=unit_score,
                decimals_score=decimals_score,
                period_length=period_length,
                id_complexity=context.id.count("_"),
            )
            existing = candidate_map.get(fact.context_ref)
            if existing is None:
                candidate_map[fact.context_ref] = candidate
            else:
                if (
                    candidate.dimension_score,
                    candidate.unit_score,
                    candidate.id_complexity,
                    candidate.decimals_score,
                    -candidate.period.toordinal(),
                ) < (
                    existing.dimension_score,
                    existing.unit_score,
                    existing.id_complexity,
                    existing.decimals_score,
                    -existing.period.toordinal(),
                ):
                    candidate_map[fact.context_ref] = candidate

        fact_candidates: List[FactCandidate] = list(candidate_map.values())

        if not fact_candidates:
            return (None, None)

        fact_candidates.sort(
            key=lambda c: (
                c.dimension_score,
                c.unit_score,
                c.id_complexity,
                c.decimals_score,
                -c.period.toordinal(),
                -c.period_length,
            )
        )
        best_dimension = fact_candidates[0].dimension_score
        best_unit = fact_candidates[0].unit_score
        preferred = [c for c in fact_candidates if c.dimension_score == best_dimension and c.unit_score == best_unit]
        preferred.sort(key=lambda c: (-c.period.toordinal(), c.id_complexity, c.decimals_score))

        current = preferred[0]
        prior = None
        same_dimensions = [
            c
            for c in preferred[1:]
            if c.context.dimensions == current.context.dimensions and c.id_complexity == current.id_complexity
        ]
        if same_dimensions:
            prior = same_dimensions[0]
        else:
            remaining = [
                c
                for c in fact_candidates
                if c.period < current.period
                and c.context.dimensions == current.context.dimensions
                and c.id_complexity == current.id_complexity
            ]
            if not remaining:
                remaining = [c for c in fact_candidates if c.period < current.period]
            if remaining:
                prior = sorted(remaining, key=lambda c: (-c.period.toordinal(), c.decimals_score))[0]

        return (current.fact, current.context), ((prior.fact, prior.context) if prior else None)

    def _write_csv(self, filename: str, rows: List[Dict[str, object]]) -> None:
        path = self.output_dir / filename
        fieldnames = [
            "Label",
            "Concept",
            "CurrentValue",
            "CurrentUnit",
            "CurrentContext",
            "CurrentPeriodStart",
            "CurrentPeriodEnd",
            "CurrentDimensions",
            "CurrentDecimalDigits",
            "PriorValue",
            "PriorUnit",
            "PriorContext",
            "PriorPeriodStart",
            "PriorPeriodEnd",
            "PriorDimensions",
            "PriorDecimalDigits",
            "Diff",
            "YoYPercent",
        ]
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "Label": row["Label"],
                        "Concept": row["Concept"],
                        "CurrentValue": _format_amount(row.get("CurrentValue")),
                        "CurrentUnit": row.get("CurrentUnit", ""),
                        "CurrentContext": row.get("CurrentContext", ""),
                        "CurrentPeriodStart": row.get("CurrentPeriodStart", ""),
                        "CurrentPeriodEnd": row.get("CurrentPeriodEnd", ""),
                        "CurrentDimensions": row.get("CurrentDimensions", ""),
                        "CurrentDecimalDigits": row.get("CurrentDecimalDigits", ""),
                        "PriorValue": _format_amount(row.get("PriorValue")),
                        "PriorUnit": row.get("PriorUnit", ""),
                        "PriorContext": row.get("PriorContext", ""),
                        "PriorPeriodStart": row.get("PriorPeriodStart", ""),
                        "PriorPeriodEnd": row.get("PriorPeriodEnd", ""),
                        "PriorDimensions": row.get("PriorDimensions", ""),
                        "PriorDecimalDigits": row.get("PriorDecimalDigits", ""),
                        "Diff": _format_amount(row.get("DiffValue")),
                        "YoYPercent": _format_percent(row.get("YoYDecimal")),
                    }
                )

    def _run_checks(
        self,
        balance_rows: List[Dict[str, object]],
        cashflow_rows: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        checks: List[Dict[str, object]] = []

        assets = _find_decimal(balance_rows, "総資産")
        liabilities = _find_decimal(balance_rows, "負債合計")
        net_assets = _find_decimal(balance_rows, "純資産")
        if assets is not None and liabilities is not None and net_assets is not None:
            diff = assets - (liabilities + net_assets)
            ok = abs(diff) <= Decimal("1000")
            checks.append(
                {
                    "name": "貸借一致",
                    "result": "OK" if ok else "NG",
                    "difference": _format_amount(diff),
                    "details": "総資産 ≒ 負債合計 + 純資産",
                }
            )

        op_cf = _find_decimal(cashflow_rows, "営業活動によるキャッシュ・フロー")
        inv_cf = _find_decimal(cashflow_rows, "投資活動によるキャッシュ・フロー")
        fin_cf = _find_decimal(cashflow_rows, "財務活動によるキャッシュ・フロー")
        delta_cash = _find_decimal(cashflow_rows, "現金及び現金同等物の増減額")
        if None not in (op_cf, inv_cf, fin_cf, delta_cash):
            calc = (op_cf or Decimal(0)) + (inv_cf or Decimal(0)) + (fin_cf or Decimal(0))
            diff = (delta_cash or Decimal(0)) - calc
            ok = abs(diff) <= Decimal("1000")
            checks.append(
                {
                    "name": "キャッシュフロー整合",
                    "result": "OK" if ok else "NG",
                    "difference": _format_amount(diff),
                    "details": "営業＋投資＋財務 ≒ 現金増減額",
                }
            )

        if not checks:
            checks.append(
                {
                    "name": "検算対象なし",
                    "result": "SKIP",
                    "difference": "-",
                    "details": "必要な数値が不足",
                }
            )
        return checks

    def _calculate_metrics(
        self,
        balance_rows: List[Dict[str, object]],
        income_rows: List[Dict[str, object]],
        cashflow_rows: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        metrics: List[Dict[str, object]] = []
        balance_map = _rows_to_map(balance_rows)
        income_map = _rows_to_map(income_rows)
        cash_map = _rows_to_map(cashflow_rows)

        def add_metric(name: str, current: Optional[Decimal], prior: Optional[Decimal], *, percent: bool, formula: str) -> None:
            yoy = _calculate_yoy(current, prior)
            metrics.append(
                {
                    "name": name,
                    "current_raw": current,
                    "prior_raw": prior,
                    "yoy_raw": yoy,
                    "current_display": _format_ratio(current, percent=percent),
                    "prior_display": _format_ratio(prior, percent=percent),
                    "yoy_display": _format_percent(yoy),
                    "formula": formula,
                }
            )

        assets_cur = _get_value(balance_map, "総資産")
        assets_prev = _get_prior_value(balance_map, "総資産")
        net_assets_cur = _get_value(balance_map, "純資産")
        net_assets_prev = _get_prior_value(balance_map, "純資産")
        sales_cur = _get_value(income_map, "売上高")
        sales_prev = _get_prior_value(income_map, "売上高")
        gross_cur = _get_value(income_map, "売上総利益")
        gross_prev = _get_prior_value(income_map, "売上総利益")
        op_cur = _get_value(income_map, "営業利益")
        op_prev = _get_prior_value(income_map, "営業利益")
        ord_cur = _get_value(income_map, "経常利益")
        ord_prev = _get_prior_value(income_map, "経常利益")
        net_cur = _get_value(income_map, "当期純利益")
        net_prev = _get_prior_value(income_map, "当期純利益")
        free_cf_cur = _get_value(cash_map, "フリー・キャッシュ・フロー")
        free_cf_prev = _get_prior_value(cash_map, "フリー・キャッシュ・フロー")
        op_cf_cur = _get_value(cash_map, "営業活動によるキャッシュ・フロー")
        op_cf_prev = _get_prior_value(cash_map, "営業活動によるキャッシュ・フロー")

        add_metric(
            "ROE",
            _safe_ratio(net_cur, net_assets_cur),
            _safe_ratio(net_prev, net_assets_prev),
            percent=True,
            formula="当期純利益 ÷ 純資産",
        )
        add_metric(
            "ROA",
            _safe_ratio(net_cur, assets_cur),
            _safe_ratio(net_prev, assets_prev),
            percent=True,
            formula="当期純利益 ÷ 総資産",
        )
        add_metric(
            "売上総利益率",
            _safe_ratio(gross_cur, sales_cur),
            _safe_ratio(gross_prev, sales_prev),
            percent=True,
            formula="売上総利益 ÷ 売上高",
        )
        add_metric(
            "営業利益率",
            _safe_ratio(op_cur, sales_cur),
            _safe_ratio(op_prev, sales_prev),
            percent=True,
            formula="営業利益 ÷ 売上高",
        )
        add_metric(
            "経常利益率",
            _safe_ratio(ord_cur, sales_cur),
            _safe_ratio(ord_prev, sales_prev),
            percent=True,
            formula="経常利益 ÷ 売上高",
        )
        add_metric(
            "純利益率",
            _safe_ratio(net_cur, sales_cur),
            _safe_ratio(net_prev, sales_prev),
            percent=True,
            formula="当期純利益 ÷ 売上高",
        )
        add_metric(
            "自己資本比率",
            _safe_ratio(net_assets_cur, assets_cur),
            _safe_ratio(net_assets_prev, assets_prev),
            percent=True,
            formula="純資産 ÷ 総資産",
        )
        add_metric(
            "総資産回転率",
            _safe_ratio(sales_cur, assets_cur),
            _safe_ratio(sales_prev, assets_prev),
            percent=False,
            formula="売上高 ÷ 総資産",
        )
        add_metric(
            "営業CFマージン",
            _safe_ratio(op_cf_cur, sales_cur),
            _safe_ratio(op_cf_prev, sales_prev),
            percent=True,
            formula="営業CF ÷ 売上高",
        )
        add_metric(
            "フリーCFマージン",
            _safe_ratio(free_cf_cur, sales_cur),
            _safe_ratio(free_cf_prev, sales_prev),
            percent=True,
            formula="フリーCF ÷ 売上高",
        )

        return metrics

    def _write_report(
        self,
        balance_rows: List[Dict[str, object]],
        income_rows: List[Dict[str, object]],
        cashflow_rows: List[Dict[str, object]],
        checks: List[Dict[str, object]],
        metrics: List[Dict[str, object]],
    ) -> None:
        report_path = self.output_dir / "定量報告.md"
        documents = [
            ("BalanceSheet.csv", balance_rows),
            ("IncomeStatement.csv", income_rows),
            ("CashFlows.csv", cashflow_rows),
        ]
        now = datetime.now().astimezone().isoformat()

        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("# 定量報告\n")
            fh.write(f"生成日時: {now}\n\n")
            fh.write("## 対象ドキュメント\n")
            if self.metadata:
                doc_id = self.metadata.get("docID", "-")
                fh.write(f"- ディレクトリ: `{self.document_dir}`\n")
                fh.write(f"- ドキュメントID: `{doc_id}`\n")
                if self.metadata.get("periodEnd"):
                    fh.write(f"- 期末: {self.metadata['periodEnd']}\n")
                if self.metadata.get("submitDateTime"):
                    fh.write(f"- 提出日時: {self.metadata['submitDateTime']}\n")
                if self.metadata.get("consolidatedFlag"):
                    fh.write(f"- 連結区分: {self.metadata['consolidatedFlag']}\n")
            else:
                fh.write(f"- ディレクトリ: `{self.document_dir}`\n")
            fh.write("\n")

            fh.write("## 出力CSV\n")
            for filename, rows in documents:
                fh.write(f"- `{filename}` : {len(rows)} 行\n")
            fh.write("\n")

            fh.write("## 主要項目 前期比\n")
            combined_rows = [row for _, rows in documents for row in rows]
            if combined_rows:
                fh.write("| 項目 | 現期 | 前期 | 増減額 | YoY |\n")
                fh.write("| --- | --- | --- | --- | --- |\n")
                for row in combined_rows:
                    fh.write(
                        "| {label} | {current} | {prior} | {diff} | {yoy} |\n".format(
                            label=row["Label"],
                            current=_format_amount(row.get("CurrentValue")),
                            prior=_format_amount(row.get("PriorValue")),
                            diff=_format_amount(row.get("DiffValue")),
                            yoy=_format_percent(row.get("YoYDecimal")),
                        )
                    )
            else:
                fh.write("- 抽出可能な項目がありませんでした。\n")
            fh.write("\n")

            fh.write("## 指標\n")
            if metrics:
                fh.write("| 指標 | 現期 | 前期 | YoY | 算出式 |\n")
                fh.write("| --- | --- | --- | --- | --- |\n")
                for metric in metrics:
                    fh.write(
                        f"| {metric['name']} | {metric['current_display']} | {metric['prior_display']} | {metric['yoy_display']} | {metric['formula']} |\n"
                    )
            else:
                fh.write("- 指標を計算できませんでした。\n")
            fh.write("\n")

            fh.write("## 検算結果\n")
            if checks:
                for check in checks:
                    fh.write(
                        f"- {check['name']}: {check['result']} (差額: {check['difference']}) - {check['details']}\n"
                    )
            else:
                fh.write("- 検算を実施できませんでした。\n")
            fh.write("\n")

            if self.notes:
                fh.write("## 留意事項\n")
                for note in self.notes:
                    fh.write(f"- {note}\n")
                fh.write("\n")

            fh.write("## AI総括\n")
            fh.write("- ※本レポートを確認し、ここに手動で総括コメントを追記してください。\n")


def _parse_date(value: str) -> Optional[date]:
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None


def _resolve_prefix(namespace: str) -> Optional[str]:
    if namespace.endswith("/jppfs_cor"):
        return "jppfs_cor"
    if namespace.endswith("/jpcrp_cor"):
        return "jpcrp_cor"
    if namespace.endswith("/jpdei_cor"):
        return "jpdei_cor"
    return None


def _format_date(value: Optional[date]) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _format_dimensions(dimensions: Dict[str, str]) -> str:
    if not dimensions:
        return ""
    parts = [f"{dim.split(':')[-1]}={val.split(':')[-1]}" for dim, val in sorted(dimensions.items())]
    return "; ".join(parts)


def _dimension_score(values: Iterable[str], prefer: str) -> int:
    values_list = list(values)
    consolidated = any(value.endswith("ConsolidatedMember") for value in values_list)
    non_consolidated = any(value.endswith("NonConsolidatedMember") for value in values_list)

    if prefer == PREFER_CONSOLIDATED:
        if consolidated:
            return 0
        if non_consolidated:
            return 2
    else:
        if non_consolidated:
            return 0
        if consolidated:
            return 2

    if not values_list:
        return 1
    return 3


def _context_period_end(context: Optional[Context]) -> Optional[date]:
    if context is None:
        return None
    return context.instant or context.end_date or context.start_date


def _context_period_start(context: Optional[Context]) -> Optional[date]:
    if context is None:
        return None
    if context.start_date:
        return context.start_date
    return context.instant


def _context_period_length(context: Context) -> int:
    if context.start_date and context.end_date:
        return (context.end_date - context.start_date).days
    return 0


def _format_amount(value: Optional[Decimal]) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, Decimal) and value.is_finite():
        try:
            integer = int(value.quantize(Decimal("1")))
            return f"{integer:,}"
        except (InvalidOperation, OverflowError):
            return str(value)
    try:
        integer = int(value)
        return f"{integer:,}"
    except (ValueError, TypeError, OverflowError):
        return str(value)


def _format_percent(value: Optional[Decimal]) -> str:
    if value is None:
        return "N/A"
    quant = value.quantize(Decimal("0.1")) if value.is_finite() else value
    sign = "+" if quant > 0 else ""
    return f"{sign}{quant}%"


def _format_ratio(value: Optional[Decimal], *, percent: bool) -> str:
    if value is None:
        return "N/A"
    if percent:
        percent_value = (value * Decimal(100)) if value.is_finite() else value
        return _format_percent(percent_value)
    quant = value.quantize(Decimal("0.01")) if value.is_finite() else value
    return f"{quant}倍"


def _calculate_yoy(current: Optional[Decimal], prior: Optional[Decimal]) -> Optional[Decimal]:
    if current is None or prior is None or prior == 0:
        return None
    change = current - prior
    if change.is_finite():
        return (change / abs(prior)) * Decimal(100)
    return None


def _rows_to_map(rows: List[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    return {row["Label"]: row for row in rows}


def _get_value(row_map: Dict[str, Dict[str, object]], label: str) -> Optional[Decimal]:
    row = row_map.get(label)
    if not row:
        return None
    value = row.get("CurrentValue")
    return value if isinstance(value, Decimal) else None


def _get_prior_value(row_map: Dict[str, Dict[str, object]], label: str) -> Optional[Decimal]:
    row = row_map.get(label)
    if not row:
        return None
    value = row.get("PriorValue")
    return value if isinstance(value, Decimal) else None


def _safe_ratio(numerator: Optional[Decimal], denominator: Optional[Decimal]) -> Optional[Decimal]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _find_decimal(rows: List[Dict[str, object]], label: str) -> Optional[Decimal]:
    row = next((r for r in rows if r.get("Label") == label), None)
    if not row:
        return None
    value = row.get("CurrentValue")
    return value if isinstance(value, Decimal) else None

