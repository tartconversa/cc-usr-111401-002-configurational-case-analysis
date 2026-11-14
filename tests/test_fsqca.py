"""模糊集组态分析的口径、最小化、反例、认证范围与版本对照测试。"""
import copy
import json
import time
import unittest
from pathlib import Path

from app.fsqca import (Budget, _enumerate_covers, _quine_mccluskey,
                       ROW_OUTCOME, ROW_CONTRADICTORY, ROW_REMAINDER_UNOBSERVED,
                       ROW_REMAINDER_BELOW_FREQ)
from app.fsqca_service import analyze, diff
from app.service import inspect as baseline_inspect

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'fsqca_sample.json')
                     .read_text(encoding='utf-8'))


def make_payload(case_specs, conditions=('A', 'B')):
    cases = [{'id': cid, 'revision': 1, 'memberships': memb, 'outcome': y, 'weight': w}
             for cid, memb, y, w in case_specs]
    return {'schema_version': 1,
            'metadata': {'conditions': list(conditions), 'calibration': 'cal-t', 'data_version': 'dv-t'},
            'collections': {'cases': cases}}


def crisp(key, conditions):
    return {c: 0.9 if ch == '1' else 0.1 for c, ch in zip(conditions, key)}


def rows_by_key(result):
    return {r['key']: r for r in result['truth_table']['rows']}


def term_literal_sets(family):
    return [frozenset(tuple(sorted(t['literals'].items())) for t in sol['terms'])
            for sol in family['solutions']]


PAYLOAD1 = make_payload([
    ('c1', {'A': 0.9, 'B': 0.9}, 0.9, 2),
    ('c2', {'A': 0.8, 'B': 0.85}, 0.7, 1),
    ('c3', {'A': 0.2, 'B': 0.8}, 0.3, 1),
    ('c4', {'A': 0.15, 'B': 0.2}, 0.1, 0.4),
    ('c5', {'A': None, 'B': 0.9}, 0.9, 1),
    ('c6', {'A': 0.9, 'B': 0.9}, None, 1),
    ('c7', {'A': 0.5, 'B': 0.9}, 0.9, 1),
])

# 多最小解：ON={000,001,010,101,110,111}，余项={011,100}
CONDS3 = ('A', 'B', 'C')
PAYLOAD_MULTI = make_payload(
    [(f'k{key}', crisp(key, CONDS3), 0.9, 1) for key in ('000', '001', '010', '101', '110', '111')],
    conditions=CONDS3)

# 核心/边缘对照：ON={000,111}，冲突={001,010,011,100}，余项={101,110}
PAYLOAD_CORE = make_payload(
    [('k000', crisp('000', CONDS3), 0.9, 1),
     ('k111', crisp('111', CONDS3), 0.9, 1)] +
    [(f'k{key}', crisp(key, CONDS3), 0.1, 1) for key in ('001', '010', '011', '100')],
    conditions=CONDS3)

# 冲突行不可借用：11为结果行，01为冲突行，10/00为未观察余项
PAYLOAD_CONFLICT = make_payload([
    ('c1', {'A': 0.9, 'B': 0.9}, 0.9, 1),
    ('c2', {'A': 0.1, 'B': 0.9}, 0.2, 1),
])


