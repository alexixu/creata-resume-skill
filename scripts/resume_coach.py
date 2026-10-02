#!/usr/bin/env python3
"""Source-linked editorial prompts for an imported resume, not a truth or fit score."""
import re


ROLE_CLUES = {
    'software': ('软件工程', 'Software engineering', r'开发|实现|测试|调试|deploy|implement|debug|built|API|Swift|Python|JavaScript', '实现范围、技术取舍与验证方式', 'implementation scope, tradeoffs and validation'),
    'data': ('数据分析', 'Data analysis', r'数据|分析|报表|SQL|Excel|dataset|analysis|analy[sz]|report|dashboard', '数据口径、分析方法与结论应用', 'data definitions, analysis methods and use of findings'),
    'product': ('产品协作', 'Product work', r'用户|需求|反馈|优先级|产品|user|requirement|feedback|prioriti|product', '问题定义、优先级决策与效果验证', 'problem definition, prioritization and outcome validation'),
    'design': ('产品与体验设计', 'Product / UX design', r'设计|交互|原型|可用性|design|prototype|usability|Figma', '作品、设计决策与用户验证', 'portfolio, design decisions and user validation'),
    'marketing': ('市场与增长运营', 'Marketing / growth', r'活动|渠道|内容|投放|增长|campaign|channel|content|growth|marketing', '受众、渠道投入与可归因结果', 'audience, channel investment and attributable results'),
    'sales': ('客户成功与销售支持', 'Customer success / sales support', r'客户|续费|负责销售|销售拓展|销售谈判|销售支持|签约|client|customer|renewal|sold\b|sales\s+(?:support|cycle|negotiation|pipeline)|contract', '客户范围、个人负责环节与业务结果', 'customer scope, personal responsibility and business outcomes'),
    'finance': ('财务与审计', 'Finance / accounting', r'财务|审计|账目|会计|finance|audit|accounting|ledger', '账目范围、复核准确性与资质要求', 'account scope, accuracy checks and credentials'),
    'operations': ('项目与流程运营', 'Operations / project work', r'流程|协调|交付|模板|协作|process|coordinat|deliver|template|workflow', '流程约束、协调对象与验收标准', 'process constraints, stakeholders and acceptance criteria'),
}
NUMBER = re.compile(r'\d+(?:\.\d+)?\s*(?:%|％|家|人|次|倍|hours?|days?|users?|clients?)|提升|增长|降低|improv|increas|reduc', re.I)
OWNERSHIP = re.compile(r'主导|独立|全面负责|led\b|lead\b|owned\b|solely', re.I)
VAGUE = re.compile(r'负责|参与|协助|responsible|participat|assist|help', re.I)
NEGATIVE = re.compile(r'不(?:会|懂|熟悉|擅长|负责)|没有|未(?:使用|掌握|负责)|无经验|\b(?:no|not|never|without|lack|cannot|can.t)\b', re.I)


def references(evidence, paths):
    return [fact['id'] for fact in evidence.get('facts', [])
            if any(path == candidate or candidate.startswith(path + '.')
                   for path in paths for candidate in fact.get('resume_paths', []))]


