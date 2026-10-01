#!/usr/bin/env python3
"""Build private resume versions from explicitly bound shared facts. Stdlib only."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import string
import sys
import tempfile

# Loading by real script path also works when imported by a test or installed skill.
import importlib.util
ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('resume_renderer', ROOT / 'scripts/resume.py')
renderer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(renderer)
IDENTIFIER = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
REVISION = re.compile(r'revision-[0-9]{6}\Z')
FORMATTER = string.Formatter()


def keys(obj, allowed, required, path):
    if not isinstance(obj, dict):
        raise ValueError(f'{path} must be an object')
    unknown = set(obj) - set(allowed)
    if unknown:
        raise ValueError(f'{path}.{sorted(unknown)[0]}: unknown field')
    missing = set(required) - set(obj)
    if missing:
        raise ValueError(f'{path}.{sorted(missing)[0]}: required field is missing')


def text(value, path):
    renderer.require_text(value, path, allow_empty=False)


def identifier(value, path):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f'{path}: use one lowercase identifier, without paths or dots')


def expand(template, values, path):
    text(template, path)
    used = set()
    try:
        for _, field, spec, conversion in FORMATTER.parse(template):
            if field is None:
                continue
            if not field or not re.fullmatch(r'[a-z][a-z0-9_]*', field):
                raise ValueError(f'{path}: only named tokens such as {{count}} are supported')
            if spec or conversion:
                raise ValueError(f'{path}: format specifications and conversions are not supported')
            used.add(field)
        missing, unused = used - set(values), set(values) - used
        if missing:
            raise ValueError(f'{path}: unknown token {{{sorted(missing)[0]}}}')
        if unused:
            raise ValueError(f'{path}: unused reference {sorted(unused)[0]}')
        return template.format_map(values)
    except (KeyError, ValueError) as error:
        if str(error).startswith(path):
            raise
        raise ValueError(f'{path}: invalid template: {error}') from error


def fact_expression(fact, language, path):
    expressions = fact['expressions']
    if language not in expressions:
        raise ValueError(f'{path}.expressions.{language}: language expression is missing')
    expression = expressions[language]
    if isinstance(expression, dict):
        key = str(fact['value'])
        if key not in expression:
            raise ValueError(f'{path}.expressions.{language}: no expression for value {key!r}')
        return expression[key]
    return expand(expression, {'value': str(fact['value'])}, f'{path}.expressions.{language}')


def set_field(data, path, value):
    """Set an existing visible string leaf; never create or infer a path."""
    tokens = re.findall(r'([a-z_]+)|\[([0-9]+)\]', path)
    node = data
    for key, index in tokens[:-1]:
        node = node[int(index)] if index else node[key]
    key, index = tokens[-1]
    node[int(index) if index else key] = value


def final_text(value, path):
    """Apply the renderer's final placeholder gate to generated auxiliary copy."""
    if renderer.PLACEHOLDER.search(value) or any(marker in value for marker in renderer.ROLE_PLACEHOLDERS):
        raise ValueError(f'{path}: unresolved placeholder; resolve it before final output')