class TruthTableTests(unittest.TestCase):
    def test_classification_weights_and_exclusions(self):
        result = analyze(PAYLOAD1)
        rows = rows_by_key(result)
        self.assertEqual(rows['11']['classification'], ROW_OUTCOME)
        self.assertEqual(rows['11']['n_weighted'], 3.0)
        self.assertAlmostEqual(rows['11']['consistency'], 2.5 / 2.6, places=5)
        self.assertEqual(rows['01']['classification'], ROW_CONTRADICTORY)
        self.assertAlmostEqual(rows['01']['consistency'], 0.375, places=5)
        self.assertEqual(rows['00']['classification'], ROW_REMAINDER_BELOW_FREQ)
        self.assertEqual(rows['00']['n_weighted'], 0.4)
        self.assertEqual(rows['10']['classification'], ROW_REMAINDER_UNOBSERVED)
        # 缺测案例不补0、不进入任何配置，排除范围随结果公开
        excluded = {e['id']: e['reasons'] for e in result['cases']['excluded']}
        self.assertEqual(set(excluded), {'c5', 'c6'})
        self.assertTrue(any('缺测' in r for r in excluded['c5']))
        all_corner_cases = {c['id'] for r in rows.values() for c in r['cases']}
        self.assertNotIn('c5', all_corner_cases)
        self.assertNotIn('c6', all_corner_cases)
        # 恰0.5案例不参与角点归属，但仍在评估案例集内
        self.assertEqual([e['id'] for e in result['truth_table']['assignment_excluded']], ['c7'])
        self.assertNotIn('c7', all_corner_cases)
        self.assertEqual(result['cases']['evaluated'], 5)

    def test_necessity_support_coverage_counterexamples(self):
        result = analyze(PAYLOAD1, {'necessity_cutoff': 0.9})
        nec_a = next(r for r in result['necessity'] if r['literals'] == {'A': 1})
        self.assertAlmostEqual(nec_a['consistency'], 3.24 / 3.74, places=5)
        self.assertAlmostEqual(nec_a['coverage'], 3.24 / 3.36, places=5)
        self.assertAlmostEqual(nec_a['support_weighted'], 3.36, places=5)
        self.assertEqual(nec_a['n_cases'], 5)
        self.assertFalse(nec_a['passes_cutoff'])
        self.assertEqual(nec_a['n_deviating'], 2)
        self.assertEqual(nec_a['deviating_cases'][0]['id'], 'c7')  # 加权缺口最大者在前
        nec_na = next(r for r in result['necessity'] if r['literals'] == {'A': 0})
        self.assertIsNotNone(nec_na['consistency'])

    def test_zero_weight_corner_counts_as_unobserved(self):
        payload = make_payload([('z1', {'A': 0.9, 'B': 0.9}, 0.9, 0)])
        rows = rows_by_key(analyze(payload))
        self.assertEqual(rows['11']['classification'], ROW_REMAINDER_UNOBSERVED)
        self.assertEqual(rows['11']['n_cases'], 1)
        self.assertEqual(rows['11']['n_weighted'], 0.0)

    def test_frequency_cutoff_is_researchers_choice(self):
        result = analyze(PAYLOAD1, {'frequency_cutoff': 3.5})
        rows = rows_by_key(result)
        self.assertEqual(rows['11']['classification'], ROW_REMAINDER_BELOW_FREQ)


class MinimizationKernelTests(unittest.TestCase):
    def deadline(self):
        return time.monotonic() + 10

    def test_qm_finds_all_prime_implicants(self):
        budget = Budget()
        primes = _quine_mccluskey(['000', '001', '010', '100', '101', '110'], [], 3, budget, self.deadline())
        self.assertEqual({(p[0], p[1]) for p in primes}, {(0, 0b011), (0, 0b101)})  # --0 与 -0-
        primes = _quine_mccluskey(['11'], ['10', '00'], 2, budget, self.deadline())
        self.assertEqual({(p[0], p[1]) for p in primes}, {(1, 2)})  # 仅1-；-0不覆盖结果角点

    def test_cover_enumeration_terms_before_literals(self):
        budget = Budget()
        # p两项文字覆盖两角点；q,r各一文字分覆盖一个角点 → 项数优先，{p}胜出
        p = (0, 0b001, 0b11, 0)
        q = (0, 0b110, 0b01, 0)
        r = (4, 0b011, 0b10, 0)
        sols, complete, _ = _enumerate_covers(2, [p, q, r], 3, budget, self.deadline())
        self.assertTrue(complete)
        self.assertEqual(sols, [frozenset({0})])
        # 去掉p后，{q,r}成为唯一最小解
        sols, complete, _ = _enumerate_covers(2, [q, r], 3, budget, self.deadline())
        self.assertEqual(sols, [frozenset({0, 1})])
        # 并列最小解全部保留：q1,q2等价 → 两个解
        q2 = (2, 0b101, 0b01, 0)
        sols, complete, _ = _enumerate_covers(2, [q, q2, r], 3, budget, self.deadline())
        self.assertEqual(len(sols), 2)
        self.assertTrue(all(len(s) == 2 for s in sols))


