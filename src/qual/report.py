from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from ..edinet.fetch import FetchError


@dataclass(frozen=True)
class SectionDefinition:
    key: str
    title: str
    focus_keywords: Sequence[str]


SECTION_DEFINITIONS: Sequence[SectionDefinition] = (
    SectionDefinition(
        key="managementPolicy",
        title="1. 経営方針・戦略",
        focus_keywords=("方針", "戦略", "成長", "投資", "重点", "DX", "海外"),
    ),
    SectionDefinition(
        key="businessRisk",
        title="2. 事業等のリスク",
        focus_keywords=("リスク", "懸念", "課題", "規制", "競合", "為替", "サプライチェーン"),
    ),
    SectionDefinition(
        key="governance",
        title="3. コーポレートガバナンス",
        focus_keywords=("取締役", "社外", "ガバナンス", "内部統制", "コンプライアンス", "多様性"),
    ),
    SectionDefinition(
        key="kam",
        title="4. 監査上の主要な検討事項",
        focus_keywords=("監査", "収益", "認識", "減損", "在庫", "期間帰属"),
    ),
    SectionDefinition(
        key="esg",
        title="5. サステナビリティ / ESG",
        focus_keywords=("ESG", "環境", "CO2", "人的資本", "多様性", "サステナ", "ガバナンス"),
    ),
)


POSITIVE_TERMS = ("成長", "強化", "拡大", "改善", "投資", "好調", "順調", "堅調", "機会")
NEGATIVE_TERMS = ("懸念", "減少", "課題", "影響", "リスク", "不確実", "遅延", "不足", "損失")
FOLLOWUP_TERMS = ("懸念", "課題", "影響", "リスク", "未解決", "調査")


def report_command(args) -> int:
    json_path = Path(args.json).resolve()
    if not json_path.exists():
        raise FetchError(f"定性情報の JSON が見つかりません: {json_path}")

    output_dir = Path(args.output or json_path.parent)
    output_dir.mkdir(parents=True, exist_ok=True)

    generator = QualReportGenerator(
        json_path=json_path,
        output_dir=output_dir,
        title=args.title or json_path.stem,
        mode=(getattr(args, "mode", "full") or "full"),
    )
    generator.generate()
    return 0


