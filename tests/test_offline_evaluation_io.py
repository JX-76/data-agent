# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import os
import tempfile
import unittest

from offline_evaluation_io import (export_blind_review_pack, import_blind_reviews,
                                   summarize_lab_records, validate_public_manifest)


class OfflineEvaluationIoTests(unittest.TestCase):
    def test_public_manifest_rejects_automatic_download_and_missing_source(self):
        errors = validate_public_manifest({'dataset_id': 'spider', 'source_url': 'https://example.invalid',
            'license_id': 'x', 'schema_mapping_version': 'v1', 'local_path': 'missing.jsonl',
            'expected_sha256': '0', 'network_downloaded_automatically': True})
        self.assertIn('source_file_missing', errors)
        self.assertIn('automatic_network_download_not_allowed', errors)

    def test_export_then_import_blind_reviews(self):
        folder = tempfile.mkdtemp(); path = os.path.join(folder, 'review.csv')
        exported = export_blind_review_pack([{'case_id': 'c1', 'query': 'q', 'answer': 'a'}], path)
        self.assertEqual(1, exported['count'])
        with open(path, 'r', encoding='utf-8') as handle:
            lines = handle.readlines()
        headers = lines[0].rstrip('\n').split(',')
        values = dict((header, '') for header in headers)
        values.update({'contract': 'offline_blind_review_v1', 'case_id': 'c1', 'query': 'q', 'answer': 'a',
                       'rubric_version': 'offline_ops_rubric_v1', 'attribution_coverage_1_5': '4',
                       'logic_chain_1_5': '4', 'actionability_1_5': '4', 'adoption_intent_1_5': '5',
                       'reviewer_id': 'r1', 'reviewer_role': 'bi', 'notes': 'ok'})
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(lines[0])
            handle.write(','.join([values[header] for header in headers]) + '\n')
        imported = import_blind_reviews(path)
        self.assertEqual('measured', imported['status'])
        self.assertEqual(1, imported['sample_size'])

    def test_lab_summary_is_explicitly_offline(self):
        result = summarize_lab_records([{'traditional_seconds': 100, 'agent_seconds': 60}])
        self.assertEqual('measured', result['status'])
        self.assertEqual(.4, result['value'])
        self.assertIn('not online', result['disclaimer'])


if __name__ == '__main__': unittest.main()
