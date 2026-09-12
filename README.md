# creata-resume-skill

在聊天中一起完成简历：明确方向、挖掘真实优势、按职业选模板、评分改进，再生成可编辑正文与中英文 PDF。[English](docs/README.en.md)

## 使用

将**整个仓库**放入 `~/.codex/skills/creata-resume-skill`（包含字体和脚本），或在本地克隆后建立同名符号链接。不要只复制 `SKILL.md`。安装位置已有内容时先检查，避免覆盖。

```bash
git clone git@github.com:alexixu/creata-resume-skill.git ~/.codex/skills/creata-resume-skill
```

在新的 Codex 对话中使用：

> $creata-resume-skill 我想投产品经理，没有写好简历。请和我一起梳理经历，先从最重要的问题问起，最后做成一页中文 PDF。

也可以贴旧简历和 JD，要求局部改写、转行定位、英文版或完整美化。不用预先填写长问卷；每轮都会形成实际文稿。

## 能做什么

- 10 种职业/阶段模板：软件、数据算法、产品、设计、市场运营、销售客户成功、财务审计、项目职能、应届实习、转行。
- 真实经历账本、岗位要求映射、成果改写；没有数据也能表达交付价值，不编造成绩。
- 六维评价：目标匹配 25、证据可信度 25、成果价值 20、结构 10、表达 10、排版提取 10。每项说明依据；未评项不冒充总分。
- 两种版式：保留图标的 `classic` 和去装饰、顺序文本的 `plain`。生成 JSON、Markdown、TXT、LaTeX，以及有依赖时的 PDF。

方法参考两部经典求职/简历写作书籍的公开介绍与出版社配套材料，并结合 CareerOneStop 的格式建议；来源与改编边界见 [方法依据](references/sources.md)。评分是编辑工具，不预测录用或保证 ATS 通过。

## 本地生成

Python 3.9+；PDF 需要 `tectonic` 和 Poppler。在 macOS 可用 `brew install tectonic poppler`。

```bash
python3 scripts/resume.py roles
python3 scripts/resume.py init --role design --language zh --output /tmp/my-resume/profile.json
# 在聊天中补齐并确认 profile.json 后：
python3 scripts/resume.py render /tmp/my-resume/profile.json --out /tmp/my-resume/v1 --theme classic --pdf
```

默认一页 A4，未确认内容只能用 `--draft` 输出草稿。PDF 生成后还需要查看渲染图及文本顺序。[数据合约和完整步骤](references/delivery.md)

## 项目文件

| 入口 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | 聊天共创流程与资源导航 |
| [职业模板](assets/role-templates.json) | 10 种章节组合、证据追问和句式 |
| [评价量表](references/rubric.md) | 评分锚点、事实阻断项和改进报告 |
| [演示数据](assets/examples/) | 明确虚构的中英文例子 |
| [中文示例 PDF](dist/examples/zh-classic.pdf) / [英文示例 PDF](dist/examples/en-plain.pdf) | 已检查的 classic 与 plain 版式 |
| [验收记录](docs/verification.md) | 已完成的构建、测试、视觉检查及边界 |
| `scripts/resume.py` | 初始化、文本输出、LaTeX/PDF 构建与基础 QA |
| `resume.tex` / `resume-zh.tex` / `resume.cls` | 保留的原始中英文模板和共享样式 |

原始模板仍用 `make -B` 构建到 `dist/resume-en.pdf` 与 `dist/resume-zh.pdf`。运行 `python3 -m unittest discover -s tests -v` 检查工具。第三方字体与图标保留上游许可；个人简历放在公共仓库之外，不自动投递或上传。