class QualReportGenerator:
    def __init__(self, *, json_path: Path, output_dir: Path, title: str, mode: str = "full") -> None:
        self.json_path = json_path
        self.output_dir = output_dir
        self.title = title
        self.mode = mode
        self.payload = self._load_json()
        self.sections = self.payload.get("sections", {})

    def generate(self) -> None:
        report_path = self.output_dir / "定性報告.md"
        generated_at = datetime.now().astimezone().isoformat()

        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(f"# 定性報告 ({self.title})\n")
            fh.write(f"生成日時: {generated_at}\n")
            fh.write(f"ソース: `{self.json_path}`\n\n")
            definitions = self._select_definitions()
            for definition in definitions:
                analysis = self._analyze_section(definition)
                fh.write(f"## {definition.title}\n")
                if not analysis:
                    fh.write("- 該当セクションの情報を取得できませんでした。\n\n")
                    continue

                summary = analysis["summary"]
                highlights = analysis["highlights"]
                tone = analysis["tone"]
                keywords = analysis["keywords"]
                followups = analysis["followups"]

                fh.write("### 概要\n")
                fh.write(f"{summary}\n\n")

                fh.write("### ハイライト\n")
                if highlights:
                    for line in highlights:
                        fh.write(f"- {line}\n")
                else:
                    fh.write("- 強調すべき文章は抽出されませんでした。\n")
                fh.write("\n")

                fh.write("### トーン指標\n")
                fh.write(
                    f"- 前向き語: {tone['positive']} 件 / 慎重語: {tone['negative']} 件 / 総語数: {tone['total_words']}\n"
                )
                fh.write(f"- トーン評価: {tone['assessment']}\n\n")

                fh.write("### キーワード出現上位\n")
                if keywords:
                    fh.write("| キーワード | 出現回数 |\n")
                    fh.write("| --- | --- |\n")
                    for word, count in keywords:
                        fh.write(f"| {word} | {count} |\n")
                else:
                    fh.write("- 抽出キーワードなし\n")
                fh.write("\n")

                fh.write("### フォローアップ候補\n")
                if followups:
                    for item in followups:
                        fh.write(f"- {item}\n")
                else:
                    fh.write("- 特筆すべき懸念ワードは検出されませんでした。\n")
                fh.write("\n")

            fh.write("## 総括\n")
            if self.mode == "quick4":
                fh.write("- 全体所感（2-3行で。成長ドライバー/懸念/良好点）\n")
                fh.write("- 次アクション（本文やIRで確認したい点）\n\n")
            else:
                fh.write("- 本レポートを踏まえた所感・懸念点・次アクションを追記してください。\n\n")

            fh.write("## メモ欄\n")
            fh.write("- 追加で確認すべき外部情報や所感をここに追記してください。\n")

    def _select_definitions(self) -> Sequence[SectionDefinition]:
        if self.mode == "quick4":
            keys = {"managementPolicy", "businessRisk", "governance", "esg"}
            return tuple(d for d in SECTION_DEFINITIONS if d.key in keys)
        return SECTION_DEFINITIONS

    def _analyze_section(self, definition: SectionDefinition) -> Optional[Dict[str, object]]:
        raw = self.sections.get(definition.key)
        if not raw:
            return None

        text = self._normalise_text(raw)
        if not text:
            return None

        sentences = _split_sentences(text)
        summary = _build_summary(sentences, limit=3)
        highlights = _extract_highlights(sentences, definition.focus_keywords, limit=4)
        tokens = _tokenise(text)
        tone = _evaluate_tone(tokens)
        keyword_pairs = _top_keywords(tokens, definition.focus_keywords)
        followups = _detect_followups(sentences)

        return {
            "summary": summary,
            "highlights": highlights,
            "tone": tone,
            "keywords": keyword_pairs,
            "followups": followups,
        }

    def _normalise_text(self, raw: object) -> str:
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, list):
            return "\n".join(item.strip() for item in raw if isinstance(item, str))
        if isinstance(raw, dict):
            segments = []
            for value in raw.values():
                if isinstance(value, str):
                    segments.append(value)
                elif isinstance(value, list):
                    segments.extend([item for item in value if isinstance(item, str)])
            return "\n".join(segments)
        return ""

    def _load_json(self) -> Dict[str, object]:
        try:
            with open(self.json_path, encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError as exc:
            raise FetchError(f"JSON の読み込みに失敗しました: {self.json_path}: {exc}") from exc


def _split_sentences(text: str) -> List[str]:
    compact = re.sub(r"\s+", " ", text)
    raw_sentences = re.split(r"(?<=[。！？])\s*", compact)
    sentences = [_trim(sentence.strip()) for sentence in raw_sentences if sentence.strip()]
    return sentences[:120]


def _build_summary(sentences: Sequence[str], *, limit: int) -> str:
    if not sentences:
        return "- サマリー対象の文章がありません"  # fallback
    selected = [_trim(sentence) for sentence in sentences[:limit]]
    return "\n".join(f"- {line}" for line in selected)


def _extract_highlights(
    sentences: Sequence[str], focus_keywords: Sequence[str], *, limit: int
) -> List[str]:
    keyword_pattern = re.compile("|".join(map(re.escape, focus_keywords))) if focus_keywords else None
    highlighted: List[str] = []
    for sentence in sentences:
        if keyword_pattern and not keyword_pattern.search(sentence):
            continue
        highlighted.append(_trim(sentence))
        if len(highlighted) >= limit:
            break
    if not highlighted:
        highlighted = [_trim(sentence) for sentence in sentences[: min(3, len(sentences))]]
    return highlighted


def _tokenise(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9一-龠ぁ-んァ-ンー・]+", text)


def _evaluate_tone(tokens: Iterable[str]) -> Dict[str, object]:
    tokens_list = list(tokens)
    positive = sum(1 for token in tokens_list if any(term in token for term in POSITIVE_TERMS))
    negative = sum(1 for token in tokens_list if any(term in token for term in NEGATIVE_TERMS))
    assessment = _tone_assessment(positive, negative)
    return {
        "positive": positive,
        "negative": negative,
        "total_words": len(tokens_list),
        "assessment": assessment,
    }


def _tone_assessment(positive: int, negative: int) -> str:
    if positive == negative:
        return "中立"
    if positive > negative:
        if positive >= negative * 2:
            return "前向き"
        return "やや前向き"
    if negative >= positive * 2:
        return "慎重"
    return "やや慎重"


def _top_keywords(tokens: Iterable[str], focus_keywords: Sequence[str]) -> List[tuple[str, int]]:
    counter = Counter(
        token
        for token in tokens
        if len(token) >= 2 and not re.fullmatch(r"[0-9０-９]+", token)
    )
    if focus_keywords:
        filtered = {word: counter[word] for word in focus_keywords if counter[word]}
        if filtered:
            return sorted(filtered.items(), key=lambda item: item[1], reverse=True)[:5]
    return counter.most_common(5)


def _detect_followups(sentences: Sequence[str]) -> List[str]:
    followups: List[str] = []
    pattern = re.compile("|".join(map(re.escape, FOLLOWUP_TERMS)))
    for sentence in sentences:
        if pattern.search(sentence):
            followups.append(_trim(sentence))
            if len(followups) >= 3:
                break
    return followups


def _trim(text: str, limit: int = 140) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
