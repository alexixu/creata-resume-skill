# 验收记录

日期：2026-09-12。本记录区分工具验证、视觉检查与尚未进行的真实用户试用。

| 层级 | 结果 |
| --- | --- |
| GitHub 仓库改名 | 已读取远端确认 `alexixu/creata-resume-skill`；本地 origin 同步 |
| Skill 格式 | skill-creator `quick_validate.py` 通过；本地技能目录符号链接指向本仓库 |
| 工具行为 | `python3 -m unittest discover -s tests -v`：9/9 通过 |
| CLI 串联 | init → 未确认草稿 → Markdown/TXT/LaTeX/字体依赖输出通过 |
| 模板结构 | 10 个职业模板 × 中英文初始化验证通过 |
| 原始模板 | `make -B` 成功；两份 PDF 均为一页 A4，字体、图标和文字已看图检查 |
| 新版 PDF | 中英文 × classic/plain 四种组合均成功，均为一页 A4，无缺失正文、溢出或缺字警告 |
| 视觉与阅读顺序 | 四个新示例均查看完整页面和提取文本，正文/日期顺序可读；classic 提取包含装饰图标，plain 无此装饰 |
| 页数失败路径 | 构造 100 条要点的示例生成 3 页，工具返回非零并记录页数失败 |
| 文档 | 内部链接有效，元数据和 workflow YAML 可解析 |
| 远端 CI | 新 workflow 已写入，尚未 push，未声称 GitHub Actions 已运行 |
| 真实聊天效果 | 尚未进行真实求职者端到端访谈或独立模型评测；验收输入见 `tests/chat-scenarios.md` |

可复现示例：

```bash
python3 scripts/resume.py render assets/examples/zh.json --out /tmp/resume-check-zh --theme classic --pdf
python3 scripts/resume.py render assets/examples/en.json --out /tmp/resume-check-en --theme plain --pdf
```

目录须为空或不存在。检查 `qa.json` 与 `resume-extracted.txt`，再用 `pdftoppm` 渲染和查看所有页。脚本的初始视觉状态仍为 `NOT_RUN`，本次人工查看后才在交付示例的 QA 记录中标为 `PASS`。

过程中发现英文断词以及等价分号 Unicode 编码造成误报，已增加规范化处理与回归测试，同时保留负号检查。生成正文采用左对齐，避免英文两端对齐产生过大的词间空隙。

Tectonic 保留了使用系统 Times New Roman 的环境可重复性提示；图标字体也有上游 glyph-name 提示，实际图标显示正常。固定时间戳不能保证跨字体版本二进制一致。本次原始 dist PDF 重建后无 Git 内容差异。

公开示例完全虚构，不能套用为真实求职者经历。质量分不是 ATS 认证或录用概率；本地文字抽取不代表所有招聘系统的解析行为。
