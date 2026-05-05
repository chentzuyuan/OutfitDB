# ClosetMind 插圖需求清單

App 走「無 emoji」風格，以下是 UI 上原本想用 emoji 但需要實際插圖的位置。建議用一致的 **單色線條 / minimal flat / monochrome** 風格保持視覺乾淨。

---

## 1. 5 個溫度區間 icon（最重要）

用在 `/training` 頁、未來可能也用在 `/settings` 顯示使用者的 `temp_offset`。

每個 ~32×32 或 64×64 SVG / PNG。命名建議：
- `temp-cold.svg` / `temp-cool.svg` / `temp-mild.svg` / `temp-warm.svg` / `temp-hot.svg`

| 區間 | 範圍 | 視覺方向（給繪圖 AI 的 prompt 建議） |
|---|---|---|
| **cold** | <10°C | 雪花 + 結冰的水滴；冷色（藍 #5b8cd0） |
| **cool** | 10–18°C | 風吹樹葉；微涼藍（#7bb6d9） |
| **mild** | 18–25°C | 太陽 + 一片雲；中性綠（#5fa372） |
| **warm** | 25–30°C | 滿太陽；暖橘（#e8a04a） |
| **hot** | >30°C | 太陽 + 熱浪線條 / 冒汗水滴；紅橘（#d65c40） |

**通用 AI prompt：**
> "Minimal flat icon, monospace single-color, 64×64 px, SVG, no text, no shadow, slight rounded corners, designed for a wardrobe app. Subject: [雪花 / 風中葉子 / 太陽和雲 / 強烈太陽 / 太陽加熱浪]. Color palette: [指定 hex]. Style: line + simple fill, no gradient."

---

## 2. 評分按鈕 icon（次要，目前用文字也夠）

用在 `/training` 與 `/recommend`。原本用 `🙁 / 😐 / 🙂 / 👎 / 👍 / ✏️`，現在改純文字。如果想要 icon：

- `rate-bad.svg` — 簡化的 frown / cross / thumb-down 線條
- `rate-meh.svg` — 水平直線 / 中性點
- `rate-like.svg` — 簡化的 smile / check / thumb-up 線條
- `rate-modify.svg` — 鉛筆或縫線
- `rate-reject.svg` — 小 X 或斜線

可以全部統一成抽象線條（不要表情）保持成人感。

---

## 3. Onboarding / Home hero（裝飾用）

`/setup` 與 `/` 首頁上方原本有 `✨` / 衣服 emoji。如果想做品牌感：

- `hero-wardrobe.svg` — 一個衣架掛三件衣服的剪影（單色 line art）
- `hero-trained.svg` — 「training complete」狀態的 icon（勾或徽章）

---

## 4. 衣物分類 icon（未來，現在不必）

在 `/closet` 或 `/upload` 的類別 dropdown 旁邊放小 icon：

- `cat-top.svg` — T 恤剪影
- `cat-bottom.svg` — 褲子剪影
- `cat-shoes.svg` — 鞋子側面
- `cat-accessory.svg` — 戒指 / 圍巾
- `cat-fullbody.svg` — 連身洋裝 / jumpsuit 剪影

---

## 命名 + 放置位置

放在 `app/static/icons/` 已存在的資料夾，與 PWA 主 icon 並列：

```
app/static/icons/
├── icon-192.png         (PWA)
├── icon-512.png         (PWA)
├── apple-touch-icon-180.png
├── temp-cold.svg        ← 新加
├── temp-cool.svg
├── temp-mild.svg
├── temp-warm.svg
├── temp-hot.svg
└── ...
```

接到 UI 的地方：在 `i18n.js` 加路徑映射，或直接在 `training.html` 的 `zoneLabel()` 改成：

```js
return `<img src="/static/icons/temp-${z.key}.svg" alt="${z.key}" /> ${labelText}`;
```

---

## 想自己畫：建議 dimensions / 風格

- 64×64 px (或 SVG vector)
- 單一主色 + 灰階輔助色
- 沒有純白 / 純黑邊框（讓 light/dark mode 都能用）
- 線條 stroke ~2px
- 圓角風格（避免太銳利）

---

## 想用 AI 生：推薦工具

- DALL·E 3 / Midjourney（主視覺、hero）
- recraft.ai（icon set 同風格批次生成）
- iconscout / heroicons（直接用現成的 free SVG icon set）
- Figma + Iconify plugin（如果你有 Figma）

如果只想要 5 個溫度 icon，最快是上 [iconify.design](https://iconify.design) 找既有的 weather icon set（如 `wi-snow / wi-strong-wind / wi-day-cloudy / wi-day-sunny / wi-hot`），直接下載 SVG 套上去。
