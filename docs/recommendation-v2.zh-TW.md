# 推薦功能 V2：只用 Spotify 資料的推薦——設計與可行性評估

[English](recommendation-v2.md)

狀態：**提案階段，尚未寫任何程式碼**。最後依 Spotify 官方文件核對日期：2026-10-18。

## 1. 需求

做一個 `recommend_from_playlist` 的 V2，**資料來源只用 Spotify**：

1. 輸入一份播放清單或一串歌曲。
2. 逐首到 Spotify 上查詢。
3. 把 Spotify 認為「相似」的歌收集起來當候選。
4. 為候選排序，回傳前 N 首。

V1（現行版本）的相似度訊號來自 Deezer 和 Last.fm，再用藝人名稱對回 Spotify。V2 則完全不用這兩個外部服務。

## 2. 重點摘要

| 問題 | 答案 |
| --- | --- |
| 能不能直接問 Spotify「哪些歌跟這首相似」？ | **不能。** 能回答這個問題的 Spotify 端點，我們的 app 全部無法使用（§3.1）。 |
| 那還能做只用 Spotify 的 V2 嗎？ | **可以，但訊號比較弱。** 可以用合作藝人、作品目錄、曲風、年代來組。品質會比 V1 差（§4，方案 C）。 |
| 有沒有完全符合需求的純 Spotify 做法？ | **有，但只限 Extended Quota Mode**，本專案實際上申請不到。做法是統計哪些歌常一起出現在公開播放清單裡（§4，方案 D）。 |
| 有沒有更優先的問題？ | **有。** Spotify 2026 年 2 月的 Development Mode 變更，很可能連 V1 都有部分壞掉（§3.2）。開始 V2 前應先確認。 |
| 建議 | 先做 1 天的 spike（§7）。接著修 V1 相容性。再用方案 C 做 V2，以 `source` 參數切換，預設仍用 V1 的混合模式。 |
| 工時 | spike 1 天。方案 C 總共 4–6 人天。方案 D 再加約 3–4 天，但前提是拿到 Extended Quota。 |
| 金錢成本 | API 費用 0 元。app 擁有者需要 Spotify Premium（2026 年起 Development Mode 的硬性要求）。不需要新增基礎設施。 |

## 3. 可行性：Spotify 實際上讓我們呼叫什麼

### 3.1 相似度端點已關閉（2024 年 11 月）

2024-11-27 起，Spotify 對該日之後建立的 app，以及沒有待審延伸申請的 Development Mode app，關閉了以下功能（[公告](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)）：

| 端點／功能 | 原本能為 V2 提供什麼 |
| --- | --- |
| `GET /recommendations` | 依種子歌曲找相似歌曲。這正是需求要的東西。 |
| `GET /artists/{id}/related-artists` | Spotify 的「粉絲也喜歡」 |
| `GET /audio-features`、`/audio-analysis` | 節奏、能量、情緒值等，可用來依聲音特徵排序 |
| 演算法生成與 Spotify 官方編輯的播放清單 | 歌曲電台（Song Radio）、「This Is …」、每日精選、官方編輯歌單都回傳 404 |
| `GET /browse/featured-playlists`、`/browse/categories/{id}/playlists` | 官方精選的候選池 |

Spotify app 裡的「相似歌曲」和「歌曲電台」，背後就是這些已關閉的系統。公開的 Web API 沒有其他途徑能取得。

### 3.2 Development Mode 再度收緊（2026 年 2 月／3 月）

之後 Spotify 又收緊了一次 Development Mode。新規則從 2026-02-11 起套用到新建的 app，2026-03-09 起套用到既有 app（[遷移指南](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)）。這些變更同時影響 V1 和 V2：

| 變更 | 對我們的影響 |
| --- | --- |
| `GET /playlists/{id}/tracks` 改名為 `/playlists/{id}/items` | V1 的 `_playlist_tracks` 還在呼叫舊路徑。 |
| 播放清單的 `items` **只對使用者擁有或共同編輯的清單回傳**，其他清單只回傳中繼資料，沒有 items。 | Client Credentials 流程沒有「使用者」，所以很可能讀不到任何播放清單的歌曲。這會讓 V1 的輸入步驟失效，方案 D 也因此不可行。 |
| 移除 `GET /artists/{id}/top-tracks` | V1 用它挑歌，`get_artist_top_tracks` 這個工具也是。 |
| 移除批次查詢 `GET /artists?ids=…`、`/tracks?ids=…` | V1 用它取得播放清單的曲風。現在每個 ID 都要各打一次。 |
| 搜尋 `limit` 上限從 50 降到 10（預設從 20 降到 5） | `search_tracks`、`search_artists` 接受最多 50，所以超過 10 的值現在會收到 400。 |
| 歌曲、專輯、藝人移除 `popularity`，藝人移除 `followers` | 無法再用熱門度排序。`_track_summary` 已經能處理沒有 `popularity` 的情況。 |
| app 擁有者必須有 Spotify Premium | 持有 Client ID 的人要持續付費。 |

