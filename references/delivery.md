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

`facts_confirmed` 是代理在用户确认关键事实或采用保守已知表述后记录的状态，不是脚本证明真伪。不能为绕过检查自动设为 true。事实账本保存在单独的 `evidence.md`，不嵌入简历输出。脚本拒绝常见占位符，但无法检测所有虚构说法，仍需人工内容审查。

## 文件与验证

生成 `resume.json`、`resume.md`、`resume.txt`、`resume.tex` 和依赖文件；`--pdf` 额外生成 `resume.pdf`、`resume-extracted.txt`、`build.log`、`qa.json`。PDF 页数、A4、文本存在性、可见字段缺失和构建溢出警告由脚本检查。PDF QA 失败返回非零；PDF 可能已生成但尚不可交付为验证通过。

继续运行：

```bash
pdftoppm -png -r 160 "$OUTPUT_ROOT/v1/resume.pdf" "$OUTPUT_ROOT/v1/review"
```

查看每一页图片：无裁切、无重叠、章节层次一致、正文可读、字体及图标正确。阅读 `resume-extracted.txt` 检查姓名、联系信息、章节和每段日期的顺序。`qa.json` 的 `visual_review` 和 `reading_order_review` 初始为 `NOT_RUN`，仅在实际检查后记录 `PASS` 或 `FAIL` 并附简短说明。不可自动把构建成功等同视觉或 ATS 验证。

如果页面过满，先删重复/弱相关句子和冗长摘要，再调整生成目录的 `resume.cls` 间距。不要直接修改公共模板保存某个用户信息。公共仓库的演示仅使用虚构资料；真实输出放用户指定的私有目录，未经请求不提交或上传。

用户需要 Word：使用可用文档工具从确认的正文生成 DOCX，再做版面检查；本脚本不提供 DOCX。环境缺依赖则交付正文及可编辑源码并明确缺失的验证步骤。
