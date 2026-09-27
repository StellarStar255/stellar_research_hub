# Research Hub —— 和 Claude 一起读论文、做实验

**中文** | [English](#english)

一个用 Python + PyQt6 写的桌面论文研究助手。导入一篇论文（本地 PDF 或 arXiv），
左边看原文，右边和 Claude 一问一答地学：它读过整篇 PDF，回答带出处页码（点一下阅读器就跳过去）；
想验证论文里的说法，就让它写个小实验在你电脑上跑，画出图来一起看。

> 架构仿照 [Smart Table Hub](../stellar_smart_table_quick_analysing_hub)：PyQt6 界面、后台线程调用 Claude、
> API Key 存系统钥匙串、崩溃不退出只记日志。

## 快速开始（网页版，推荐，零设置）

```bash
./start.sh        # 自动打开 http://localhost:8767
```

回答用你电脑上已登录的 Claude Code（`claude` 命令），**不需要 API Key、不需要任何设置**。
左上角输入 arXiv 编号（如 1706.03762）或把 PDF 拖进窗口，就可以开始问。

- 左边论文库，中间 PDF，右边对话；回答里的 `p.3` 可点，PDF 跳到那一页；公式用 LaTeX 渲染
- **研究主题**：左上角输入一个主题（如「长上下文 LLM 的位置编码外推」），它会自己搜 arXiv / 网页、挑 4–8 篇重要论文下载进库、
  读完写综述报告（脉络、关键论文对比、阅读顺序、开放问题），存在「📋 报告」里；报告里的论文名可点，中间直接打开，
  「精读这篇 →」切到单篇精读。它只被允许运行 `./rh search / cite / import` 三个命令
  - **先出计划再动手**：新主题第一轮只给出子方向和候选论文表（引用数、会议、入选理由、备选），
    点「✅ 按这个计划开始」或直接说修改意见后才下载（没确认前 `rh import` 会拒绝）
  - **选论文有依据**：搜索结果附 Semantic Scholar 引用数 / 高影响引用 / 年均引用 / 会议。免费额度大家共用，
    偶尔限流会显示「引用数未查到」；想稳定可以申请免费 key，设 `SEMANTIC_SCHOLAR_API_KEY=...` 再启动
- **历史项目**：主题和论文的对话、报告、笔记都自动保存，重启后点左边就回到上次（Claude 也记得之前读过的内容）。
  鼠标移到条目上：✎ 重命名（顶栏标题双击也行）、🗄 归档（收进列表底部「已归档」）、× 删除
- **导出**：「📋 报告 / 📝 笔记」抽屉里 ⬇ Markdown 下载 .md（论文链接换成 arXiv 网址）；🖨 PDF 打开打印版，选「存储为 PDF」
- 让它做实验：它把代码写进 `experiments/`，回答里出现「▶ 运行」按钮，你点了才执行；
  输出和图自动发回给它解读。Claude 自己没有执行命令的权限，写文件也只限 `experiments/` 和 `notes.md`
- 环境变量：`RESEARCH_AGENT_MODEL=sonnet` 回答更快；`RESEARCH_PYTHON=/path/to/python` 指定跑实验的解释器
- 代码在 `web/`（server.py、agent.py、agent_prompt.md、static/index.html），和桌面版共用 `core/` 与论文库

## 桌面版（PyQt6，需要 API Key）

```bash
pip install -r requirements.txt
python3 stellar_research_hub.py            # 或 ./run_qt.sh
python3 stellar_research_hub.py paper.pdf  # 启动时直接导入一篇
```

1. 菜单 **设置 → 设置…** 填 Anthropic API Key（留空则用环境变量 `ANTHROPIC_API_KEY`），顺便写一句「我的背景」
   （例如「研一，熟悉 PyTorch，概率论一般」），Claude 会据此调整讲解深度。
2. 左下角 **＋ PDF** / **＋ arXiv** 导入论文（也可以直接把 PDF 拖进窗口）。arXiv 支持编号、abs/pdf 链接、HF Papers 链接。
3. 在右边「对话」页提问，或点快捷按钮。

## 功能

### 💬 来回提问
- **整篇 PDF 给 Claude 读**（含图表、公式），不是只抽文字
- **引用页码**：回答里的 `p.3` 是出处，点击后阅读器跳到对应页；「引用本页」按钮把当前页号带进问题
- **四种风格**（每个对话开始时选）：
  - 导师（苏格拉底式）：一次讲一个概念，讲完反问你检验理解，答错先给提示——适合真正学会
  - 讲解：直接、完整地解释，多用类比和数值例子
  - 速读：要点列表，先判断值不值得细读
  - 实验：尽量用可运行的小实验回答
- **快捷提问**：三分钟速览 / 带我精读 / 拆解核心方法 / 考考我 / 设计小实验 / 批判性评价 / 整理笔记
- **多个对话**：同一篇论文可以开多个对话（比如一个精读、一个专门做实验），都自动保存
- **可选联网**：允许 Claude 搜索相关工作、后续论文、官方代码（设置里开关）

### 🧪 做实验
- Claude 通过 `run_python` 工具写代码，在**你的电脑上**运行（工作目录：这篇论文的 `experiments/`，文件跨运行保留）
- 输出和图片回传给 Claude——它能**看到自己画的图**并解读；图也直接显示在对话里和「实验」页
- `plt.show()` 会自动存图；报错行号和代码对得上；超时 / 点「停止」会杀掉进程
- **默认每次运行前弹窗确认**（看一眼代码，可以写理由拒绝）；可以对某个对话选「本次对话都直接运行」，或在设置里全局关闭确认
- 设置里可以把解释器指向你自己的 conda 环境（装了 torch 等）

### 📝 笔记
- 每篇论文一份 Markdown 笔记，自动保存，可切换预览
- 让 Claude「把要点记到笔记里」，它会用 `save_note` 追加进去

### 💰 省钱
- 论文 PDF 打 1 小时的提示缓存，整段历史也自动缓存：追问时论文不重复计费
- 对话顶部显示本对话的估算花费和缓存命中率

## 数据放在哪

```
~/.stellar_research_hub/library/<论文>/
    paper.pdf  meta.json  notes.md
    sessions/*.json          # 对话（完整的 API 消息历史）
    experiments/             # 实验工作目录
        .runs/               # 每次运行的代码存档
        .artifacts/          # 回传给 Claude 的图
```

环境变量 `RESEARCH_HUB_HOME` 可以把整个目录换到别处（比如同步盘）。

## 发给 Anthropic 的内容

提问时会发送：当前论文的 PDF、这个对话的历史、实验代码的输出和图片。笔记不会自动发送（除非你让 Claude 读）。

## 快捷键

- `⌘/Ctrl+Enter` 发送；`Esc` 停止生成
- `⌘/Ctrl+N` 新对话；`⌘/Ctrl+L` 聚焦输入框
- `⌘/Ctrl+O` 导入 PDF；`⌘/Ctrl+Shift+O` 从 arXiv 导入

## 模型

默认 `claude-opus-5`（自适应思考，思考深度可在设置里调）。
Opus 5 / Fable 5.1 开启了服务端 fallback：主模型偶尔误拒时自动换备用模型继续。
可选 `claude-sonnet-5`（更便宜）、`claude-fable-5-1`（最强）、`claude-haiku-4-5`（最便宜，不支持思考）。

## 开发

```bash
python3 -m pytest          # 41 个测试：agent 回合（假 Claude 客户端）、实验沙箱、arXiv、论文库、渲染、界面端到端
```

代码结构：
- `core/`（不依赖 Qt）：`library.py` 论文库、`arxiv.py` 下载、`sandbox.py` 实验运行、`agent.py` 对话回合与工具、`render.py` 历史→Markdown
- `qtui/`：`main_window.py`、`chat_panel.py`、`pdf_panel.py`、`library_panel.py`、`side_panels.py`（笔记/实验）、`settings.py`、`workers.py`（后台线程）

一个约束：**对话历史只追加、不修改**。系统提示词、工具列表、论文标题都在对话创建时定下；
中途改它们会让提示缓存失效，服务端也会拒绝历史里的 thinking 块。想换风格就开新对话。

## 已知限制

- 对话区不渲染 LaTeX（提示词要求 Claude 用 Unicode 写公式，长推导放代码块）
- 阅读器不支持划词；需要划词/批注时点阅读器右上角 ↗ 用系统阅读器打开
- 单篇 PDF 上限约 30MB（API 单次请求 32MB）
- 实验代码不是在沙箱里跑的，权限和你本人一样——这就是默认要确认的原因

---

## English

A PyQt6 desktop app for studying research papers with Claude. Import a PDF or arXiv paper,
read it on the left, and ask Claude about it on the right: Claude reads the full PDF and its
answers cite page numbers (click one to jump there). Ask it to run a small experiment and it
writes Python, runs it on your machine (after you approve), looks at the output and plots, and
explains the result. Per-paper notes, multiple conversations per paper, four tutoring styles, and
prompt caching so follow-up questions don't re-bill the paper. The UI is Chinese-only for now.

```bash
pip install -r requirements.txt
python3 stellar_research_hub.py
```

MIT License.
