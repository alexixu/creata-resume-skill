# 共享事实与多版本同步

只改两句话时直接编辑即可。需要维护中英文或多个岗位版本时，可使用标准库工具 `scripts/facts.py`。真实事实、来源、简历及输出目录均放在公开 skill 仓库之外；工具不会上传任何文件。

## 私有工作区

将虚构演示 `assets/examples/fact-workspace.json` 复制到用户选择的私有目录，再替换为本轮访谈确实得到的内容。演示含同一门店报表经历的运营 / 产品 × 中文 / 英文四个版本。

合同版本为 `schema_version: 1`，只接受已声明的字段：

- `facts`：每项有稳定 `id`、记录原话与来源位置的 `source`、`status`（`confirmed` / `pending`）、唯一 `value`（字符串或整数）和 `expressions`（`zh` / `en`）。`confirmed` 表示用户已确认或已读来源支持，不代表独立事实核实。
- `versions`：每项有单段小写 `id`、使用渲染器现有格式的 `resume` 模板、`bindings`、`summary` 和 `basis`。
- `bindings`：`path` 必须是模板中可显示的字符串字段，例如 `sections[1].entries[0].bullets[0]`。用 `template` 与 `refs` 显式关联事实 ID；同一路径不能绑定两次，不能绑定整个对象。不会猜测近似字段名或执行字符串替换。
- `summary`：同样以 `template` / `refs` 生成的当前版本摘要。
- `basis`：包含 `requirement`、`template`、`refs` 的 JD 证据理由。输出记录事实 ID、来源、状态与关联正文位置；它不自动打分，也不证明事实真伪。

计数只保存一次：

```json
{"id":"store_count", "source":"本轮回答 F03：纠正为 2 家门店。",
 "status":"confirmed", "value":2,
 "expressions":{"zh":"{value}", "en":"{value}"}}
```

需要不同语言措辞时，用取值查表，职责只保存一次 `value: "contributor"`：

```json
{"zh":{"lead":"主导", "contributor":"参与"},
 "en":{"lead":"Led", "contributor":"Contributed to"}}
```

正文绑定示例：

```json
{"path":"sections[1].entries[0].bullets[0]",
 "template":"{responsibility}报表模板整理，覆盖 {stores} 家门店。",
 "refs":{"responsibility":"responsibility", "stores":"store_count"}}
```

普通模板仅支持命名 token，不支持属性访问、转换或格式表达式；未引用的 `refs` 也会报错，避免事实绑定被悄悄丢弃。用 `{{` / `}}` 表示文字花括号。

## 检查、纠正与同步

以下 `$SKILL_ROOT` 是当前 `SKILL.md` 所在的真实 skill 根目录；`$case_dir` 是用户私有目录。使用绝对路径后可以从任意工作目录调用，不能选仓库或包含输入文件的父目录作为 `--out`。

```bash
python3 "$SKILL_ROOT/scripts/facts.py" check "$case_dir/fact-workspace.json"
python3 "$SKILL_ROOT/scripts/facts.py" sync "$case_dir/fact-workspace.json" --out "$case_dir/versions"
```

每次同步都创建全新 `revision-000001` 等目录，生成各版本 `resume.json` / `.md` / `.txt`、`summary.json` / `.txt`、`basis.json` 与 `status.json`。修订根目录保留完整 `workspace.json` 事实台账和模板快照；`manifest.json` 列出全部修订与版本状态。

若用户纠正“3 家门店 → 2 家门店”“主导 → 参与”，只修改事实的 `value`，同步更新 `source` 为新的回答位置、根据回答更新 `status`，再次执行 `check` 和 `sync`。四个当前版本中所有显式绑定的正文、摘要、JD 理由都会重新生成。旧修订的全部版本 `status.json` 与修订状态标为 `STALE`，记录替代修订；旧正文和手工改动不会覆写或删除。

**同步范围是显式绑定的字段。** 未绑定的静态内容或旧导出中的手工添加不会自动修改或继承，必须人工核对；需要后续同步的新增表述，应先回写私有模板并建立事实绑定。事实存在待确认项时仍可生成带草稿标记的版本，证据文件保留其状态。

`CURRENT` 仅表示最新生成，不表示已核实或已批准投递。每次同步默认 `NEEDS_REVIEW`，正文标为草稿，`facts_confirmed` 置为 `false`；不会沿用旧整份简历的终稿批准。只有用户实际复核本次事实及所有版本后，才使用：

```bash
python3 "$SKILL_ROOT/scripts/facts.py" sync "$case_dir/fact-workspace.json" --out "$case_dir/versions" --confirmed-after-review
```

该选项仍要求每个模板 `facts_confirmed: true`、所有被引用事实 `status: "confirmed"`，并对正文、生成摘要与 JD 证据理由应用相同的终稿占位检查；选项不会替用户确认事实。`requirement` 是 JD 原文上下文，其中原本出现的 TODO 等词不是候选人断言，不按简历占位词处理。若要 PDF，选定新修订中的 `resume.json` 使用常规 `resume.py render` 流程，完成页面、视觉及阅读顺序验收。PDF 也应保存在对应版本目录或与其明确关联，以便交付时检查相邻 `status.json`。

非空且无工具标记的目录、路径穿越、版本 ID 中的路径、仓库输出、指向外部文件的历史元数据软链接都会被拒绝。发布元数据前备份旧清单和状态；若发布失败，恢复这些元数据并移除仅本次生成的新修订，保留输入、旧正文与手工改动，之后可再次同步。若底层 I/O 连恢复也无法完成，工具明确报告恢复失败，保留目录供恢复，不宣称清单已经一致。强制杀进程或机器断电不属于该异常回滚保证；若中断留下 `.sync.lock`，先确认没有同步进程、检查清单与状态，再恢复，或选新的私有输出目录。
