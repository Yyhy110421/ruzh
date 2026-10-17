"""示例引擎：用一个词表做翻译。

文件名以 `_` 开头，所以它不会被自动加载 —— 想启用就把名字改成
`example_dictionary.py`，或者照这个格式写你自己的。
"""

NAME = "dictionary-demo"
LABEL = "示例 · 词表翻译"
HINT = "演示插件怎么写：只查一张表，查不到就原样返回"
LANGUAGES = ["en", "zh"]          # 不填表示所有语言对

TABLE = {
    "hello": "你好",
    "world": "世界",
    "good morning": "早上好",
}


class DictionaryTranslator:
    def load(self):
        pass                      # 这里可以加载模型、连数据库、建 HTTP 会话…

    def translate(self, text: str) -> str:
        key = text.strip().lower()
        if key in TABLE:
            return TABLE[key]
        # 逐词替换，长词优先，避免 "good morning" 被拆坏
        out = text
        for src in sorted(TABLE, key=len, reverse=True):
            out = out.replace(src, TABLE[src])
        return out

    def describe(self) -> str:
        return f"词表条目数：{len(TABLE)}"


def create(cfg, models_root):
    """必需。返回一个带 translate(text) 的对象。"""
    return DictionaryTranslator()
