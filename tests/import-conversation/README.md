# 旧简历导入：实际执行的虚构三轮记录

这是一次**同模型定向模拟**：主代理逐轮扮演虚构用户，响应代理先读取真实 PDF 并交付本轮结果，再收到下一轮输入。响应代理没有预写后续用户答案，也没有读取生成 PDF 的原始 JSON。它不是实际求职者研究、盲测、独立模型评测或招聘结果证据。

## 文件与执行顺序

- `fictional-old-resume.pdf`：1 页 A4、全部虚构的原始输入；指纹见 `record.json`。
- `automatic-before-layout-fix/`：真实运行曾因通用 PDF 阅读顺序警告将多数内容留作未归类。保留这次自动结果，未用人工整理替换。
- `automatic-round1/`：修正分类条件后，在新目录重新导入同一 PDF 的真实输出。22 个原文片段，1 项页首职位仍待归类。
- `round-1-response.md` 至 `round-3-response.md`：依次交付的实际回复，包含完整整理稿、改写、追问、故事及方向。回合原文也保存在 `record.json`。
- `current-resume.json`：第三轮形成的当前渲染器输入，`facts_confirmed: false`；使用 `--draft`。
- `current-evidence.json`：当前正文的 23 个字段引用、两段故事、三方向依据、撤回关系与仍待复核事项。

第一轮已对照原 PDF 全页图像读取，给完整整理稿、前三项改进和三个问题。第二轮按用户纠正撤回“主导、3 家、30%”，更新为经理定字段、本人制作模板与检查、2 家每周约三个月，并继续提供正文。第三轮加入门店编号漏填、日期格式不一和异常样表检查，选定产品运营主方向，直接完成草稿且不再提问。

姓名、联系信息、公司、正式职位、时间和教育一直保留为 `source_only`；用户允许沿用不等于确认。旧 `claim-000007` 保留在历史来源中并标为 `superseded`，不会用于当前正文、故事或方向。未知上线与业务效果没有被补成成果。

## 复现与边界

从仓库根运行，`$CASE_OUTPUT` 应指向仓库外的私有临时目录：

```bash
python3 scripts/import_resume.py tests/import-conversation/fictional-old-resume.pdf --out "$CASE_OUTPUT/imported"
python3 scripts/resume.py render tests/import-conversation/current-resume.json --out "$CASE_OUTPUT/current-draft" --theme plain --draft
```

当前代码的自动建议可能随修复改变；已保存的自动结果是当时输出，不能覆盖成现在的结果。第一轮实际人工回复未采用自动建议中由“销售报表”词语误导的客户成功/销售支持方向。后两轮与当前正文是代理结合用户答复的人工语义整理，不是 CLI 自动产物。

本次进行了窄范围结构与来源核对：渲染器草稿格式、PDF 指纹、原文位置、逐轮回复一致、用户原话引用与时序、当前字段可定位、当前故事和方向不引用已撤回事实、未触及身份事实仍待复核、无私有绝对路径。它们不检验写作质量、方向正确性或真实求职成效，也不是对其他格式/OCR 路径的验证。

公开快照对真实临时目录前缀作占位替换，并将 Markdown 行末双空格规范为等效的反斜杠换行；原话与事实状态保持不变，不含真实求职者数据。记录结束时没有生成当前 PDF，所以历史 `final_pdf_generated: false` 和 `NOT_RUN` 保留。后续开发构建与页面验收单独记录，不回填为对话当时已经完成的工作。