def compile_workspace(workspace, confirmed_after_review=False):
    """Validate the contract and generate all current versions without file writes."""
    keys(workspace, ('schema_version', 'notice', 'facts', 'versions'),
         ('schema_version', 'facts', 'versions'), 'workspace')
    if type(workspace['schema_version']) is not int or workspace['schema_version'] != 1:
        raise ValueError('workspace.schema_version must be 1')
    if 'notice' in workspace:
        text(workspace['notice'], 'workspace.notice')
    if not isinstance(workspace['facts'], list) or not workspace['facts']:
        raise ValueError('workspace.facts must be a non-empty list')
    facts, fact_paths = {}, {}
    for index, fact in enumerate(workspace['facts']):
        path = f'workspace.facts[{index}]'
        keys(fact, ('id', 'source', 'status', 'value', 'expressions'),
             ('id', 'source', 'status', 'value', 'expressions'), path)
        identifier(fact['id'], f'{path}.id')
        if fact['id'] in facts:
            raise ValueError(f'{path}.id: duplicate fact id {fact["id"]}')
        text(fact['source'], f'{path}.source')
        if fact['status'] not in ('confirmed', 'pending'):
            raise ValueError(f'{path}.status must be confirmed or pending')
        if type(fact['value']) not in (str, int):
            raise ValueError(f'{path}.value must be a string or integer')
        if isinstance(fact['value'], str):
            text(fact['value'], f'{path}.value')
        keys(fact['expressions'], ('zh', 'en'), (), f'{path}.expressions')
        if not fact['expressions']:
            raise ValueError(f'{path}.expressions must contain a language')
        for language, expression in fact['expressions'].items():
            ep = f'{path}.expressions.{language}'
            if isinstance(expression, dict):
                if not expression:
                    raise ValueError(f'{ep} must contain choices')
                for choice, label in expression.items():
                    text(choice, ep)
                    text(label, f'{ep}.{choice}')
            else:
                # Scalar expressions must reference canonical value once or more.
                expand(expression, {'value': str(fact['value'])}, ep)
            fact_expression(fact, language, path)
        facts[fact['id']], fact_paths[fact['id']] = fact, path
    if not isinstance(workspace['versions'], list) or not workspace['versions']:
        raise ValueError('workspace.versions must be a non-empty list')
    compiled, seen = [], set()
    for index, version in enumerate(workspace['versions']):
        path = f'workspace.versions[{index}]'
        keys(version, ('id', 'resume', 'bindings', 'summary', 'basis'),
             ('id', 'resume', 'bindings', 'summary', 'basis'), path)
        identifier(version['id'], f'{path}.id')
        if version['id'] in seen:
            raise ValueError(f'{path}.id: duplicate version id {version["id"]}')
        seen.add(version['id'])
        data = copy.deepcopy(version['resume'])
        renderer.validate(data, draft=True)
        language = data['language']
        visible = dict(renderer.iter_visible_fields(data))
        dependencies, bound_paths = set(), {}

        def expression(item, item_path, extra=()):
            keys(item, ('template', 'refs') + extra, ('template', 'refs') + extra, item_path)
            if not isinstance(item['refs'], dict) or not item['refs']:
                raise ValueError(f'{item_path}.refs must be a non-empty object')
            replacements = {}
            for token, fact_id in item['refs'].items():
                if not re.fullmatch(r'[a-z][a-z0-9_]*', token):
                    raise ValueError(f'{item_path}.refs.{token}: invalid token')
                if not isinstance(fact_id, str) or fact_id not in facts:
                    raise ValueError(f'{item_path}.refs.{token}: unknown fact id {fact_id!r}')
                dependencies.add(fact_id)
                replacements[token] = fact_expression(facts[fact_id], language, fact_paths[fact_id])
            return expand(item['template'], replacements, f'{item_path}.template')

        if not isinstance(version['bindings'], list) or not version['bindings']:
            raise ValueError(f'{path}.bindings must be a non-empty list')
        for bind_index, binding in enumerate(version['bindings']):
            bp = f'{path}.bindings[{bind_index}]'
            value = expression(binding, bp, ('path',))
            if not isinstance(binding['path'], str) or binding['path'] not in visible:
                raise ValueError(f'{bp}.path: unknown or non-visible string field {binding["path"]!r}')
            if binding['path'] in bound_paths:
                raise ValueError(f'{bp}.path: conflicting binding for {binding["path"]}')
            set_field(data, binding['path'], value)
            bound_paths[binding['path']] = sorted(set(binding['refs'].values()))
        if not isinstance(version['summary'], list) or not version['summary']:
            raise ValueError(f'{path}.summary must be a non-empty list')
        summaries = [expression(item, f'{path}.summary[{i}]')
                     for i, item in enumerate(version['summary'])]
        if confirmed_after_review:
            for summary_index, value in enumerate(summaries):
                final_text(value, f'{path}.summary[{summary_index}].template')
        if not isinstance(version['basis'], list):
            raise ValueError(f'{path}.basis must be a list')
        basis = []
        for basis_index, item in enumerate(version['basis']):
            ip = f'{path}.basis[{basis_index}]'
            value = expression(item, ip, ('requirement',))
            text(item['requirement'], f'{ip}.requirement')
            if confirmed_after_review:
                # The requirement is quoted JD context, not a candidate claim.
                final_text(value, f'{ip}.template')
            ids = sorted(set(item['refs'].values()))
            basis.append({'requirement': item['requirement'], 'evidence': value,
                          'fact_ids': ids,
                          'resume_paths': [p for p, refs in bound_paths.items() if set(ids) & set(refs)]})
        if confirmed_after_review:
            pending = sorted(fid for fid in dependencies if facts[fid]['status'] != 'confirmed')
            if pending:
                raise ValueError(f'{path}: unconfirmed facts: {", ".join(pending)}')
            # The explicit CLI review flag never overrides the source fact gate.
            renderer.validate(data, draft=False)
        else:
            data['facts_confirmed'] = False
            renderer.validate(data, draft=True)
        compiled.append({'id': version['id'], 'resume': data, 'summary': summaries,
                         'basis': basis, 'bindings': bound_paths,
                         'facts': [{'id': fid, 'source': facts[fid]['source'],
                                    'status': facts[fid]['status'], 'value': facts[fid]['value'],
                                    'expression': fact_expression(facts[fid], language, fact_paths[fid])}
                                   for fid in sorted(dependencies)]})
    return compiled


