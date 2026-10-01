#!/usr/bin/env python3
"""Check recorded simulation structure and references, not writing quality or truth."""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUBRIC = {'目标匹配': 25, '证据与可信度': 25, '成果与价值': 20,
          '结构与取舍': 10, '表达质量': 10, '排版与提取': 10}


def check_record(record):
    try:
        return _check_record(record)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        return [f'record: invalid nested structure: {error}']


def _check_record(record):
    errors = []

    def require(condition, path, message):
        if not condition:
            errors.append(f'{path}: {message}')

    if not isinstance(record, dict):
        return ['record: must be an object']
    execution = record.get('execution', {})
    require(execution.get('mode') == 'same_model_directed_simulation', 'execution.mode',
            'must disclose same-model directed simulation')
    for key, expected in (('fictional', True), ('independent_review', False),
                          ('real_jobseeker', False), ('blind', False)):
        require(execution.get(key) is expected, f'execution.{key}', 'simulation boundary missing')
    require(bool(record.get('limitations')), 'limitations', 'must preserve evaluation limits')
    require(record.get('status') == 'completed', 'status', 'record is not complete')
    turns = record.get('turns', [])
    narrow = record.get('scope') == 'two_sentence_edit'
    require(len(turns) >= (1 if narrow else 3), 'turns', 'insufficient recorded turns')
    facts = {fact.get('id'): fact for fact in record.get('facts', [])}
    require(len(facts) == len(record.get('facts', [])), 'facts', 'duplicate fact IDs')

    for key, fact in facts.items():
        path = f'facts[{key}]'
        number = fact.get('source_turn', 0)
        valid_turn = isinstance(number, int) and 1 <= number <= len(turns)
        require(valid_turn, path + '.source_turn', 'unknown source turn')
        if valid_turn:
            quote = fact.get('source_quote', '')
            require(bool(quote) and quote in turns[number - 1].get('user', ''),
                    path + '.source_quote', 'quote must occur in recorded user input')
        for old_id in fact.get('supersedes', []):
            old = facts.get(old_id, {})
            require(old.get('superseded_by') == key and old.get('status') == 'superseded',
                    path + '.supersedes', 'correction must preserve reciprocal history')
            require(old.get('source_turn', len(turns) + 1) < number,
                    path + '.supersedes', 'correction must follow the original source')

    def check_claim(claim, path, text, turn_number, final=False):
        require(bool(claim.get('text')) and claim.get('text', '') in text,
                path + '.text', 'claim must occur in the recorded output')
        refs = claim.get('fact_ids', [])
        require(bool(refs), path + '.fact_ids', 'claim must cite source facts')
        for ref in refs:
            fact = facts.get(ref)
            require(fact is not None, path + '.fact_ids', f'unknown fact {ref}')
            if fact is None:
                continue
            require(fact.get('source_turn', len(turns) + 1) <= turn_number,
                    path + '.fact_ids', f'future fact {ref}')
            replacement = facts.get(fact.get('superseded_by'), {})
            stale = fact.get('status') == 'superseded' and (
                final or replacement.get('source_turn', 0) <= turn_number)
            require(not stale, path + '.fact_ids', f'superseded fact {ref}')
            require(not (final and fact.get('not_for_resume')),
                    path + '.fact_ids', f'excluded fact {ref}')

    for index, turn in enumerate(turns):
        path = f'turns[{index}]'
        require(turn.get('number') == index + 1, path + '.number', 'turn order must be contiguous')
        require(bool(turn.get('user')) and bool(turn.get('assistant')),
                path, 'must retain both actual input and output')
        require(bool(turn.get('progress')), path + '.progress', 'must describe visible progress')
        questions = turn.get('questions', [])
        require(len(questions) <= 3, path + '.questions', 'more than three questions')
        if narrow:
            require(not questions, path + '.questions', 'narrow edit must not force an interview')
        for question in questions:
            require(question in turn.get('assistant', ''), path + '.questions',
                    'question must occur in actual output')
        require(bool(turn.get('claims')), path + '.claims', 'must retain source-linked draft content')
        for claim_index, claim in enumerate(turn.get('claims', [])):
            check_claim(claim, f'{path}.claims[{claim_index}]', turn.get('assistant', ''), index + 1)

    delivery = record.get('deliverables', {})
    versions = delivery.get('versions', [])
    require(bool(versions), 'deliverables.versions', 'final text missing')
    require(bool(delivery.get('remaining_risks')), 'deliverables.remaining_risks',
            'must preserve unknowns and validation limits')
    require(bool(record.get('observations')), 'observations', 'execution observations missing')
    targets = {target['id']: target for target in record.get('targets', [])}
    require(len(targets) == len(record.get('targets', [])), 'targets', 'duplicate target IDs')
    for target_id, target in targets.items():
        number = target.get('source_turn', 0)
        quote = target.get('source_quote', '')
        require(isinstance(number, int) and 1 <= number <= len(turns)
                and bool(quote) and quote in turns[number - 1].get('user', ''),
                f'targets[{target_id}]', 'JD must cite recorded input')
    all_claim_ids = set()
    version_claim_ids = {}
    target_claim_ids = {target_id: set() for target_id in targets}
    last_reply = turns[-1].get('assistant', '') if turns else ''
    for version in versions:
        path = f'versions[{version.get("id")}]'
        version_id = version.get('id')
        require(bool(version_id) and version_id not in version_claim_ids, path,
                'version IDs must be unique')
        local_ids = set()
        text = version.get('text', '')
        require(bool(text) and text in last_reply, path + '.text',
                'final version must come from the actual final response')
        require(bool(version.get('claims')), path + '.claims', 'source-linked final claims missing')
        for claim_index, claim in enumerate(version.get('claims', [])):
            claim_id = claim.get('id')
            require(bool(claim_id) and claim_id not in all_claim_ids, path + '.claims',
                    'final claim IDs must be unique')
            all_claim_ids.add(claim_id)
            local_ids.add(claim_id)
            check_claim(claim, f'{path}.claims[{claim_index}]', text, len(turns), final=True)
        version_claim_ids[version_id] = local_ids
        target_id = version.get('target_id')
        require(target_id in targets or (narrow and target_id is None), path + '.target_id',
                'unknown target')
        if target_id in target_claim_ids:
            target_claim_ids[target_id].update(local_ids)

    requirements = {(target_id, requirement['id']) for target_id, target in targets.items()
                    for requirement in target.get('requirements', [])}
    mappings = delivery.get('jd_mappings', [])
    pairs = [(mapping.get('target_id'), mapping.get('requirement_id')) for mapping in mappings]
    require(set(pairs) == requirements and len(pairs) == len(requirements),
            'deliverables.jd_mappings', 'must account for every supplied JD requirement')
    for mapping in mappings:
        path = f'jd_mappings[{mapping.get("requirement_id")}]'
        target_id = mapping.get('target_id')
        require((target_id, mapping.get('requirement_id')) in requirements, path,
                'requirement does not belong to declared target')
        require(mapping.get('status') in ('supported', 'partial', 'gap'), path + '.status',
                'must be supported, partial, or gap')
        require(set(mapping.get('fact_ids', [])) <= facts.keys(), path, 'unknown fact reference')
        for ref in mapping.get('fact_ids', []):
            require(facts.get(ref, {}).get('status') != 'superseded', path,
                    f'superseded fact {ref}')
        require(set(mapping.get('claim_ids', [])) <= target_claim_ids.get(target_id, set()),
                path, 'claim must belong to a version for the declared target')
        if mapping.get('status') == 'supported':
            require(bool(mapping.get('fact_ids')) and bool(mapping.get('claim_ids')),
                    path, 'supported requirement needs source and final claim references')
        if mapping.get('status') in ('gap', 'partial'):
            require(bool(mapping.get('gap')), path + '.gap', 'missing evidence must remain explicit')

    assessments = delivery.get('assessments', [])
    if narrow:
        require(not assessments, 'deliverables.assessments', 'narrow edit must not force scoring')
    else:
        require({item.get('version_id') for item in assessments} == set(version_claim_ids)
                and len(assessments) == len(version_claim_ids),
                'deliverables.assessments', 'must assess each final version once')
    for assessment in assessments:
        path = f'assessments[{assessment.get("version_id")}]'
        local_ids = version_claim_ids.get(assessment.get('version_id'), set())
        dimensions = assessment.get('dimensions', [])
        require(len(dimensions) == len(RUBRIC)
                and {item.get('name') for item in dimensions} == set(RUBRIC),
                path + '.dimensions', 'must retain all six rubric dimensions')
        score = weight = 0
        for dimension in dimensions:
            require(dimension.get('weight') == RUBRIC.get(dimension.get('name')),
                    path, 'rubric weight mismatch')
            level = dimension.get('level')
            if dimension.get('name') == '排版与提取' and not execution.get('pdf_executed'):
                require(level is None, path, 'PDF layout cannot be scored without PDF execution')
            if level is None:
                continue
            require(type(level) is int and 0 <= level <= 4, path, 'invalid rubric level')
            if type(level) is not int:
                continue
            weight += dimension['weight']
            score += dimension['weight'] * level / 4
            refs = dimension.get('evidence_claim_ids', [])
            require(bool(refs) and set(refs) <= local_ids, path,
                    'score evidence must belong to the assessed version')
            require(bool(dimension.get('reason')), path, 'score reason missing')
        require(assessment.get('assessed_weight') == weight, path, 'assessed denominator mismatch')
        require(assessment.get('assessed_score') == score, path, 'weighted score mismatch')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='*', type=Path)
    args = parser.parse_args()
    paths = args.paths or sorted((ROOT / 'tests/conversation-evals').glob('*.json'))
    if not paths:
        print('FAIL: no conversation records found; nothing was checked.')
        return True
    failures = 0
    for path in paths:
        try:
            errors = check_record(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError, KeyError) as error:
            errors = [f'invalid artifact: {error}']
        failures += bool(errors)
        print(f'{"FAIL" if errors else "PASS"} structure/references: {path.name}')
        for error in errors:
            print('  ' + error)
    print('This checks record structure and references, not writing quality, factual truth, or hiring outcomes.')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