class SolutionFamilyTests(unittest.TestCase):
    def test_all_minimal_solutions_kept(self):
        result = analyze(PAYLOAD_MULTI)
        fam = result['solutions']['complex']
        self.assertEqual(fam['status'], 'complete')
        self.assertEqual(len(fam['solutions']), 2)
        for sol in fam['solutions']:
            self.assertEqual(len(sol['terms']), 3)
            self.assertTrue(all(t['n_literals'] == 2 for t in sol['terms']))
            self.assertTrue(all(t['borrowed_remainders'] == [] for t in sol['terms']))
        # 两个解文字集相同：核心为全部六个文字，边缘为空
        self.assertEqual(len(fam['core_literals']), 6)
        self.assertEqual(fam['peripheral_literals'], [])
        # 简约解借用全部余项后坍缩为空项
        pam = result['solutions']['parsimonious']
        self.assertEqual(len(pam['solutions']), 1)
        self.assertEqual(pam['solutions'][0]['terms'][0]['literals'], {})
        self.assertEqual(pam['solutions'][0]['terms'][0]['borrowed_remainders'], ['011', '100'])
        self.assertAlmostEqual(pam['solutions'][0]['solution_consistency'], 0.9, places=5)
        self.assertAlmostEqual(pam['solutions'][0]['solution_coverage'], 1.0, places=5)

    def test_core_and_peripheral_literals(self):
        result = analyze(PAYLOAD_CORE)
        fam = result['solutions']['parsimonious']
        self.assertEqual(len(fam['solutions']), 2)
        self.assertEqual(fam['core_literals'], [{'A': 0}, {'A': 1}, {'B': 0}, {'C': 0}])
        self.assertEqual(fam['peripheral_literals'], [{'B': 1}, {'C': 1}])
        borrowed = sorted(t['borrowed_remainders']
                          for sol in fam['solutions'] for t in sol['terms'] if t['n_literals'] == 2)
        self.assertEqual(borrowed, [['101'], ['110']])
        cx = result['solutions']['complex']
        self.assertEqual(len(cx['solutions']), 1)
        self.assertEqual(term_literal_sets(cx),
                         [frozenset({(('A', 0), ('B', 0), ('C', 0)), (('A', 1), ('B', 1), ('C', 1))})])

    def test_intermediate_respects_directional_expectations(self):
        base = analyze(PAYLOAD_CORE, {'directional_expectations': {'C': 0}})
        fam = base['solutions']['intermediate']
        self.assertEqual(len(fam['solutions']), 1)  # 含C=1的项被方向预期排除
        self.assertEqual(term_literal_sets(fam),
                         [frozenset({(('A', 0), ('B', 0), ('C', 0)), (('A', 1), ('B', 1))})])
        strict = analyze(PAYLOAD_CORE, {'directional_expectations': {'B': 0, 'C': 0}})
        fam2 = strict['solutions']['intermediate']
        # 两个简约质蕴涵项都被排除，回落到不借反事实的复杂解项
        self.assertEqual(term_literal_sets(fam2), term_literal_sets(strict['solutions']['complex']))
        self.assertTrue(all(t['borrowed_remainders'] == [] for t in fam2['solutions'][0]['terms']))

    def test_contradictory_rows_are_never_borrowed(self):
        result = analyze(PAYLOAD_CONFLICT)
        rows = rows_by_key(result)
        self.assertEqual(rows['01']['classification'], ROW_CONTRADICTORY)
        fam = result['solutions']['parsimonious']
        # 若01被当作余项借用，简约解会得到{B:1}；正确结果只能是{A:1}且仅借用10
        self.assertEqual(term_literal_sets(fam), [frozenset({(('A', 1),)})])
        self.assertEqual(fam['solutions'][0]['terms'][0]['borrowed_remainders'], ['10'])
        term = fam['solutions'][0]['terms'][0]
        self.assertAlmostEqual(term['consistency'], 1.0, places=5)
        self.assertAlmostEqual(term['raw_coverage'], 1.0 / 1.1, places=5)
        with self.assertRaisesRegex(ValueError, '非逻辑余项'):
            analyze(PAYLOAD_CONFLICT, {'remainder_policy': {'include': ['01']}})
        ok = analyze(PAYLOAD_CONFLICT, {'remainder_policy': {'include': ['10']}})
        self.assertEqual(term_literal_sets(ok['solutions']['parsimonious']), term_literal_sets(fam))

    def test_negated_outcome_is_analyzed_independently(self):
        pos = analyze(PAYLOAD_CONFLICT)
        neg = analyze(PAYLOAD_CONFLICT, {'negated_outcome': True})
        self.assertEqual(neg['outcome_direction'], 'absence')
        rows = rows_by_key(neg)
        self.assertEqual(rows['11']['classification'], ROW_CONTRADICTORY)
        self.assertEqual(rows['01']['classification'], ROW_OUTCOME)
        self.assertEqual(term_literal_sets(neg['solutions']['complex']),
                         [frozenset({(('A', 0), ('B', 1))})])
        with self.assertRaisesRegex(ValueError, '方向不同'):
            diff(pos, neg)

    def test_solution_terms_expand_back_to_configurations_and_cases(self):
        result = analyze(PAYLOAD_MULTI)
        rows = rows_by_key(result)
        on_keys = {k for k, r in rows.items() if r['classification'] == ROW_OUTCOME}
        remainder_keys = {k for k, r in rows.items() if r['classification'].startswith('remainder')}
        for fam in result['solutions'].values():
            for sol in fam['solutions']:
                for term in sol['terms']:
                    expect = {c['id'] for key in term['covers_configurations'] for c in rows[key]['cases']}
                    self.assertEqual(set(term['cases']), expect)
                    self.assertLessEqual(set(term['covers_configurations']), on_keys)
                    self.assertLessEqual(set(term['borrowed_remainders']), remainder_keys)