def read_workspace(source):
    source = Path(source).resolve()
    try:
        return source, json.loads(source.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f'{source}: cannot read workspace: {error}') from error


def atomic_bytes(path, payload):
    """Replace one file atomically; also used to restore exact metadata bytes."""
    if path.is_symlink():
        raise ValueError(f'{path}: refusing a symlink')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=path.parent,
                                         prefix='.metadata-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json(path, value):
    """Replace only metadata sidecars atomically, with no symlink following."""
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def metadata_backup(out, manifest):
    paths = [out / 'manifest.json', out / '.fact-workspace-output']
    for revision in manifest['revisions']:
        folder = out / revision['revision']
        paths.append(folder / 'revision.json')
        paths.extend(folder / version / 'status.json' for version in revision['versions'])
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def rollback_publication(backup, target):
    """Restore sidecars only; never touch an older editable body or snapshot."""
    errors = []
    for path, payload in backup.items():
        try:
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, payload)
        except (OSError, ValueError) as error:
            errors.append(f'{path}: {error}')
    if not errors:
        try:
            # This directory was exclusively created by the failed invocation.
            shutil.rmtree(target)
        except OSError as error:
            errors.append(f'{target}: {error}')
    if errors:
        raise ValueError('publication rollback could not finish; preserve this output root '
                         'and restore metadata before retrying: ' + '; '.join(errors))


def safe_output(source, output):
    raw = Path(output).expanduser()
    if '..' in raw.parts:
        raise ValueError('--out: parent traversal (..) is not allowed')
    out = raw.resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError('--out: shared fact output must stay outside the public skill repository')
    if source == out or out in source.parents:
        raise ValueError('--out: output must not be the input file or a directory containing it')
    if out.exists() and not out.is_dir():
        raise ValueError('--out must be a directory')
    return out


def load_manifest(out):
    marker, manifest_path = out / '.fact-workspace-output', out / 'manifest.json'
    if any(out.iterdir()) and not marker.is_file():
        raise ValueError('--out: existing non-empty directory is not a facts output workspace')
    if marker.is_symlink() or manifest_path.is_symlink():
        raise ValueError('--out: refusing a symlink in workspace metadata')
    if not manifest_path.exists():
        if marker.exists():
            raise ValueError('--out: manifest.json is missing; preserve this folder and use a new output root')
        return {'schema_version': 1, 'current_revision': None, 'revisions': []}
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        keys(manifest, ('schema_version', 'current_revision', 'revisions'),
             ('schema_version', 'current_revision', 'revisions'), 'manifest')
        if manifest['schema_version'] != 1 or not isinstance(manifest['revisions'], list):
            raise ValueError('manifest: invalid schema or revision list')
        names = set()
        for index, item in enumerate(manifest['revisions']):
            path = f'manifest.revisions[{index}]'
            keys(item, ('revision', 'state', 'workspace_sha256', 'versions', 'review'),
                 ('revision', 'state', 'workspace_sha256', 'versions', 'review'), path)
            name = item['revision']
            if not isinstance(name, str) or not REVISION.fullmatch(name) or name in names:
                raise ValueError(f'{path}.revision: invalid or duplicate revision')
            names.add(name)
            folder = out / name
            if folder.is_symlink() or not folder.is_dir():
                raise ValueError(f'{path}.revision: missing directory or symlink')
            if (folder / 'revision.json').is_symlink():
                raise ValueError(f'{path}: refusing symlink revision metadata')
            if not isinstance(item['versions'], list):
                raise ValueError(f'{path}.versions must be a list')
            for version_id in item['versions']:
                identifier(version_id, f'{path}.versions')
                vf = folder / version_id
                if vf.is_symlink() or not vf.is_dir() or (vf / 'status.json').is_symlink():
                    raise ValueError(f'{path}: refusing missing or symlink version metadata')
        if manifest['current_revision'] not in names:
            raise ValueError('manifest.current_revision: unknown revision')
        return manifest
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f'manifest.json: cannot read output history: {error}') from error


