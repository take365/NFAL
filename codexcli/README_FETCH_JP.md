# codexcli edinet fetch 使い方

`python -m codexcli edinet fetch` コマンドは、EDINET API v2 (`https://api.edinet-fsa.go.jp/api/v2`) から直近2期分の有価証券報告書（docTypeCode=120）を取得し、指定ディレクトリへ JSON 形式で保存します。ドキュメントの選定・抽出ロジックは `design.txt` に従っています。

## 事前準備

```env
APIKEY=... # .env に EDINET API のキーを設定
```

## 実行例

```bash
python -m codexcli edinet fetch --edinet E05907
```

※ v2 API の書類一覧は `documents.json?date=YYYY-MM-DD&type=2&Subscription-Key=...` 形式で日付ごとに取得するため、既定の 3 年レンジでは日数分のリクエストが走ります。必要に応じて `--from` / `--to` で期間を絞ってください。

オプション:
- `--from`, `--to` : 期間指定 (YYYY-MM-DD)。既定は今日から遡って3年〜今日。
- `--prefer` : 同一期の連結/個別が併存する場合の優先 (`consolidated` or `separate`)。
- `--outdir` : 出力先ディレクトリ（既定値 `output`）。

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

`index.json` には収集時刻・docID・ハッシュなどのメタ情報が含まれます。
