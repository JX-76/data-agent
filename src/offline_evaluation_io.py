# -*- coding: utf-8 -*-
"""Offline public-data, human-review, and lab-study interchange contracts.

No downloader is included: public datasets must be explicitly approved and
imported by a caller.  This makes licence, checksum, and schema mapping visible
in the release certificate instead of hiding a network dependency.
"""
from __future__ import unicode_literals
import csv
import hashlib
import json
import os

PUBLIC_SOURCE_CONTRACT = 'offline_public_dataset_manifest_v1'
REVIEW_CONTRACT = 'offline_blind_review_v1'
LAB_RECORD_CONTRACT = 'offline_lab_study_record_v1'


def _text(value):
    return '' if value is None else str(value)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block: break
            digest.update(block)
    return digest.hexdigest()


def validate_public_manifest(manifest):
    manifest = manifest or {}; errors = []
    for field in ('dataset_id', 'source_url', 'license_id', 'schema_mapping_version', 'local_path', 'expected_sha256'):
        if not manifest.get(field): errors.append('%s_required' % field)
    local = manifest.get('local_path')
    if local and os.path.exists(local) and manifest.get('expected_sha256'):
        if sha256_file(local) != manifest['expected_sha256']:
            errors.append('source_checksum_mismatch')
    elif local:
        errors.append('source_file_missing')
    if manifest.get('network_downloaded_automatically'):
        errors.append('automatic_network_download_not_allowed')
    return errors


def export_blind_review_pack(cases, path):
    """Export neutral case rows; never include model score or expected answer."""
    rows = []
    for case in cases or []:
        row = dict(case or {})
        rows.append({'contract': REVIEW_CONTRACT, 'case_id': row.get('case_id'),
                     'query': row.get('query'), 'answer': row.get('answer'),
                     'rubric_version': 'offline_ops_rubric_v1',
                     'attribution_coverage_1_5': '', 'logic_chain_1_5': '',
                     'actionability_1_5': '', 'adoption_intent_1_5': '',
                     'reviewer_id': '', 'reviewer_role': '', 'notes': ''})
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent): os.makedirs(parent)
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ['contract', 'case_id'])
        writer.writeheader(); writer.writerows(rows)
    return {'contract': REVIEW_CONTRACT, 'path': path, 'count': len(rows), 'status': 'exported'}


def import_blind_reviews(path):
    rows = []
    with open(path, 'r', encoding='utf-8') as handle:
        for row in csv.DictReader(handle): rows.append(row)
    valid = []; errors = []
    dims = ['attribution_coverage_1_5', 'logic_chain_1_5', 'actionability_1_5', 'adoption_intent_1_5']
    for index, row in enumerate(rows, 2):
        if not row.get('case_id') or not row.get('reviewer_id'):
            errors.append({'row': index, 'reason': 'case_id_and_reviewer_id_required'}); continue
        try:
            scores = dict((dim, int(row[dim])) for dim in dims)
            if any(score < 1 or score > 5 for score in scores.values()): raise ValueError('outside_1_5')
        except Exception:
            errors.append({'row': index, 'reason': 'all_rubric_scores_must_be_1_5'}); continue
        valid.append({'contract': REVIEW_CONTRACT, 'case_id': row['case_id'], 'reviewer_id': row['reviewer_id'],
                      'reviewer_role': row.get('reviewer_role') or 'unspecified', 'scores': scores,
                      'notes': row.get('notes') or ''})
    return {'contract': REVIEW_CONTRACT, 'valid_reviews': valid, 'invalid_rows': errors,
            'sample_size': len(valid), 'status': 'measured' if valid else 'not_measured'}


def summarize_lab_records(records):
    """Summarize paired BI-vs-agent task times; absent records stay unmeasured."""
    pairs = []
    for row in records or []:
        try:
            traditional = float(row['traditional_seconds']); agent = float(row['agent_seconds'])
            if traditional <= 0 or agent < 0: continue
            pairs.append((traditional, agent))
        except Exception:
            continue
    if not pairs:
        return {'contract': LAB_RECORD_CONTRACT, 'status': 'not_measured', 'sample_size': 0, 'value': None}
    deltas = [(traditional-agent)/traditional for traditional, agent in pairs]
    return {'contract': LAB_RECORD_CONTRACT, 'status': 'measured', 'sample_size': len(pairs),
            'value': round(sum(deltas)/float(len(deltas)), 4),
            'metric': 'lab_decision_efficiency_delta',
            'disclaimer': 'Offline paired laboratory estimate, not online productivity impact.'}


__all__ = ['validate_public_manifest', 'export_blind_review_pack', 'import_blind_reviews',
           'summarize_lab_records', 'sha256_file']
