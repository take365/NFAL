# AGENTS

NFAL リポジトリで財務・定性分析を進めるためのエージェント運用ガイドです。下記 4 つのメニューを状況に応じて組み合わせ、利用者の指示を確認しながらレポートを作成してください。
初めての会話では役割とメニュー、指示の仕方の紹介をしてください。

## 0. 基本フローと指示の扱い
- 依頼を受けたら **目的・対象 EDINET コード・期間** を必ず確認し、不足があればヒアリングして補完する。
- 既存の出力が最新かどうかを `output/<EDINET>` 以下で確かめ、必要に応じて再取得する。
- 各メニューの実行結果（生成ファイル、ログ、注意事項）を報告し、最後に全体総括と今後のFollow-upを整理する。
- プログラムで生成した出力は、利用者への引渡し前に簡潔なコメント（所感・懸念点）を添える。

## 1. 財務諸表分析支援エージェント（データ取得）
EDINET API から有価証券報告書を取得し、`output/<EDINET>` 以下に格納します。

1. `.env` に `APIKEY` が設定されていることを確認。
2. 取得コマンド:
   ```bash
   python3 -m src edinet fetch \
       --edinet <EDINETコード> \
       --from  <YYYY-MM-DD> \
       --to    <YYYY-MM-DD> \
       [--prefer consolidated|separate] \
       [--cache-dir <dir>] [--clear-cache] [--cache-ttl DAYS]
   ```
3. 初回取得後、`output/<EDINET>/<日付_docID>/` に ZIP・PDF・添付資料、`yuho_latest.json` 等が生成される。再実行時はキャッシュと重複ディレクトリに注意。
4. 取得内容を報告（対象期間、保存先、注意点）。必要に応じてキャッシュの扱いや失敗時の再試行方法を案内する。

## 2. 定量分析エージェント（プログラム＋総括）
財務指標の抽出と定量レポート作成を自動化し、最後に人手で総括コメントを加えます。

1. 定量レポート生成:
   ```bash
   python3 -m src quant report \
       --document output/<EDINET>/<日付_docID>
   ```
2. 生成物: `quant/BalanceSheet.csv`, `IncomeStatement.csv`, `CashFlows.csv`, `定量報告.md`。
   - CSV には現期・前期・差分・YoY が格納される。
   - `定量報告.md` の「AI総括」はプレースホルダーなので **必ず手動で要約コメントを追記** する（主要KPIの増減、財務体質、CFのポイント等）。
3. コメント作成時は、主な増減要因・懸念点・良好点を 2〜3 行にまとめ、次の分析アクション（例: 追加開示の確認）を添えると良い。

## 3. 定性分析エージェント（Lean-4 / ハンドクラフト）
有価証券報告書の記述を読み、必要最小限の4観点＋総括で素早く所感を記録します。

1. `docs/qualitative_analysis_checklist.md` の観点（経営方針、リスク、ガバナンス、KAM、ESG など）を参照。
2. 必要に応じて `yuho_latest.json` 内のテキスト抜粋や公開資料を確認し、以下のような Markdown を手動で作成:
   ```markdown
   # 定性報告 (<銘柄>/<期>)
   生成日時: YYYY-MM-DDTHH:MM:SS+09:00
   参照資料: `output/<EDINET>/yuho_latest.json`

   ## 1. 経営方針・戦略
   - 中期経営計画や長期ビジョンの明確さ、一貫性、期をまたぐ継続性
   - 記述トーン（攻め/守り、前向き/慎重）の変化と根拠
   - 重点テーマ（海外展開、DX、サプライチェーン、ESG など）の強調度合いと施策具体性
   ・・・
   ## 総括
   - 全体所感と次に注視すべき論点

   定性評価では各項目はタスク化してチェックしながら順次実施する。

   ```
3. プログラム補助が欲しい場合は以下を実行し、骨子（要約／ハイライト等）を下敷きに**必ず人手で加筆修正**する。
   - フル版: `python3 -m src qual report --json <JSON> [--title <title>]`
   - Lean-4（推奨）: `python3 -m src qual report --json <JSON> --mode quick4 [--title <title>]`

## 4. 外部情報突合（独立メニュー）
- 目的: 有報の記載（第三者/特別調査委員会、訂正報告書、内部統制、KAM、TOB 等）と適時開示・会社IR・ニュースの整合確認。
- 実行: `python3 -m src external collect --json output/<EDINET>/yuho_latest.json [--output output/<EDINET>/external]`
- 生成物: `output/<EDINET>/external/sources.md`（取得日時／クエリ／URL／タイトル／要点／整合）
- 補助: 必要に応じて `docs/web_search_playbook.md` を参照し、一次資料PDF本文で要点の追記・整合（○/△/×）を更新。

## 補足
- `pandas` / `pyarrow` が必要（既に `python3 -m pip install --user --break-system-packages pandas pyarrow` 済み）。
- レポート提出時は、実行コマンド・生成ファイル・コメントを明示し、追加調査が必要な点を記録。
- 依頼に対して不明点がある場合は、作業着手前に必ず確認質問を行う。
