# NFAL edinet fetch 使い方

`python -m src edinet fetch` は、EDINET API v2 (`https://api.edinet-fsa.go.jp/api/v2`) から有価証券報告書（docTypeCode=120）を取得し、指定ディレクトリへ保存します。最新と前期の代表有報を選定して `yuho_latest.json` / `yuho_previous.json` を出力し、該当ドキュメントの ZIP/PDF/添付を保存・展開します。

## 事前準備

```env
APIKEY=... # .env に EDINET API のキーを設定
```

## 実行例

```bash
python -m src edinet fetch --edinet E05907
```

※ v2 API の書類一覧は `documents.json?date=YYYY-MM-DD&type=2&Subscription-Key=...` 形式で日付ごとに取得するため、既定の 3 年レンジでは日数分のリクエストが走ります。必要に応じて `--from` / `--to` で期間を絞ってください。

オプション:
- `--from`, `--to` : 期間指定 (YYYY-MM-DD)。既定は「今日から遡って3年〜今日」。
- `--prefer` : 同一期の連結/個別が併存する場合の優先 (`consolidated` or `separate`)。
- `--outdir` : 出力先ディレクトリ（既定値 `output`）。
- `--cache-dir` : 書類一覧キャッシュの保存先（既定は `<outdir>/.cache/edinet`）。
- `--no-cache` : キャッシュを使わず毎回取得（pandas/pyarrow不要）。
- `--clear-cache` : 取得前にキャッシュを削除。
- `--cache-ttl DAYS` : 指定日数より古いキャッシュを自動的に無効化。

補足: 既定ではキャッシュを利用するため `pandas` と `pyarrow` が必要です。インストールが難しい環境では `--no-cache` を付けて実行してください。

## 出力構成

```
output/
  E05907/
    yuho_latest.json
    yuho_previous.json
    index.json
    S1xxxxxx/                # docID ごとのディレクトリ
      type1/
        document.zip        # API type=1 の ZIP（保存したまま）
        files/…             # ZIP を展開した iXBRL/HTML 等
      document.pdf          # type=2（存在する場合）
      attachments/          # type=3（存在する場合）
        attachments.zip
        files/…             # 添付書類を展開
```

`index.json` には収集時刻・docID・期間・連結区分・ハッシュなどのメタ情報が含まれます。

関連コマンド（概要）:
- 定量: `python -m src quant report --document output/<EDINET>/<日付_docID>` → `quant/*.csv`, `定量報告.md`
- 定性: `python -m src qual report --json output/<EDINET>/yuho_latest.json [--mode quick4]` → `定性報告.md`
- 外部: `python -m src external collect --json output/<EDINET>/yuho_latest.json` → `external/sources.md`
