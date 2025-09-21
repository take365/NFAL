# Web 検索プレイブック（外部情報との突合）

このドキュメントは、有報の記載と外部情報（適時開示・会社IR・ニュース等）の整合確認を効率化するための手順とテンプレートをまとめたものです。

## 目的とアウトプット
- 目的: 有報で触れられる重要イベント（第三者委員会、特別調査委員会、訂正報告書、KAM 等）について、一次/二次ソースを確認し、要点を要約して定性報告へ反映する。
- 保存物:
  - `output/<EDINET>/external/sources.md`（最低限の記録）
  - 必要に応じて PDF のURL/取得日時（ローカル保存は任意）

## 推奨ソース
- Nikkei TDNR（適時開示の PDF リンクがページ内 `pdfLocation` として付与）
  - 例: `https://www.nikkei.com/nkd/disclosure/tdnr/<ID>/`
- TDnet 直接リンク（release.tdnet.info）
- kabutan / irbank（開示転載やリンク集）
- 会社サイトの news/information（プレスリリース、社内お知らせ）

## 検索クエリ雛形
- 「<社名> 第三者委員会 調査報告書 <YYYY年M月D日>」
- 「<社名> 特別調査委員会 調査報告書 <YYYY年M月D日>」
- 「<社名> 内部統制 報告書 訂正 <YYYY年M月D日>」
- 「TDnet <証券コード> <YYYY年M月>」
- サイト限定（状況に応じて）
  - `site:nikkei.com/nkd/disclosure/tdnr`、`site:release.tdnet.info`、`site:kabutan.jp`、`site:irbank.net`、会社ドメイン（news/information）

## 記録テンプレ（sources.md）
以下を `output/<EDINET>/external/sources.md` に追記:

```
# 外部情報ソース（<社名> / <期>)
取得日時: YYYY-MM-DDTHH:MM:SS+09:00

- クエリ: 「<検索語>」
  - URL: <リンク>
  - タイトル: <ページタイトル>
  - 要点: <1〜2行で要約>
  - 整合性: ○/△/×（有報のどの記述と一致/差異か）
```

## 参考: その場で結果を列挙する簡易 Python スニペット
DuckDuckGo 検索結果（HTML版）から上位リンクを抽出します。`jq` などは不要です。

```
python3 - << 'PY'
import sys, urllib.parse, urllib.request, re
ua = 'Mozilla/5.0'
queries = [
  '<社名> 第三者委員会 調査報告書 <YYYY年M月D日>',
  '<社名> 特別調査委員会 調査報告書 <YYYY年M月D日>',
  '<社名> 内部統制 報告書 訂正 <YYYY年M月D日>',
  'TDnet <証券コード> <YYYY年M月>'
]
for q in queries:
    print('\n###', q)
    url = 'https://duckduckgo.com/html/?q=' + urllib.parse.quote(q)
    req = urllib.request.Request(url, headers={'User-Agent': ua})
    with urllib.request.urlopen(req, timeout=20) as res:
        html = res.read().decode('utf-8', errors='ignore')
    results = re.findall(r'<a rel="nofollow" class="result__a" href="([^"]+)">([^<]+)</a>', html)
    for link, title in results[:8]:
        print('-', re.sub(r'<[^>]+>', '', title), '|', link)
PY
```

## レポートへの反映
- `output/<EDINET>/定性報告.md` の「外部情報との突合」セクションに、
  - 出典URL／要点／整合性コメントを簡潔に記載
  - 追加の確認が必要な場合は「フォローアップ」へ記録

## 注意事項
- PDF のローカル保存は任意。リンク切れに備え、URL と取得日時の記録を必須化。
- 検索エンジンの仕様変更でスニペットが不安定化することがあるため、最終確認は必ずリンク先で実施。
- 組織ポリシー上の制約（プロキシ・社内ネットワーク等）がある場合は、事前に環境条件を明記。
