# 推薦功能 V2 Spike：結果

[English](recommendation-v2-spike.md) · 設計文件：[recommendation-v2.zh-TW.md](recommendation-v2.zh-TW.md)

這是 [V2 計畫](recommendation-v2.zh-TW.md#7-工時與計畫)的步驟 0。2026-10-18 用 [`spike/recommendation_v2_spike.py`](../spike/recommendation_v2_spike.py) 對線上 Spotify API 實測，原始輸出在 [`spike/results.json`](../spike/results.json)。

## 重點摘要

- **受測的 app 沒有受到 2026 年 2 月 Development Mode 限制。** 設計文件列為「已移除」的端點和欄位全部還能用：搜尋 `limit=50`、批次 `/artists` 和 `/tracks`、`top-tracks`、`popularity`，以及讀取非自己擁有的公開清單的 items。只有 2024 年 11 月的關閉有生效。所以**對這個 Client ID 來說 V1 沒壞，步驟 1（V1 相容性修正）不需要做**。
- **E2E：8 個 MCP 工具透過真實的 stdio MCP client 全部通過。** `recommend_from_playlist` 對 77 首的範例清單，4.5 秒回傳 5 首推薦。
- **方案 C 可行。** 20 個種子中有 20 個至少觸發一種強訊號（C1–C3），沒有任何種子退回成「同藝人的其他歌」。20 個種子共 118 次呼叫。
- **方案 D 在這個 app 上也可行，但原始結果雜訊多。** 搜尋到的清單中有 27% 真的含有種子歌曲。前幾名候選被種子歌曲的翻唱版、以及單一藝人的清單汙染。這可以修（見下方）。
- **注意：結果只適用於這個 Client ID。** spike 用的是 SpotifyMCP2 專案在 Claude Desktop 設定中的憑證，它在 dashboard 上是什麼模式未知。依 Spotify 遷移指南，今天新建的 Development Mode app 應該仍會受限。採用這些結論前，請先到 *Developer Dashboard → app → Settings* 確認模式。

## 1. 端點測試（app token，Client Credentials）

| 測試 | 結果 | 設計文件預期 |
| --- | --- | --- |
| `search` `limit=10` | 200 | 200 |
| `search` `limit=50` | **200** | 400（2026 年 2 月上限） |
| `GET /tracks/{id}`、`/artists/{id}`、`/artists/{id}/albums` | 200 | 200 |
| 批次 `GET /artists?ids`、`/tracks?ids` | **200** | 已移除 |
| `GET /artists/{id}/top-tracks` | **200** | 已移除 |
| `GET /browse/new-releases` | **200** | 已移除 |
| `GET /recommendations` | 404 | 404 ✔ |
| `GET /artists/{id}/related-artists` | 404 | 404 ✔ |
| `GET /audio-features/{id}` | 403 | 403 ✔ |

欄位：`track.popularity`、`track.external_ids.isrc`、`track.available_markets`、`artist.popularity`、`artist.followers` 全部**都在**。Radiohead 的曲風：`art rock`、`alternative rock`。

## 2. 播放清單存取

| 情況 | 中繼資料 | `/tracks`（舊路徑） | `/items`（新路徑） |
| --- | --- | --- | --- |
| app token，別人的公開清單 | 200，內嵌 77 首 | 200 | 200 |
| app token，Spotify 官方編輯清單（Today's Top Hits） | 404 | 404 | 404 |
| user token，自己的清單 | 200 | 200 | 200 |
| user token，別人的公開清單 | 200，內嵌 77 首 | 200 | 200 |

兩種路徑都能用，所以 V1 目前呼叫 `/tracks` 沒問題。改用 `/items` 仍是提前因應改名的低成本做法。搜尋「radiohead」播放清單回傳 10 筆，其中 4 筆是 `null`，沒有 Spotify 官方清單。程式必須跳過 null；V1 不搜尋播放清單，所以不受影響。

## 3. 方案 C 涵蓋率（20 個跨語言、跨曲風的種子）

| 訊號 | 觸發的種子數 |
| --- | --- |
| 搜尋找到種子 | 20/20，但有 1 個找錯（見下方） |
| C1 合作藝人或 `appears_on` 作品 | 20/20 |
| 藝人有曲風 | 19/20（Childish Gambino 沒有） |
| C2 曲風＋年代歌曲搜尋有結果 | 17/20 |
| C3 曲風藝人搜尋有結果 | 9/20 |
| C4 自己的作品 | 20/20 |
| 只有 C4 觸發 | **0/20** |
| 成本 | 118 次呼叫，循序 18.4 秒（6 個並行約 3–4 秒） |

觀察：

- **找種子要用欄位篩選加精確比對。** 直接搜尋 `Blinding Lights The Weeknd` 回傳的是 Dua Lipa 的 *Don't Start Now*。應依設計文件的提議，用 `track:"…" artist:"…"` 並核對名稱。
- **C1 的 `appears_on` 每個都有觸發，但大多是合輯**（「Now That's What I Call…」、電影原聲帶）。C1 的權重應放在*共同掛名*的藝人上（只有 3/20 個種子有），並調降多藝人合輯中 `appears_on` 藝人的權重。
- **C3 很弱。** 用 `genre:` 搜尋藝人，`art rock`、`grunge`、`french house`、`mandopop` 都沒有結果，但 C2 用同樣曲風搜尋歌曲卻有結果。應降低權重或拿掉。
- C2 不平均：*Creep* 只有 1 首，*Smells Like Teen Spirit*、*One More Time* 是 0，流行、K-pop、J-pop、華語流行則都滿 10 首。

## 4. 方案 D：播放清單共現（10 個種子）

| 指標 | 數值 |
| --- | --- |
| 至少有一個可用清單的種子 | 10/10 |
| 搜尋到的清單中含有種子**歌曲** | 24/89（27%） |
| 搜尋到的清單中只含種子**藝人** | 32/89（36%） |
| 成本 | 109 次呼叫，循序 30.7 秒（6 個並行約 5–6 秒） |

不錯的前 5 名（尚未清理）：

- *Take Five*：Coltrane *Blue Train*、Branford Marsalis、三宅純。
- *Lose Yourself*：Coolio *Gangsta's Paradise*、Dr. Dre *Still D.R.E.*、JAY-Z。
- *倔強*：費玉清、王傑、瘦子E.SO、冰球樂團。

要修的失敗模式：

| 問題 | 例子 | 修法 |
| --- | --- | --- |
| 種子歌曲的翻唱和 remix | *Lemon*：前 5 名有 5 首是翻唱。*One More Time*：5 首都是 remix。 | 排除正規化後歌名和種子相同的候選 |
| 一份清單被單一藝人佔滿 | *Dynamite*：前 5 名全是 GiGi Waterproof | 限制每位藝人在單一清單中的貢獻，並要求候選至少出現在 2 份清單 |
| 只比對到名稱 | *Bohemian Rhapsody*：一份提到 Queen 的拉丁音樂清單 | 含種子歌曲的清單權重高於只含種子藝人的清單 |

## 5. E2E：每個 MCP 工具都走 stdio

| 工具 | 結果 | 時間 |
| --- | --- | --- |
| `list_tools` | ✅ 8 個工具 | — |
| `search_tracks`（limit 3 / limit 20） | ✅ 3 / ✅ 20 | 0.8 秒 / 0.4 秒 |
| `search_artists` | ✅ 3 | 0.3 秒 |
| `get_track` | ✅ | 0.1 秒 |
| `get_artist` | ✅ | 0.1 秒 |
| `get_artist_top_tracks` | ✅ 10 | 0.1 秒 |
| `get_album_tracks` | ✅ 3 | 0.1 秒 |
| `get_playlist_tracks` | ✅ 5 | 0.1 秒 |
| `recommend_from_playlist`（limit 5） | ✅ 5 | 4.5 秒 |

整次執行共 246 次 app token 呼叫、8 次 user token 呼叫，沒有任何 429。

## 6. 對設計文件的修正

| 設計文件說 | Spike 發現（這個 Client ID） |
| --- | --- |
| §3.2：V1 很可能壞了 | 沒壞。8 個工具全部通過。 |
| §3.2：沒有熱門度 | `popularity` 還在，可以再當同分時的排序依據。 |
| §4：方案 D 只限 Extended Quota | 這裡可用。這個 app *是不是* Extended Quota 未知。 |
| §6：方案 D 約 300–450 次呼叫 | 20 個種子、每個 10 份清單約 220 次，再加清理 |
| §5.2：C1 是最強訊號 | 共同掛名確實如此，但 `appears_on` 大多是合輯雜訊。C3 很弱。 |

## 7. 建議

1. **先到 Developer Dashboard 確認 app 的模式。** 如果是 Extended Quota，或其他不受限的模式，後面的路線就由它決定。
2. **如果不受限，用方案 D 當 V2**，清單資料不足的種子再用方案 C 的 C1/C2 當備援，並納入 §4 的三項修正。這最接近原始需求（「Spotify 使用者認為相似的歌」），不需要外部服務，而且候選本來就是 Spotify 歌曲。
3. **如果是受限的 Development Mode app**，只做方案 C（去掉 C3），也就是設計文件的建議。
4. 不論哪一種，步驟 1（V1 修正）先不做。改預設值前，先跑步驟 3 的離線評估（V1 vs. V2，recall@N）。

## 重現方式

```bash
SPOTIFY_CLIENT_ID=... SPOTIFY_CLIENT_SECRET=... \
  [SPOTIFY_REFRESH_TOKEN=...] uv run python spike/recommendation_v2_spike.py
```

腳本是唯讀的：只發 GET 呼叫，有提供 refresh token 時再加一次 token 更新。執行約 1 分鐘、約 250 次呼叫，會覆寫 `spike/results.json`。
