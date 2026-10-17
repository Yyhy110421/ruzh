"""Loading translation engines the user dropped into a folder.

``engines_custom/`` sits next to ``main.py`` - deliberately *outside* the ``app``
package, so updating the app never overwrites someone's own engine.

A plugin is a plain ``.py`` file declaring:

.. code-block:: python

    NAME = "my-engine"                 # required, unique id
    LABEL = "我的引擎"                  # shown in the dropdown
    HINT = "用我自己的模型"              # one line under the dropdown
    LANGUAGES = ["en", "zh"]           # optional; omit to accept every pair

    def SUPPORTS(src, tgt):            # optional, for asymmetric pairs
        return src == "en"

    def create(cfg, models_root):      # required
        return MyTranslator()          # needs translate(text); load() optional

Loading is deliberately forgiving: a plugin with a syntax error becomes a row in the
engine list that says what is wrong, rather than a crash at startup.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from .base import CustomEngineAdapter, EngineError, EngineSpec

#: Set on every plugin module so a plugin can find the project root.
PLUGIN_DIR_NAME = "engines_custom"


@dataclass
class LoadProblem:
    path: str
    message: str
    detail: str = ""


def engine_dir(project_root: str | Path) -> Path:
    return Path(project_root) / PLUGIN_DIR_NAME


def _load_module(path: Path):
    name = f"screen_translator_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def discover(project_root: str | Path) -> tuple[list[EngineSpec], list[LoadProblem]]:
    """Return the engines found in the plugin folder, plus anything that failed."""
    folder = engine_dir(project_root)
    specs: list[EngineSpec] = []
    problems: list[LoadProblem] = []

    # The folder is created with its documentation and a disabled example, so a user
    # who has never opened it still finds the contract spelled out there.
    write_example(project_root, folder=folder)

    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = _load_module(path)
        except Exception as exc:  # noqa: BLE001
            problems.append(LoadProblem(str(path), f"导入失败：{exc}", traceback.format_exc()))
            continue

        create = getattr(module, "create", None)
        if not callable(create):
            problems.append(LoadProblem(str(path), "缺少 create(cfg, models_root) 函数"))
            continue

        engine_id = str(getattr(module, "NAME", "") or path.stem)
        label = str(getattr(module, "LABEL", "") or engine_id)
        hint = str(getattr(module, "HINT", "") or f"来自 {path.name}")
        languages = [str(x) for x in (getattr(module, "LANGUAGES", None) or [])]
        supports_fn = getattr(module, "SUPPORTS", None)

        def supports(src, tgt, _fn=supports_fn, _langs=languages):
            if callable(_fn):
                try:
                    return bool(_fn(src, tgt))
                except Exception:  # noqa: BLE001
                    return False
            if _langs:
                return src in _langs and tgt in _langs
            return True

        spec = EngineSpec(
            id=engine_id, label=label, kind="custom", hint=hint,
            origin=str(path), supports=supports, languages=languages,
        )

        def factory(cfg, models_root, _spec=spec, _create=create):
            # A plugin object only has to provide translate(); the adapter supplies
            # load/set_beams/describe and the route wrapper the pipeline expects.
            from ..translator import TranslationRoute

            target = _create(cfg, models_root)
            if target is None:
                raise EngineError(f"引擎 {_spec.id} 的 create() 返回了 None")
            adapter = CustomEngineAdapter(target, _spec, cfg.source_lang, cfg.target_lang)
            return TranslationRoute([adapter], label=adapter.label, is_pivot=False)

        spec.factory = factory
        specs.append(spec)

    return specs, problems


def write_example(project_root: str | Path, folder: Path | None = None) -> Path:
    """Create the plugin folder with a documented example, if it is missing."""
    folder = folder or engine_dir(project_root)
    folder.mkdir(parents=True, exist_ok=True)
    readme = folder / "README.md"
    if not readme.exists():
        readme.write_text(_README, encoding="utf-8")
    example = folder / "_example_dictionary.py"
    if not example.exists():
        example.write_text(_EXAMPLE, encoding="utf-8")
    return folder


_README = """# 自己接翻译引擎

把任意 `.py` 文件放进这个目录，重启程序（或点「重新扫描」），它就会出现在
主界面的「翻译引擎」下拉框里。

这个目录**故意放在 app 包外面**，所以升级程序不会覆盖你写的引擎。

## 最小例子

```python
NAME = "my-engine"
LABEL = "我的引擎"

def create(cfg, models_root):
    return MyTranslator()          # 需要有 translate(text) -> str
```

## 可以声明的变量

| 变量 | 必需 | 说明 |
| --- | --- | --- |
| `NAME` | 是 | 唯一 id，不填就用文件名 |
| `LABEL` | 否 | 下拉框里显示的名字 |
| `HINT` | 否 | 下拉框下面那行说明 |
| `LANGUAGES` | 否 | 例如 `["en", "zh"]`；不填表示所有语言对都接 |
| `SUPPORTS(src, tgt)` | 否 | 需要更细的判断时用，返回 True/False |
| `create(cfg, models_root)` | 是 | 返回翻译器对象 |

## 翻译器对象

只需要一个方法：

```python
def translate(self, text: str) -> str
```

可选：

```python
def load(self)                 # 初始化（加载模型、建立连接等）
def set_beams(self, n)         # 不适用就忽略
def describe(self) -> str      # 调试信息
```

`create()` 里抛异常不会让程序崩溃，界面会显示具体原因。
"""

_EXAMPLE = '''"""示例引擎：用一个词表做翻译。

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
'''