**注意：** 這個環境沒有憑證，無法實際呼叫線上 API。我們的 CI 測試全部是 mock，所以抓不到上述任何一項。我們的 app 會不會受影響，取決於它的模式（Development 或 Extended Quota），所以這是 spike（§7）第一個要確認的事。

### 3.3 Extended Quota Mode 實際上拿不到

Extended Quota Mode 不受 2026 年 2 月的限制。但從 2025-05-15 起，它只開放給「已具規模、可擴展且具影響力的使用情境」，實務上等於需要有大量使用者的公司行號（[審核標準更新](https://developer.spotify.com/blog/2025-04-15-updating-the-criteria-for-web-api-extended-access)）。就算今天拿到 Extended Quota，§3.1 的端點仍然不能用。那些端點只保留給 2024 年 11 月以前就已經在用的 app。

### 3.4 Development Mode + Client Credentials 仍可使用的

以下是純 Spotify 版 V2 能用的材料：

- `GET /search`：`type=track|artist|album|playlist`，每頁最多 10 筆，用 `offset` 翻頁。歌曲搜尋支援欄位篩選 `artist:`、`album:`、`year:`（可用區間，例如 `year:2005-2010`）、`genre:`。
- `GET /tracks/{id}`：單首歌曲，包含完整的演出藝人名單（featuring 和合作者）。
- `GET /artists/{id}`：單一藝人，包含 `genres`（很多藝人是空的）。
- `GET /artists/{id}/albums`：藝人的專輯、單曲和 `appears_on`（客串參與）作品。
- `GET /albums/{id}`：專輯和前 50 首歌。
- `GET /playlists/{id}`：只有中繼資料，除非是自己擁有的清單。

### 3.5 不考慮：非官方端點

網頁版播放器會用從已登入瀏覽器取得的 token，呼叫內部端點（「歌曲電台」、「粉絲也喜歡」、「相似歌曲」）。使用這些端點違反 Spotify 開發者條款（禁止逆向工程與爬取），可能導致 Client ID 或帳號被封，而且 Spotify 一改就會無預警壞掉。**不列入範圍。**

## 4. 方案比較

| | A. Spotify 相似度端點 | B. 非官方網頁播放器 API | C. Spotify 原生訊號 | D. 播放清單共現 |
| --- | --- | --- | --- | --- |
| 符合需求（「Spotify 的相似歌曲」） | 完全符合 | 完全符合 | 部分符合 | 接近（群眾共識的相似度） |
| Development Mode 可用 | ❌ 已關閉 | ⚠️ 違反條款 | ✅ | ❌ 讀不到別人清單的歌曲 |
| Extended Quota 可用 | ❌ 僅限 2024 年前的 app | ⚠️ 違反條款 | ✅ | ✅ |
| 探索性（能挖到新藝人） | 高 | 高 | **低到中** | 高 |
| 需要跨服務比對名稱 | 否 | 否 | 否 | 否 |
| 結論 | 不可行 | 否決 | **建議採用** | 只在有 Extended Quota 時考慮 |

## 5. 演算法

### 5.1 共用流程

```
輸入   ──►  種子  ──►  候選產生  ──►  評分  ──►  多樣化  ──►  前 N 首
(清單或     (≤20 首,    (一或多種訊號;      (加權      (依種子輪流,
 歌曲列表)   逐首查詢)   每個候選都是         投票)       每位藝人設上限)
                         Spotify 歌曲 ID)
```

**輸入。** V2 接受播放清單，或一串歌曲。每首歌可以是歌曲 URL/URI/ID，或 `"歌名 - 藝人"` 字串。歌曲列表完全繞開播放清單 items 的限制，所以在 Development Mode 下應該是主要用法。播放清單只有在 app 擁有該清單、或 app 有 Extended Quota 時才讀得到。

**種子。** 把每首歌對應到一首 Spotify 歌曲：

- ID：打一次 `GET /tracks/{id}`；
- 字串：打一次 `search type=track`，查詢條件為 `track:"…" artist:"…"`，取正規化後完全相符的第一筆。

種子上限 `MAX_SEEDS = 20`，和 V1 的成本上限相同。長清單依藝人出現次數挑種子，也就是 V1 的挑法，讓清單的主要藝人排在前面。

**多樣化與輸出。** 沿用 V1 的 `spread()` 概念。每個候選都有一個「主導種子」（投它最多票的種子）。種子輪流出牌，每位藝人最多 `tracks_per_artist` 首。排除種子歌曲本身，並移除 ISRC 相同的重複歌曲（`external_ids` 已在 2026 年 3 月恢復）。沒有 ISRC 時，改用正規化後的「歌名 + 主要藝人」當鍵值。每首推薦都保留 `because` 欄位，列出是哪些種子帶出它的。

### 5.2 方案 C：Spotify 原生訊號（建議）

對每首種子歌曲 `s`（藝人 `a`、發行年 `y`、藝人曲風 `G`），從四種訊號收集候選，每種訊號有權重 `w`：

| 訊號 | 做法 | 每個種子的呼叫數 | `w` | 用意 |
| --- | --- | --- | --- | --- |
| **C1 合作藝人** | `s` 上掛名的其他藝人，加上 `a` 的 `appears_on` 作品中的藝人（`/artists/{a}/albums?include_groups=appears_on`），取他們最新專輯的歌 | 1–3 | 1.0 | 和種子藝人合作過的藝人，是 Spotify 目前還公開的訊號中最強的相似度訊號。 |
| **C2 曲風＋年代搜尋** | 對 `G` 中前 1–2 個曲風做 `search type=track q=genre:"g" year:(y-3)-(y+3)`，各取 10 筆 | 1–2 | 0.7 | 同一個樂壇、同一個時期。`G` 為空時跳過。 |
| **C3 曲風藝人搜尋** | `search type=artist q=genre:"g"`，再取每位藝人的最新專輯 | 2–3 | 0.5 | 補到 C1、C2 漏掉的藝人。 |
| **C4 同藝人的其他作品** | `/artists/{a}/albums`，取種子所在專輯以外的歌 | 1–2 | 0.3 | 冷門種子的保底。探索性低，所以權重低。 |

評分：

```
score(c) = Σ_種子  weight(s) · Σ_訊號  w_訊號 · rank_decay(c, 訊號, s)
rank_decay = 1 - 名次 / 清單長度                    # 和 V1 相同的規則
weight(s)  = s 的藝人在輸入中出現的次數                # 和 V1 相同
加成       × 1.3：c 被 ≥2 個不同種子帶出（共識）
懲罰       × 0.5：c 的藝人已經在輸入中（偏好探索）
```

Spotify 移除了熱門度欄位，所以沒有熱門度可當排序依據。同分時先比不同種子數，再比發行日期（新的優先）。

**已知弱點。**

- 曲風標籤稀疏又籠統。很多藝人沒有任何曲風，這時 C2/C3 什麼都回不來。
- 沒有合作藝人、也沒有曲風時，結果會退回 C4（同一位藝人的其他歌）。
- 預期結果會比 V1 用 Deezer/Last.fm 的訊號保守。比較像「同一個樂壇和周邊」，而不是「X 的粉絲也愛 Y」。

### 5.3 方案 D：播放清單共現（僅限 Extended Quota）

這是以公開的使用者播放清單做 item-to-item 協同過濾，也是我們能自己做出、最接近「Spotify 相似歌曲」的方法。

```
對每個種子 s：
    playlists = search(type=playlist, q='"{s.歌名}" {s.藝人}', 最多 2 頁 = 20 個)
              + search(type=playlist, q='{s.藝人}', 1 頁 = 10 個)
    對每個清單 p（跳過 null、Spotify 官方、超過 500 首的巨型清單）：
        items = GET /playlists/{p}/items（每頁 ≤100，最多讀 2 頁）
        若 s 和 s 的藝人都不在 items 中：捨棄 p          # 搜尋只比對到名稱
        對 items 中每首 c ≠ s：
            co[c][s] += 1 / log2(2 + len(items))         # 清單越長，每首的權重越低

score(c) = Σ_s weight(s) · co[c][s] / sqrt(df(c))   # df = 含有 c 的清單數；壓低太大眾的歌
```

- 播放清單搜尋比對的是清單的*名稱與描述*，不是清單裡的歌。所以程式要確認種子真的在清單裡。通過這個檢查的清單比例目前未知，spike 必須實測。
- 候選本來就是 Spotify 歌曲物件，不需要比對名稱。這也消除了 V1 最主要的失敗原因。

## 6. 成本與效能

以下估算假設 20 個種子、6 個並行工作（V1 的設定）、每次 Spotify 呼叫約 200 ms。Spotify 不公開它的速率限制。它以 30 秒滾動視窗計算呼叫次數，超過就回 `429` 並附 `Retry-After` 標頭（[文件](https://developer.spotify.com/documentation/web-api/concepts/rate-limits)）。所以**真正要看的成本是請求數**。

| | V1（現行） | V2 方案 C | V2 方案 D |
| --- | --- | --- | --- |
| 每次 Spotify 呼叫數 | 約 60（另有約 40 次 Deezer/Last.fm） | **約 120–200** | **約 300–450** |
| 延遲（估計） | 約 8 秒（2026 年變更前實測） | 約 6–10 秒 | 約 15–25 秒 |
| 429 風險 | 低 | 中。需要處理 `Retry-After`。 | 高。需要快取和退避。 |
| 外部依賴 | Deezer（非商業條款）、Last.fm key（選用） | 無 | 無 |
| 金錢 | 0 元 | 0 元 | 0 元，但需要 Extended Quota |
| 基礎設施 | 無 | 無。行程內 TTL 快取為選用。 | 強烈建議加 TTL 快取。 |

持續成本：

- app 擁有者的 Spotify Premium 訂閱。2026 年起 Development Mode 就有這個要求，不論出哪個版本。
- 2026 年 2 月後新建的 Development Mode app 最多 5 位使用者，但這個限制只影響 Authorization Code（使用者登入）流程。

降低成本的方法：

- `GET /artists/{id}`、`/artists/{id}/albums` 和搜尋結果快取 24 小時。不同種子間藝人重複率很高。
- 候選數達到 3×N 就提早停止收集。
- 加上遵守 `Retry-After` 的 `429` 退避。目前 `_api` 遇到第一個 `429` 就直接拋錯。

## 7. 工時與計畫

| 步驟 | 內容 | 工時 |
| --- | --- | --- |
| **0. Spike（先做）** | 用真實憑證確認 app 的模式。呼叫新舊兩種播放清單路徑、`top-tracks`、批次 `/artists`、`limit=50` 的搜尋。確認非自己擁有的公開清單是否回傳 `items`。抽 20 個種子，記錄有多少有曲風或合作藝人（方案 C 的涵蓋率）。若是 Extended Quota，也實測共現命中率（方案 D）。 | 1 天 |
| 1. V1 相容性（另開 PR） | 只在 spike 確認壞掉時做：改用 `/items`、搜尋上限改 10、取代 `top-tracks` 和批次 `/artists`、加上 `429` 退避。 | 1–2 天 |
| 2. V2 方案 C | 新工具 `recommend_v2(songs \| playlist, limit, tracks_per_artist)`，或在現有工具加 `source="spotify"` 參數。包含輸入解析、四種訊號、評分、多樣化，以及沿用現有測試風格的 respx mock 測試。 | 2–3 天 |
| 3. 離線評估 | 拿幾份自己擁有的清單，保留 20% 當答案，用剩下 80% 推薦，比較 V1 和 V2 的 recall@N、藝人多樣性與新穎度。結果決定預設值。 | 1 天 |
| 4. 快取 | 藝人、專輯、搜尋查詢的行程內 TTL 快取 | 0.5 天 |
| — 方案 D | 只在有 Extended Quota 時做：清單搜尋、驗證、共現評分，加上快取 | +3–4 天 |

**建議路線合計：** 步驟 0、2、3、4 共 4–6 人天，若需要步驟 1 再加 1–2 天。

## 8. 風險

| 風險 | 可能性 | 對策 |
| --- | --- | --- |
| 方案 C 品質不如 V1 | 高 | 作為選項上線，不取代 V1。由步驟 3 的數據決定預設值。 |
| Spotify 繼續砍 Development Mode 功能 | 中（16 個月內已經砍了兩次） | 訊號模組化，某個訊號失效只少一個訊號。保留 V1 的混合模式。 |
| 大量輸入引發大量 429 | 中 | 限制種子數、加快取、依 `Retry-After` 退避。 |
| 曲風稀疏導致 C2/C3 沒有結果 | 冷門或非英語藝人偏高 | C1、C4 仍會回傳候選。回傳 `because`，讓使用者看得到推薦理由。 |
| 使用者期待跟 Spotify app 的「相似歌曲」一樣 | 高 | 在文件中清楚說明公開 API 不提供。 |

## 9. 待決事項

1. 核准 1 天的 spike，需要真實的 Client ID 和 secret。
2. spike 之後選一個：
   - **V2 = 方案 C**，和 V1 並存（建議），或
   - 若 spike 顯示方案 C 涵蓋率太差，放棄 V2，只修 V1。
3. 方案 D 只在專案日後拿到 Extended Quota 時才考慮。

## 參考資料

- [Changes to the Web API (2024-11-27)](https://developer.spotify.com/blog/2024-11-27-changes-to-the-web-api)
- [Updating the criteria for Web API extended access (2025-04-15)](https://developer.spotify.com/blog/2025-04-15-updating-the-criteria-for-web-api-extended-access)
- [February 2026 Web API Dev Mode changes: migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Update on developer access and platform security (2026-02-06)](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security)
- [Rate limits](https://developer.spotify.com/documentation/web-api/concepts/rate-limits)