class BudgetAndCertificationTests(unittest.TestCase):
    def test_condition_count_budget(self):
        payload = make_payload([('x', {f'C{i}': 0.9 for i in range(11)}, 0.9, 1)],
                               conditions=tuple(f'C{i}' for i in range(11)))
        with self.assertRaisesRegex(ValueError, 'max_conditions'):
            analyze(payload)

    def test_partial_results_keep_certified_scope(self):
        result = analyze(PAYLOAD_MULTI, {'budget': {'max_cover_nodes': 1}})
        fam = result['solutions']['complex']
        self.assertEqual(fam['status'], 'partial')
        self.assertFalse(fam['enumeration_complete'])
        self.assertIsNone(fam['core_literals'])
        self.assertEqual(result['certification']['truth_table'], 'complete')
        self.assertEqual(result['certification']['necessity'], 'complete')
        self.assertTrue(fam['prime_implicants_complete'])  # 已认证范围保留

    def test_implicant_budget_marks_family_unavailable(self):
        result = analyze(PAYLOAD_MULTI, {'budget': {'max_implicants': 1}})
        fam = result['solutions']['complex']
        self.assertEqual(fam['status'], 'unavailable')
        self.assertFalse(fam['prime_implicants_complete'])
        self.assertEqual(result['certification']['truth_table'], 'complete')