def sync(source, output, confirmed_after_review=False):
    source, workspace = read_workspace(source)
    compiled = compile_workspace(workspace, confirmed_after_review)
    out = safe_output(source, output)
    out.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(out)
    lock = out / '.sync.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError('--out: another sync is active, or .sync.lock needs recovery') from error
    os.close(fd)
    pending = None
    try:
        digest = hashlib.sha256(json.dumps(workspace, ensure_ascii=False, sort_keys=True,
                                           separators=(',', ':')).encode()).hexdigest()
        number = max([int(item['revision'].split('-')[1]) for item in manifest['revisions']] + [0]) + 1
        if number > 999999:
            raise ValueError('--out: revision limit reached; use a new output root')
        revision = f'revision-{number:06d}'
        target = out / revision
        if target.exists():
            raise ValueError(f'{target}: refusing to overwrite an existing revision')
        pending = Path(tempfile.mkdtemp(prefix='.pending-', dir=out))
        review = 'CONFIRMED_AFTER_REVIEW' if confirmed_after_review else 'NEEDS_REVIEW'
        record = {'revision': revision, 'state': 'CURRENT', 'workspace_sha256': digest,
                  'versions': [v['id'] for v in compiled], 'review': review}
        for version in compiled:
            folder = pending / version['id']
            folder.mkdir()
            write_json(folder / 'resume.json', version['resume'])
            for markdown, suffix in ((True, 'md'), (False, 'txt')):
                (folder / f'resume.{suffix}').write_text(
                    renderer.render_text(version['resume'], markdown=markdown,
                                         draft=not confirmed_after_review), encoding='utf-8')
            write_json(folder / 'summary.json', version['summary'])
            prefix = '' if confirmed_after_review else 'DRAFT / 草稿 — 需要重新确认当前内容\n\n'
            (folder / 'summary.txt').write_text(prefix + '\n'.join(version['summary']) + '\n', encoding='utf-8')
            write_json(folder / 'basis.json', {'notice': 'JD evidence mapping only; no automatic score or independent fact verification.',
                                             'items': version['basis'], 'facts': version['facts'],
                                             'bindings': version['bindings']})
            write_json(folder / 'status.json', {**record, 'version': version['id'],
                                               'notice': 'CURRENT means latest generated version; it is not human delivery approval.'})
        # Retain the full ledger and templates: a digest alone cannot recover
        # provenance once the private input is corrected in a later interview.
        write_json(pending / 'workspace.json', workspace)
        write_json(pending / 'revision.json', record)
        backup = metadata_backup(out, manifest)
        pending.rename(target)
        pending = None
        try:
            # Only status sidecars change in old revisions; editable bodies are preserved.
            for old in manifest['revisions']:
                old['state'] = 'STALE'
                old_folder = out / old['revision']
                write_json(old_folder / 'revision.json', {**old, 'superseded_by': revision})
                for version_id in old['versions']:
                    write_json(old_folder / version_id / 'status.json',
                               {**old, 'version': version_id, 'superseded_by': revision,
                                'notice': 'STALE: shared facts or current generation changed. Preserve manual edits and review the latest revision.'})
            manifest['current_revision'] = revision
            manifest['revisions'].append(record)
            atomic_bytes(out / '.fact-workspace-output', b'Private generated fact workspace, schema 1.\n')
            write_json(out / 'manifest.json', manifest)
        except BaseException:
            rollback_publication(backup, target)
            raise
        return {'output': str(out), 'current_revision': revision, 'review': review,
                'versions': [v['id'] for v in compiled], 'stale_revisions': len(manifest['revisions']) - 1}
    finally:
        if pending is not None:
            shutil.rmtree(pending)
        lock.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='validate shared facts and all version bindings without writing')
    check.add_argument('input', type=Path)
    command = commands.add_parser('sync', help='create a new revision; older exported bodies are preserved')
    command.add_argument('input', type=Path)
    command.add_argument('--out', required=True, type=Path)
    command.add_argument('--confirmed-after-review', action='store_true',
                         help='only after actual review of this workspace; does not bypass unconfirmed facts')
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            _, workspace = read_workspace(args.input)
            compiled = compile_workspace(workspace)
            result = {'valid': True, 'facts': len(workspace['facts']),
                      'versions': [{'id': v['id'], 'facts': [f['id'] for f in v['facts']],
                                    'pending': [f['id'] for f in v['facts'] if f['status'] == 'pending']}
                                   for v in compiled], 'notice': 'Validation is not independent fact verification or delivery approval.'}
        else:
            result = sync(args.input, args.out, args.confirmed_after_review)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
