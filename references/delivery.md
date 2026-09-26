# 数据与交付

以下路径相对 skill 根目录；先解析当前 `SKILL.md` 所在真实目录为 `SKILL_ROOT`，不要假设用户的工作目录就是 skill。脚本路径用绝对路径。工具只用 Python 标准库；PDF 另需 tectonic 和 Poppler（pdfinfo、pdftotext、pdftoppm）。Tectonic 首次构建可能下载 TeX 包，简历内容不需要提交到在线转换网站。

## 最短可执行路径

```bash
python3 "$SKILL_ROOT/scripts/resume.py" roles
python3 "$SKILL_ROOT/scripts/resume.py" init --role product --language zh --output "$OUTPUT_ROOT/profile.json"
# 将已经在聊天中确认的内容填入 profile.json，再生成新版本目录：
python3 "$SKILL_ROOT/scripts/resume.py" render "$OUTPUT_ROOT/profile.json" --out "$OUTPUT_ROOT/v1" --theme classic --pdf
# 顺序文本版使用 --theme plain；未确认草稿使用 --draft，并带有显式草稿标记。
```

输出目录必须是新目录或空目录，防止覆盖旧简历。相同数据可换 theme 或语言内容生成不同版本。`--max-pages 2` 仅用于用户明确要求两页；默认一页。字体和本地 LaTeX 支持文件随输出复制，移动整个目录后仍可编译。

## JSON 合约

```json
{
  "language": "zh",
  "role": "product",
  "name": "用户确认的姓名",
  "headline": "用户确认的目标岗位与定位",
  "contacts": ["用户选择的联系方式或公开作品链接"],
  "facts_confirmed": false,
  "sections": [
    {
      "id": "experience",
      "title": "工作经历",
      "entries": [
        {
          "heading": "公司名称 / 正式职位",
          "date": "起止年月",
          "bullets": ["已经在聊天中确认的行动与成果"]
        }
      ]
    }
  ]
}
```

`sections` 数组决定实际顺序，可增删自定义章节。空章节自动略过。`heading`、`date` 可省略；概览和技能可以只填 bullets。所有文本是纯文本，不能嵌 LaTeX、HTML 或 Markdown；脚本转义特殊字符。链接以可读文本显示，不自动生成超链接。

输入错误会给出从零开始的字段路径，如 `sections[1].entries[1].bullets[2]`。普通 `【项目名称】` 可作正文；`【待确认：…】`、`【待填：…】`、职业句式中的 `【结果】` 等已知待填词，以及 TODO/TBD/YYYY 仍不能进入终稿。空章节的标题不参与正文占位符检查。粘贴的 CRLF 换行与其他空白一样会规范化为空格。

生成的 LaTeX 使用 `literaltext` 类选项，保留英文撇号、`--dry-run` 等双连字符原文。姓名可自然换行；未填写姓名的草稿省略姓名标题，保留草稿标记。

PDF 中的 `http(s)://`、`www.` 和 `example.com/portfolio` 这类链接可在行末折行，保留原始字符，不增加断词横线或可点击链接。Markdown 保留 `&copy;` 等字面文本，防止数字或横线开头的条目标题变成额外列表/分隔线；定位语和每条联系信息分别显示一行。TXT 仍保留原有纯文本结构。

`facts_confirmed` 是代理在用户确认关键事实或采用保守已知表述后记录的状态，不是脚本证明真伪。不能为绕过检查自动设为 true。事实账本保存在单独的 `evidence.md`，不嵌入简历输出。脚本拒绝常见占位符，但无法检测所有虚构说法，仍需人工内容审查。

## 文件与验证

生成 `resume.json`、`resume.md`、`resume.txt`、`resume.tex` 和依赖文件；`--pdf` 额外生成 `resume.pdf`、`resume-extracted.txt`、`build.log`、`qa.json`。PDF 页数、A4、文本存在性、可见字段缺失和构建溢出警告由脚本检查。PDF QA 失败返回非零；PDF 可能已生成但尚不可交付为验证通过。

`qa.json` 的 `status: PASSED/FAILED` 仅表示自动检查结果，`stage` 和 `completed_stages` 表示当前及已执行阶段。`max_pages` 记录允许页数，`failure_reasons` 列出失败原因；缺失正文同时保留原有 `missing_text_indices` 并提供可定位的 `missing_text_fields`。重复构建警告只列一次。`PASSED` 不代表事实确认、人工视觉检查或 ATS 认证。

缺少依赖、编译或提取失败时，仍保留可编辑文件，并在 `build.log` 留存错误；报告的 `error` 和失败阶段用于定位问题。未运行的检查不填结果。文本比对允许拉丁单词在行末断词，保留负号与原有连字符的差别。

继续运行：

```bash
pdftoppm -png -r 160 "$OUTPUT_ROOT/v1/resume.pdf" "$OUTPUT_ROOT/v1/review"
```

查看每一页图片：无裁切、无重叠、章节层次一致、正文可读、字体及图标正确。阅读 `resume-extracted.txt` 检查姓名、联系信息、章节和每段日期的顺序。`qa.json` 的 `visual_review` 和 `reading_order_review` 初始为 `NOT_RUN`，仅在实际检查后记录 `PASS` 或 `FAIL` 并附简短说明。不可自动把构建成功等同视觉或 ATS 验证。

如果页面过满，先删重复/弱相关句子和冗长摘要，再调整生成目录的 `resume.cls` 间距。不要直接修改公共模板保存某个用户信息。公共仓库的演示仅使用虚构资料；真实输出放用户指定的私有目录，未经请求不提交或上传。

用户需要 Word：使用可用文档工具从确认的正文生成 DOCX，再做版面检查；本脚本不提供 DOCX。环境缺依赖则交付正文及可编辑源码并明确缺失的验证步骤。