def plan(data, evidence, report, target=None):
    """Produce a conservative starting plan; the conversational skill develops it."""
    zh = data.get('language', 'zh') == 'zh'
    def tr(chinese, english):
        return chinese if zh else english
    target = target or report.get('requested_target') or report.get('role')
    entries, bullets = [], []
    for si, section in enumerate(data.get('sections', [])):
        for ei, entry in enumerate(section.get('entries', [])):
            path = f'sections[{si}].entries[{ei}]'
            rows = [{'text': value, 'path': f'{path}.bullets[{bi}]'}
                    for bi, value in enumerate(entry.get('bullets', [])) if value.strip()]
            bullets.extend(rows)
            if section.get('id', '').split('-')[0] in ('experience', 'projects') and rows:
                entries.append({'heading': entry.get('heading', ''), 'date': entry.get('date', ''),
                                'path': path, 'rows': rows})
    # Contacts, job titles and bare skills are not evidence of having performed work.
    work_rows = [row for entry in entries for row in entry['rows']]
    improvements, questions = [], []

    def add(kind, title, reason, rows, question, suggestion):
        paths = [row['path'] for row in rows]
        ids = references(evidence, paths)
        improvements.append({'kind': kind, 'title': title, 'reason': reason, 'resume_paths': paths,
                             'fact_ids': ids, 'source_quotes': [row['text'] for row in rows],
                             'suggestion': suggestion, 'status': 'proposal'})
        questions.append({'kind': kind, 'question': question, 'fact_ids': ids, 'resume_paths': paths})

    if report.get('conflicts') or evidence.get('remaining_blocks') or report.get('warnings') and any(
            re.search(r'OCR|partial|failed|missing|ambigu|possible columns|多列|未提取|顺序歧义', str(w), re.I)
            for w in report['warnings']):
        paths = [p for f in evidence.get('facts', []) if f['id'] in {
            item['fact_id'] for item in evidence.get('remaining_blocks', [])}
                 for p in f.get('resume_paths', [])]
        rows = [row for row in bullets if row['path'] in paths][:1]
        add('source_review', tr('先复核来源与归类', 'Review source coverage and grouping'),
            tr('提取或归类有疑点；候选结构需要和原文件核对。', 'Extraction or grouping needs comparison with the source.'), rows,
            tr('原文件中的姓名、日期、章节归类或识别遗漏应如何修正？', 'Which candidate names, dates, grouping or extraction gaps need correction?'),
            tr('保留全部原文；复核后再调整结构，未知项继续待确认。', 'Retain source text; adjust grouping after review and leave unknown items pending.'))
    metric = next((row for row in work_rows if NUMBER.search(row['text'])), None)
    owner = next((row for row in work_rows if OWNERSHIP.search(row['text'])), None)
    vague = next((row for row in work_rows if VAGUE.search(row['text'])), None)
    if metric and len(improvements) < 3:
        add('result_scope', tr('补齐结果口径', 'Clarify result definitions'),
            tr('旧稿的数字或效果说法还没有当前用户确认。', 'Numbers or outcome claims in the old draft are not yet confirmed.'), [metric],
            tr(f'“{metric["text"]}”的基线、周期与计算方式是什么？不清楚可以直接说明。', f'What baseline, period and calculation support “{metric["text"]}”? Unknown is a valid answer.'),
            tr('口径不明时，改用实际交付物、使用范围或已知反馈，不补造百分比。', 'If the measurement is unknown, use known deliverables, adoption or feedback without inventing percentages.'))
    focus = owner or vague or (work_rows[0] if work_rows else None)
    if focus and len(improvements) < 3:
        add('personal_action', tr('写清个人动作与取舍', 'Specify personal actions and decisions'),
            tr('职责或团队成果需要拆出本人做的部分。', 'Separate personal contributions from responsibilities and team results.'), [focus],
            tr(f'在“{focus["text"]}”中，你亲自完成了哪一步、做了什么选择？谁负责其余部分？', f'For “{focus["text"]}”, what did you personally do or decide, and who handled the rest?'),
            tr('按“遇到的问题 → 自己的动作与选择 → 已知交付或结果”改写；职责归属保持保守。', 'Rewrite as problem → personal action/decision → known deliverable/result, with conservative ownership.'))
    if not target and len(improvements) < 3:
        add('target', tr('选择一个主要投递方向', 'Choose a primary target'),
            tr('没有当前目标时只能做通用草稿，不能判断完整岗位匹配。', 'Without a current target, this is a general draft with incomplete role matching.'), [],
            tr('你想优先尝试哪种工作？也可说最喜欢或最不想做的任务。', 'Which work would you like to try first? You can name preferred or unwanted tasks.'),
            tr('下方方向仅用于探索；选定目标后再重排相关经历。', 'Use the directions below for exploration; reorder relevant evidence after selecting a target.'))
    if len(improvements) < 3:
        missing = next((entry for entry in entries if not entry['heading'] or not entry['date']), None)
        if missing:
            add('timeline', tr('补齐经历标题和时间', 'Complete entry headings and dates'),
                tr('候选条目缺标题或日期，时间线还不能确认。', 'A candidate entry lacks a heading or dates, so the timeline needs review.'), missing['rows'][:1],
                tr('这段经历的正式职位、组织和起止时间是什么？', 'What were the official role, organization and start/end dates?'),
                tr('只补用户提供的正式名称和日期；不把职责名改成任职头衔。', 'Use supplied official names/dates; do not turn a responsibility into a job title.'))
    if not improvements:
        add('content', tr('补充可用经历', 'Supply usable experience'),
            tr('当前来源不足以形成经历故事。', 'The source has insufficient material for an experience story.'), [],
            tr('请选择一段经历，说明遇到的问题、自己做的事和留下的交付物。', 'Choose one experience and describe the problem, your actions and the deliverable.'),
            tr('先补实际经历，再写故事和方向。', 'Add actual experience before developing stories and directions.'))

    stories = []
    for entry in entries[:2]:
        quotes = [row['text'] for row in entry['rows']]
        stories.append({'heading': entry['heading'], 'date': entry['date'],
                        'resume_paths': [entry['path']], 'fact_ids': references(evidence, [entry['path']]),
                        'source_quotes': quotes, 'status': 'source_only_draft',
                        'draft': tr('旧稿记载：', 'The old draft states: ') + '；'.join(quotes),
                        'gaps': [tr('当时的具体问题和约束', 'Specific problem and constraints'),
                                 tr('本人行动、选择及协作者分工', 'Personal actions, decisions and collaborators'),
                                 tr('可确认的结果或交付证据', 'Confirmable outcomes or deliverable evidence')]})
    directions = []
    for role, (label_zh, label_en, pattern, gap_zh, gap_en) in ROLE_CLUES.items():
        matching = []
        for row in work_rows:
            # Exclude a whole negative clause rather than treating absent skills as strengths.
            clauses = re.split(r'[。；;\n]', row['text'])
            if any(re.search(pattern, clause, re.I) and not NEGATIVE.search(clause) for clause in clauses):
                matching.append(row)
        if matching:
            directions.append({'role': role, 'label': tr(label_zh, label_en), 'status': 'exploratory',
                               'fact_ids': references(evidence, [row['path'] for row in matching]),
                               'source_quotes': [row['text'] for row in matching],
                               'basis': tr('旧稿存在相关任务线索，尚待用户复核。', 'The old draft contains related task clues requiring user review.'),
                               'gaps': tr(gap_zh, gap_en),
                               'tradeoff': tr('可从已有任务切入；仍需确认是否喜欢这类工作，并补目标岗位所需证据。', 'Existing tasks offer a starting point; confirm preferences and evidence required by the target role.')})
    # Count is a sorting device only; do not expose it as a capability/fit score.
    directions.sort(key=lambda item: (item['role'] != report.get('role'), -len(item['fact_ids'])))
    return {'schema_version': 1, 'status': 'COACHING_STARTING_POINT', 'language': data.get('language', 'zh'),
            'requested_target': target, 'improvements': improvements[:3], 'questions': questions[:3],
            'stories': stories, 'directions': directions[:3],
            'direction_notice': tr('方向是基于旧稿的探索线索，不证明胜任；缺少线索时不硬凑方向。', 'Directions are exploratory source clues, not proof of competence; absent evidence is not filled in.'),
            'notice': tr('本地规则生成的访谈起点。旧稿全部仍待复核，聊天中需完善正文、故事与方向；不是真实性核验、ATS评分或录用预测。', 'A local rule-based interview starting point. Imported claims remain unconfirmed; develop the resume, stories and directions in conversation. This is not verification, an ATS score or a hiring prediction.')}


