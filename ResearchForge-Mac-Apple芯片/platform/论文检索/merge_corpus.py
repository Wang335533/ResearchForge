"""Lossless, repeatable CNKI -> ResearchForge CSV merge; no model calls."""
from __future__ import annotations

import argparse
import collections
import csv
import datetime
import hashlib
import json
import time
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ('cnki_id', 'title', 'authors', 'abstract', 'keywords', 'journal', 'year')
VERSION = 'cnki-journal-mapping-1'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def notice_reason(title):
    """Conservative title rules, recorded separately from source metadata.

    Do not classify every mention of 广告、声明、目录、更正 as a notice:
    those words also occur in substantive research titles.
    """
    t = re.sub(r'\s+', '', title).strip('。.!！')
    if re.search(r'征稿|征订|启事|稿约|投稿指南|投稿须知|征文通知', t):
        return '征稿、征订或投稿通知（题名规则）'
    if re.fullmatch(r'勘误|更正|更正和补充|作者来函更正|编后[记语]?|声明|会议消息', t):
        return '编辑通知或勘误（题名规则）'
    if re.search(r'(杂志社|杂志|本刊|编辑部).{0,6}(严正|郑重|重要)?声明$', t):
        return '期刊声明（题名规则）'
    if re.search(r'总目录$|\d{4}年.*目录$', t):
        return '期刊年度目录（题名规则）'
    return ''


def map_cnki(row):
    ident = row['cnki_id'].strip()
    if not ident or not row['title'].strip() or not re.fullmatch(r'\d{4}', row['year'].strip()):
        raise ValueError(f'中文记录缺少编号、题名或有效年份：{ident!r}')
    reason = notice_reason(row['title'])
    # All original fields survive, including unparsed pages, funds and metrics.
    # CNKI metrics must never be stored as WOS citation counts.
    return {**row, 'ut': 'CNKI:' + ident, 'source_title': row['journal'],
            'publication_year': row['year'], 'keywords_plus': row['keywords'],
            'language': 'zh', 'source_database': 'CNKI',
            'document_types': 'Editorial Material' if reason else 'Journal Record',
            'record_type_basis': reason or '源文件未提供文献类型；期刊记录，未推定为 Article/Review'}


def replace_file(source, target, attempts=40):
    """os.replace that waits out a brief concurrent reader, which Windows treats as a lock."""
    for attempt in range(attempts):
        try:
            return os.replace(source, target)
        except PermissionError:
            if os.name != 'nt' or attempt == attempts - 1:
                raise
            time.sleep(0.05)


def merge_corpus(source, chinese, *, apply=False, backup_dir=None):
    source, chinese = Path(source).resolve(), Path(chinese).resolve()
    if source == chinese:
        raise ValueError('合并目标不能是中文原始文件本身。')
    hashes = {'source': digest(source), 'chinese': digest(chinese)}
    seen = {}
    audit = dict(version=VERSION, source=str(source), chinese=str(chinese),
                 before_sha256=hashes, original_rows=0, chinese_rows=0,
                 added=0, identical_skipped=0, notices=0, chinese_missing_abstract=0)
    by_type = collections.Counter()
    temporary = None
    try:
        with source.open(encoding='utf-8-sig', newline='') as a, chinese.open(encoding='utf-8-sig', newline='') as b:
            old, new = csv.DictReader(a), csv.DictReader(b)
            if not old.fieldnames or 'ut' not in old.fieldnames:
                raise ValueError('目标 CSV 缺少 ut。')
            if not new.fieldnames or any(k not in new.fieldnames for k in REQUIRED):
                raise ValueError('中文 CSV 缺少必需字段。')
            if any(k in new.fieldnames for k in ('ut', 'source_database', 'record_type_basis')):
                raise ValueError('中文原始 CSV 包含保留字段，请核对字段映射。')
            fields = list(dict.fromkeys([*old.fieldnames, *new.fieldnames, 'language', 'source_database', 'record_type_basis']))
            with tempfile.NamedTemporaryFile('w', encoding='utf-8-sig', newline='', dir=source.parent,
                                             prefix='.bilingual-', suffix='.pending', delete=False) as f:
                temporary = Path(f.name)
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()

                def normalized(row):
                    if None in row or any(v is None for v in row.values()):
                        raise ValueError('CSV 行字段数量与表头不一致。')
                    return {k: row.get(k, '') for k in fields}

                def row_hash(row):
                    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

                for row in old:
                    audit['original_rows'] += 1
                    ident = row['ut'].strip()
                    if not ident or ident in seen:
                        raise ValueError(f'原库编号为空或重复：{ident!r}；未修改原文件。')
                    if not row.get('language'):
                        row['language'] = 'zh' if ident.startswith('CNKI:') else 'en'
                    if not row.get('source_database'):
                        row['source_database'] = 'CNKI' if ident.startswith('CNKI:') else 'WOS'
                    row = normalized(row)
                    seen[ident] = row_hash(row)
                    writer.writerow(row)
                for raw in new:
                    if None in raw or any(v is None for v in raw.values()):
                        raise ValueError('中文 CSV 行字段数量与表头不一致。')
                    audit['chinese_rows'] += 1
                    row = normalized(map_cnki(raw))
                    by_type[row['document_types']] += 1
                    audit['notices'] += row['document_types'] == 'Editorial Material'
                    audit['chinese_missing_abstract'] += not bool(row['abstract'].strip())
                    ident, sha = row['ut'], row_hash(row)
                    if ident in seen:
                        if seen[ident] != sha:
                            raise ValueError(f'相同编号内容冲突：{ident}；未覆盖任何记录。')
                        audit['identical_skipped'] += 1
                        continue
                    seen[ident] = sha
                    writer.writerow(row)
                    audit['added'] += 1
                f.flush()
                os.fsync(f.fileno())
        if digest(source) != hashes['source'] or digest(chinese) != hashes['chinese']:
            raise ValueError('合并期间输入文件发生变化；未发布。')
        audit.update(total_rows=len(seen), chinese_record_types=dict(by_type),
                     merged_sha256=digest(temporary), applied=False)
        if apply and audit['merged_sha256'] != hashes['source']:
            backup = Path(backup_dir) if backup_dir else ROOT/'work/backups'/('corpus-merge-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            backup.mkdir(parents=True, exist_ok=True)
            copy = backup/source.name
            if copy.exists() and digest(copy) != hashes['source']:
                raise ValueError('备份路径已有不同文件，拒绝覆盖。')
            if not copy.exists():
                shutil.copy2(source, copy)
            audit['backup'] = str(copy)
            # A unique completed sibling is atomically published at the original path.
            replace_file(temporary, source)
            audit['applied'] = True
        return audit
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='保留原始字段，将中文 CNKI 期刊合并到 ResearchForge 文献源 CSV')
    parser.add_argument('--source', type=Path, default=ROOT/'abstract_en.csv')
    parser.add_argument('--chinese', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='备份并发布；默认只核验')
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--audit', type=Path)
    args = parser.parse_args()
    result = merge_corpus(args.source, args.chinese, apply=args.apply, backup_dir=args.backup_dir)
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.audit:
        args.audit.parent.mkdir(parents=True, exist_ok=True)
        args.audit.write_text(content+'\n', encoding='utf-8')
    print(content)


if __name__ == '__main__':
    main()
