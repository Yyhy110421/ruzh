# app/data 里的第三方数据

这个目录里的数据文件不是本项目原创，各自的授权如下。**商用前请保留本文件与本节的署名。**

---

## zh_ru_dict.db —— 中俄词典

- **来源**：[FreeDict](https://freedict.org/) 的 `zho-rus`，版本 2025.11.23，156,590 个词条
- **上游**：[WikDict](http://www.wikdict.com/) 基于 [Wiktionary](https://www.wiktionary.org/)
  经 [DBnary](http://kaiko.getalp.org/about-dbnary/) 生成
- **维护者**：Karl Bartel
- **授权**：[CC BY-SA 3.0 Unported](https://creativecommons.org/licenses/by-sa/3.0/)
- **由本项目的 `scripts/build_dictionary.py` 转换**（清理重音符号与维基标记，并建立俄→中反向索引）

### 这份授权意味着什么

- ✅ **可以商用**：CC BY-SA 不禁止商业使用
- ✅ **需要署名**：必须保留上面的来源与作者信息，本文件就是署名
- ⚠️ **ShareAlike（相同方式共享）**：这份**词典数据本身**（以及由它派生的
  反向索引）如果再次分发，仍须以 CC BY-SA 3.0 或兼容授权发布。
  它作为独立的数据文件随程序一起分发，**不会**要求你的程序代码开源。

如果要去掉这个约束，可以在构建时不生成这个文件：删除 `app/data/zh_ru_dict.db`
即可，程序会自动跳过词典功能。

---

## ru_words.txt —— 俄语词表与词频

- **来源**：[wordfreq](https://github.com/rspeer/wordfreq) 3.1.1 的俄语词频数据
- **授权**：wordfreq 以 MIT / CC BY-SA 4.0 双重授权发布，其词频数据源自
  [OPUS](https://opus.nlpl.eu/)、[OpenSubtitles](https://www.opensubtitles.org/)、
  Wikipedia 等公开语料
- **由本项目的 `scripts/build_wordlist.py` 导出**（取前 20 万词及其词频）

用途：恢复俄语识别结果里被丢掉的词间空格。

---

## trade_glossary.json —— 中俄外贸词库

- **来源**：本项目**实测生成**，不是复制来的现成词库
- **做法**：`scripts/build_trade_glossary.py` 用离线引擎逐条翻译 65 个外贸术语，
  记录它实际译成了什么，再针对译错的部分生成纠正规则（共 124 条）
- **授权**：与本项目相同（MIT），可自由修改

这个文件是**可以直接编辑**的普通 JSON。你在程序里添加的规则会覆盖它。
---

## ocr/cyrillic_PP-OCRv3_rec/ —— 西里尔文字识别模型

- **来源**：PaddleOCR 的 cyrillic_PP-OCRv3_rec（PP-OCRv3 多语种识别模型），
  转成 ONNX 供 RapidOCR / onnxruntime 使用
- **授权**：[Apache-2.0](https://github.com/PaddlePaddle/PaddleOCR/blob/release/2.7/LICENSE)，
  与 PaddleOCR 自身一致
- **文件**：model.onnx（8.6 MB，识别网络）+ dict.txt（163 行字符集）

**为什么放在包里而不是让用户下载**：没有它，俄文会被当成相似字母认出来
（Встреча → BcTpe4a），整个程序的用途就没了。8.6 MB 不值得在第一个用户面前
放一道坎。自带的 dict.txt 同时含拉丁和西里尔字母，所以识别结果会混写，
app/ocr_engine.py 里的 fold_to_cyrillic 负责归一。

翻译模型（688 MB）不在仓库里，用 scripts/setup_models.py 下载；那部分的授权见
根目录 README.md 的「第三方」一节。
