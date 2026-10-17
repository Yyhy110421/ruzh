# 自己接翻译引擎

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
