NFAL プロジェクト README

NFAL は、Codex CLI（codexcli）を用いて EDINET の有価証券報告書を分析するための試作ツールです。財務データの取得・定量レポート化・定性骨子生成・外部情報突合を、簡単なコマンドで支援します。

■ これなに（Overview）
- 目的: 有報の迅速な一次把握（数値抽出・所感メモ・外部照合）
- 方式: Codex CLI 上で `python -m src` コマンド群を実行
- 出力: `output/<EDINET>/` 配下に JSON/ZIP/PDF、定量/定性レポート、外部情報記録を生成

■ はじめかた（Quick Start）
1) `.env` に EDINET API キーを設定: `APIKEY=...`
2) 取得（例）: `python3 -m src edinet fetch --edinet E03310 --from 2025-06-21 --to 2025-09-21`
3) 定量: `python3 -m src quant report --document output/E03310/<日付_docID>`
4) 定性(Lean-4): `python3 -m src qual report --json output/E03310/yuho_latest.json --mode quick4`
5) 外部情報: `python3 -m src external collect --json output/E03310/yuho_latest.json`

■ くわしい使い方（Docs）
- 運用ガイド: `AGENTS.md` を参照（メニュー/手順/出力/所感の作法）
- コマンド詳細: `src/README_FETCH_JP.md` と `src/cli.py` を参照

■ 重要事項（ディスクレーマー）
- 本プロジェクトは試作（PoC）です。出力の正確性・完全性・最新性は保証しません。
- ツールは投資助言を目的とせず、財務・会計・監査に関する専門的判断を代替しません。
- 分析結果は必ず一次資料（EDINET提出書類、注記、監査報告書、会社IR等）で検証してください。利用は自己責任でお願いします。

■ 必要環境
- Python 3.10+
- `.env` に `APIKEY` を設定
- 推奨: `pandas`/`pyarrow`（書類一覧キャッシュに使用）。未導入でも `--no-cache` で利用可

