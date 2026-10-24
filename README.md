# RuZh · 守译

A desktop translator for **Chinese ↔ Russian trade**. Screen, Word, Excel, PDF and
images all run through the same offline OCR and the same terminology glossary.
Everything executes on your machine — no text ever leaves it.

**Русско-китайский переводчик для торговли.** Экран, Word, Excel, PDF, изображения —
всё через один офлайн-OCR и один глоссарий терминов. Всё работает локально.

---

## English

### What it does

Select a window or a screen region. RuZh keeps capturing it, runs offline OCR, translates,
and lays the translation out **at the original position** of each text block — columns stay
columns, and the font shrinks to fit each box.

| Entry | What happens |
|---|---|
| **Screen** | Continuous capture → OCR → translation → placed at source positions |
| **Word (.docx)** | Whole document translated, formatting preserved |
| **Excel (.xlsx)** | Every cell with text translated, formulas untouched |
| **PDF** | Text layer when present, page OCR when scanned |
| **Image** | Photo, screenshot, scan — OCR then translate |

### Built for trade paperwork

- **Numbers and money are protected.** Digits are masked before the model sees them, so
  amounts cannot drift. When the source mentions roubles, the output cannot say "RMB".
- **Terminology glossary.** Pin the translation of product names, company names and
  industry terms.
- **Output repair.** Currency symbols, thousands separators and duplicated characters are
  normalised after the model answers.

### Engines

| Engine | Speed | Quality | Network |
|---|---|---|---|
| **Local LLM** (Qwen2.5-1.5B via llama.cpp) | ~4 s per block | Good for UI and everyday text | Offline |
| **OPUS-MT** (ru→en→zh pivot) | 0.4–0.8 s per block | Word-level, gist only | Offline |
| **OpenAI-compatible** (DeepSeek, Moonshot, Ollama, LM Studio, vLLM…) | Network bound | Best | Needs a key |
| **Custom** | — | Drop a Python file into `engines_custom/` | — |

### Requirements

- Windows 10 / 11, 64-bit, 8 GB RAM recommended
- No Python, no installer: the release package carries its own runtime, models and OCR

### Honest limits

- The bundled 1.5B model reads UI, menus, chat and everyday text well. **Contract clauses
  still need a human check** — switching to an API engine closes that gap.
- A full screen of about 12 text blocks takes roughly 45–50 seconds with the local model.
- The floating window is opt-in; the translation appears in its own panel.

---

## Русский

### Что это

Выделите окно или область экрана. RuZh постоянно снимает эту область, распознаёт текст
офлайн, переводит и размещает перевод **на месте каждого исходного блока**: колонки
остаются колонками, шрифт уменьшается под размер блока.

Документы идут тем же путём: Word (.docx), Excel (.xlsx), PDF, изображения — один OCR,
один глоссарий, одна защита чисел.

### Для торговых документов

- **Числа и суммы защищены.** Цифры маскируются до того, как их увидит модель.
- **Глоссарий терминов.** Перевод названий товаров и компаний можно задать жёстко.
- **Постобработка.** Символы валют, разделители разрядов и дубли символов нормализуются.

### Движки перевода

| Движок | Скорость | Качество | Сеть |
|---|---|---|---|
| **Локальная LLM** (Qwen2.5-1.5B) | ~4 с на блок | Интерфейсы и обычный текст | Офлайн |
| **OPUS-MT** (ru→en→zh) | 0.4–0.8 с на блок | По словам, только для смысла | Офлайн |
| **OpenAI-совместимый** (DeepSeek, Ollama, LM Studio…) | Зависит от сети | Лучшее | Нужен ключ |

### Требования

- Windows 10 / 11, 64 бита, рекомендуется 8 ГБ ОЗУ
- Python не нужен: пакет несёт собственный runtime, модели и OCR

### Ограничения

- Модель 1.5B хорошо читает интерфейсы и переписку. **Пункты договора нужно проверять
  человеку** — этот разрыв закрывается подключением API-движка.
- Полный экран примерно из 12 блоков переводится за 45–50 секунд.

---

## 中文

中俄贸易用的桌面翻译工具。屏幕、Word、Excel、PDF、图片，五条入口共用同一套离线 OCR
和同一份术语库，全部在本机跑，内容不外发。

- **屏幕翻译**：框选窗口或区域，译文按原文每个文字块的位置排布，分栏不变，字号缩到框里
- **文档翻译**：Word 保排版、Excel 只动文字、PDF 有文字层走文字层、图片和扫描件走识别
- **数字与金额保护**：模型看不到原始数字，金额不会漂；原文有卢布，译文不会出现人民币
- **术语表**：品名、公司名、行业术语可以强制指定
- **引擎可选**：本地模型（离线）、OPUS-MT（离线，快但粗糙）、任意 OpenAI 兼容接口（最好，需 key）

**诚实的边界**：内置 1.5B 模型读界面、菜单、聊天没问题，**合同条款仍需人工复核**；
一屏 12 块约 45–50 秒。

---

## License

MIT — see [LICENSE](LICENSE).