class DiffTests(unittest.TestCase):
    def base(self):
        return make_payload([
            ('c1', {'A': 0.9, 'B': 0.9}, 0.9, 1),
            ('c2', {'A': 0.85, 'B': 0.85}, 0.85, 1),
            ('c3', {'A': 0.9, 'B': 0.2}, 0.8, 1),
        ])

    def test_path_disappears_when_case_moves_corner(self):
        a = analyze(self.base())
        moved = self.base()
        moved['collections']['cases'][2]['memberships']['B'] = 0.8
        b = analyze(moved)
        d = diff(a, b)
        fam = d['families']['complex']
        self.assertEqual(fam['disappeared_terms'], [{'A': 1}])
        self.assertEqual(fam['new_terms'], [{'A': 1, 'B': 1}])
        changes = {c['key']: c for c in d['truth_table_changes']}
        self.assertEqual(changes['10']['classification'],
                         {'from': ROW_OUTCOME, 'to': ROW_REMAINDER_UNOBSERVED})
        self.assertEqual(changes['10']['cases_removed'], ['c3'])
        self.assertEqual(changes['11']['cases_added'], ['c3'])
        self.assertEqual(d['options_changed'], {})
        self.assertFalse(d['same_input'])

    def test_persistent_path_with_changed_support(self):
        a = analyze(self.base())
        grown = self.base()
        grown['collections']['cases'].append(
            {'id': 'c4', 'revision': 1, 'memberships': {'A': 0.88, 'B': 0.15}, 'outcome': 0.85, 'weight': 1})
        b = analyze(grown)
        fam = diff(a, b)['families']['complex']
        self.assertEqual(fam['disappeared_terms'], [])
        self.assertEqual(fam['new_terms'], [])
        self.assertEqual(len(fam['persistent_terms']), 1)
        self.assertEqual(fam['persistent_terms'][0]['cases_added'], ['c4'])
        self.assertEqual(fam['persistent_terms'][0]['cases_removed'], [])

    def test_diff_requires_same_direction_and_conditions(self):
        a = analyze(self.base())
        other = analyze(self.base(), {'conditions': ['A']})
        with self.assertRaisesRegex(ValueError, '口径不同'):
            diff(a, other)


class ServiceAndRobustnessTests(unittest.TestCase):
    def test_determinism_under_case_reorder(self):
        shuffled = copy.deepcopy(PAYLOAD_MULTI)
        shuffled['collections']['cases'].reverse()
        a = json.dumps(analyze(PAYLOAD_MULTI), ensure_ascii=False, sort_keys=True)
        b = json.dumps(analyze(shuffled), ensure_ascii=False, sort_keys=True)
        self.assertEqual(a, b)

    def test_input_identity_matches_existing_service(self):
        self.assertEqual(analyze(PAYLOAD1)['input_digest'], baseline_inspect(PAYLOAD1)['input_digest'])
        self.assertEqual(analyze(PAYLOAD1)['schema'], 'fsqca/1')
        self.assertIn('探索性', analyze(PAYLOAD1)['framing'])

    def test_options_validation(self):
        with self.assertRaisesRegex(ValueError, '未知选项'):
            analyze(PAYLOAD1, {'consistancy_cutoff': 0.8})
        with self.assertRaisesRegex(ValueError, '方向预期'):
            analyze(PAYLOAD1, {'directional_expectations': {'A': 2}})
        with self.assertRaisesRegex(ValueError, '未选条件'):
            analyze(PAYLOAD1, {'directional_expectations': {'ZZ': 1}})
        with self.assertRaisesRegex(ValueError, '不在资料契约'):
            analyze(PAYLOAD1, {'conditions': ['A', 'ZZ']})
        sub = analyze(PAYLOAD1, {'conditions': ['A']})
        self.assertEqual(len(sub['truth_table']['rows']), 2)

    def test_fixture_smoke(self):
        result = analyze(FIXTURE)
        self.assertEqual(result['calibration'], 'cal-demo-1')
        rows = rows_by_key(result)
        self.assertEqual({k for k, r in rows.items() if r['classification'] == ROW_OUTCOME},
                         {'1111', '1110', '1011', '1101'})
        self.assertEqual({k for k, r in rows.items() if r['classification'] == ROW_CONTRADICTORY},
                         {'0010', '0101', '0000'})
        excluded = {e['id'] for e in result['cases']['excluded']}
        self.assertEqual(excluded, {'c13', 'c14'})
        self.assertEqual([e['id'] for e in result['truth_table']['assignment_excluded']], ['c15'])
        for name, fam in result['solutions'].items():
            self.assertEqual(fam['status'], 'complete', name)
            self.assertTrue(fam['solutions'], name)
        neg = analyze(FIXTURE, {'negated_outcome': True})
        self.assertEqual(neg['outcome_direction'], 'absence')
        d = diff(result, analyze(FIXTURE))
        self.assertTrue(d['same_input'])
        for fam in d['families'].values():
            self.assertEqual(fam['disappeared_terms'], [])
            self.assertEqual(fam['new_terms'], [])


if __name__ == '__main__':
    unittest.main()