def render_review(review, language='zh'):
    zh = language == 'zh'
    def tr(chinese, english):
        return chinese if zh else english
    def escaped(value):
        value = value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return re.sub(r'([\\`*_{}\[\]()#+.!|\-])', r'\\\1', value)
    def quoted(value):
        # All source text remains data even when the review is displayed as Markdown.
        return '\n'.join('> ' + escaped(line) for line in value.splitlines())
    lines = ['# ' + tr('旧简历改进与访谈起点', 'Imported resume review and interview starting point'), '', review['notice'], '']
    for index, item in enumerate(review['improvements'], 1):
        lines += [f'## {index}. {item["title"]}', '', item['reason'], '', item['suggestion'], '']
        for quote in item['source_quotes']:
            lines += [quoted(quote), '']
        if item['fact_ids']:
            lines += [tr('来源：', 'Sources: ') + ', '.join(item['fact_ids']), '']
    lines += ['## ' + tr('本轮关键问题', 'Questions for this round'), '']
    lines += [f'{i}. {escaped(item["question"])}' for i, item in enumerate(review['questions'], 1)]
    lines += ['', '## ' + tr('经历故事素材（待复核）', 'Story material (needs review)'), '']
    if not review['stories']:
        lines += [tr('来源不足，先补一段实际经历。', 'Insufficient source material; supply one actual experience.'), '']
    for item in review['stories']:
        lines += ['### ' + escaped(item['heading'] or tr('候选经历', 'Candidate experience')), '', quoted(item['draft']), '',
                  tr('待补：', 'Gaps: ') + ' / '.join(item['gaps']), '',
                  tr('来源：', 'Sources: ') + ', '.join(item['fact_ids']), '']
    lines += ['## ' + tr('探索方向', 'Exploratory directions'), '', review['direction_notice'], '']
    for item in review['directions']:
        lines += ['### ' + item['label'], '', item['basis'], '',
                  tr('待补证据：', 'Evidence gaps: ') + item['gaps'], '',
                  tr('取舍：', 'Tradeoff: ') + item['tradeoff'], '',
                  tr('来源：', 'Sources: ') + ', '.join(item['fact_ids']), '']
        for quote in item['source_quotes']:
            lines += [quoted(quote), '']
    if not review['directions']:
        lines += [tr('尚无足够的任务线索，请在聊天中先明确经历与偏好。', 'Insufficient task clues; clarify experiences and preferences in conversation.'), '']
    return '\n'.join(lines)
