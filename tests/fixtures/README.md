# 検証用データ
これらは2026-10-16にwebで取得された公式本文・表の数値から再構成した**形式テスト用標本**です。原HTTP応答をそのまま保存したファイルではありません。現時点の市場データとして本番集約へ投入しません。

- CFTC: https://www.cftc.gov/dea/options/financial_lof.htm 取得版の観測日2026-08-04、6市場。
- MUFG: https://www.mufgresearch.com/fx/monthly-foreign-exchange-outlook-august-2026/ 公表2026-08-03、7/31基準。
- ING: https://think.ing.com/forecasts/ 「16 July」の年がないため方向集約から除外。
- 日銀: https://www.boj.or.jp/statistics/tk/yoshi/tk2606.htm 2026年6月調査・7/1公表。
- BLS: https://www.bls.gov/news.release/cpi.nr0.htm および https://www.bls.gov/news.release/ppi.nr0.htm 取得版は2026年6月。

本番ファイル内には、故障注入テストや合成市場データは入れません。直接取得できたとの誤表記を防ぐため、本番の初期台帳は未取得状態です。
