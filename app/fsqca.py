"""模糊集组态分析（探索性比较）。

在既有二值配置摘要（app.baseline）之外，提供可由研究人员反复调整口径的
模糊集组态分析：加权真值表、必要性扫描、充分性组态的多解最小化、
反例追溯、否定结果独立分析与版本对照。

核心约定：
- 缺测（null）不补 0；缺测案例的排除范围随结果公开。
- 隶属度恰为 0.5 的案例不参与角点归属，仍参与解的模糊集合指标。
- 已观察但未达一致性门槛的冲突行永不作为逻辑余项借用；
  未观察或低于频数门槛的余项按声明政策借用。
- 复杂解不借用余项；简约解按声明政策借用；中间解只接受方向预期允许的反事实，
  不借用任何反事实的复杂解质蕴涵项不受方向预期限制。
- 最小标准按（项数, 文字数）字典序，枚举全部达到该标准的解，不报告单次贪心结果。
- 否定结果（~Y）由独立调用在 1-y 上完整重算，不由正向结论取反。
- 输出为探索性组态比较，不构成已识别的因果效应。
"""
import json
import math
import time
from dataclasses import dataclass
from itertools import product

SCHEMA_ID = 'fsqca/1'
DIFF_SCHEMA_ID = 'fsqca-diff/1'
FRAMING = ('探索性组态比较分析：结果描述样本内的集合关系与可能并存的多条路径，'
           '不构成已识别的因果效应；否定结果须独立分析，不可由本结果取反。')

ROW_OUTCOME = 'outcome'                     # 达到频数与一致性门槛
ROW_CONTRADICTORY = 'contradictory'         # 已观察但未达一致性门槛（冲突），不可借用
ROW_REMAINDER_UNOBSERVED = 'remainder_unobserved'      # 无加权案例的余项
ROW_REMAINDER_BELOW_FREQ = 'remainder_below_frequency' # 低于频数门槛的余项
REMAINDER_CLASSES = frozenset({ROW_REMAINDER_UNOBSERVED, ROW_REMAINDER_BELOW_FREQ})

FAMILIES = ('complex', 'parsimonious', 'intermediate')

STATUS_COMPLETE = 'complete'
STATUS_PARTIAL = 'partial'
STATUS_UNAVAILABLE = 'unavailable'

_MAX_DEVIATING = 10  # 每项最多列出的反例数，总数另报


class BudgetExceeded(Exception):
    """资源预算耗尽；调用方保留已认证范围，不伪装完备。"""


@dataclass(frozen=True)
class Budget:
    max_conditions: int = 10
    max_implicants: int = 100000
    max_cover_nodes: int = 500000
    max_solutions: int = 2000
    time_seconds: float = 60.0


_OPTION_KEYS = {'conditions', 'frequency_cutoff', 'consistency_cutoff', 'necessity_cutoff',
                'directional_expectations', 'remainder_policy', 'negated_outcome', 'budget'}
_BUDGET_KEYS = frozenset({'max_conditions', 'max_implicants', 'max_cover_nodes',
                          'max_solutions', 'time_seconds'})


def _r(x, nd=6):
    """输出浮点统一取舍；None与非法值不进入结果。"""
    if x is None:
        return None
    v = round(float(x), nd)
    return 0.0 if v == 0 else v


def _resolve_options(meta, options):
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValueError('选项应为对象')
    unknown = sorted(set(options) - _OPTION_KEYS)
    if unknown:
        raise ValueError(f'未知选项: {unknown}')
    meta_conditions = meta.get('conditions')
    if (not isinstance(meta_conditions, list) or not meta_conditions
            or any(not isinstance(c, str) or not c for c in meta_conditions)):
        raise ValueError('metadata.conditions应为非空字符串数组')
    if len(set(meta_conditions)) != len(meta_conditions):
        raise ValueError('metadata.conditions存在重复条件')
    conditions = options.get('conditions', list(meta_conditions))
    if (not isinstance(conditions, list) or not conditions
            or any(not isinstance(c, str) or not c for c in conditions)):
        raise ValueError('conditions应为非空字符串数组')
    if len(set(conditions)) != len(conditions):
        raise ValueError('conditions存在重复条件')
    outside = sorted(set(conditions) - set(meta_conditions))
    if outside:
        raise ValueError(f'条件不在资料契约中: {outside}')
    budget_over = options.get('budget', {})
    if not isinstance(budget_over, dict) or set(budget_over) - _BUDGET_KEYS:
        raise ValueError('budget包含未知字段')
    vals = {}
    for field in _BUDGET_KEYS:
        v = budget_over.get(field, getattr(Budget(), field))
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f'budget.{field}应为正数')
        if field == 'time_seconds':
            vals[field] = float(v)
        else:
            if isinstance(v, float) and not v.is_integer():
                raise ValueError(f'budget.{field}应为正整数')
            vals[field] = int(v)
    budget = Budget(**{f: vals[f] for f in ('max_conditions', 'max_implicants',
                                            'max_cover_nodes', 'max_solutions', 'time_seconds')})
    if len(conditions) > budget.max_conditions:
        raise ValueError(f'条件数{len(conditions)}超过预算max_conditions={budget.max_conditions}，'
                         '请缩减条件或提高预算')
    freq = options.get('frequency_cutoff', 1.0)
    if isinstance(freq, bool) or not isinstance(freq, (int, float)) or not math.isfinite(freq) or freq < 0:
        raise ValueError('frequency_cutoff应为非负有限数')
    cons = options.get('consistency_cutoff', 0.8)
    if isinstance(cons, bool) or not isinstance(cons, (int, float)) or not 0 < cons <= 1:
        raise ValueError('consistency_cutoff应在(0,1]内')
    nec = options.get('necessity_cutoff')
    if nec is not None and (isinstance(nec, bool) or not isinstance(nec, (int, float)) or not 0 < nec <= 1):
        raise ValueError('necessity_cutoff应在(0,1]内或为null')
    de = options.get('directional_expectations', {})
    if not isinstance(de, dict):
        raise ValueError('directional_expectations应为对象')
    for c, v in de.items():
        if c not in conditions:
            raise ValueError(f'方向预期涉及未选条件: {c}')
        if v not in (0, 1) or isinstance(v, bool):
            raise ValueError('方向预期取值应为0（缺席）或1（在场）')
    rp = options.get('remainder_policy', 'all')
    if rp != 'all':
        keys = rp.get('include') if isinstance(rp, dict) else None
        if (not isinstance(keys, list)
                or any(not isinstance(k, str) or len(k) != len(conditions) or set(k) - {'0', '1'} for k in keys)):
            raise ValueError("remainder_policy应为'all'或{'include': [配置键...]}")
        if len(set(keys)) != len(keys):
            raise ValueError('remainder_policy.include存在重复')
        rp = {'include': sorted(keys)}
    neg = options.get('negated_outcome', False)
    if type(neg) is not bool:
        raise ValueError('negated_outcome应为布尔值')
    resolved = {'conditions': list(conditions),
                'frequency_cutoff': float(freq),
                'consistency_cutoff': float(cons),
                'necessity_cutoff': None if nec is None else float(nec),
                'directional_expectations': {c: de[c] for c in sorted(de)},
                'remainder_policy': rp,
                'negated_outcome': neg,
                'budget': {f: vals[f] for f in sorted(_BUDGET_KEYS)}}
    return resolved, list(conditions), budget


def _membership_value(v, what):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f'{what}应为[0,1]内的数或null')
    f = float(v)
    if not math.isfinite(f) or f < 0.0 or f > 1.0:
        raise ValueError(f'{what}应为[0,1]内的数或null')
    return f


def _prepare_cases(snapshot, conditions, negate):
    """列表删除缺测案例（不补0）；否定结果在此统一取 1-y。"""
    eval_cases = []
    excluded = []
    for row in snapshot.rows('cases'):
        cid = row['id']
        memb = row.get('memberships')
        if not isinstance(memb, dict):
            raise ValueError(f'案例{cid}的memberships应为对象')
        xs = []
        reasons = []
        for c in conditions:
            v = _membership_value(memb.get(c), f'案例{cid}条件{c}隶属度')
            if v is None:
                reasons.append(f'条件{c}缺测')
            xs.append(v)
        y = _membership_value(row.get('outcome'), f'案例{cid}结果隶属度')
        if y is None:
            reasons.append('结果缺测')
        w = row.get('weight')
        if (isinstance(w, bool) or not isinstance(w, (int, float))
                or not math.isfinite(float(w)) or float(w) < 0):
            raise ValueError(f'案例{cid}权重应为非负有限数')
        if reasons:
            excluded.append({'id': cid, 'reasons': reasons})
            continue
        eval_cases.append({'id': cid, 'w': float(w), 'x': tuple(xs),
                           'y': 1.0 - y if negate else y})
    eval_cases.sort(key=lambda c: c['id'])
    return eval_cases, excluded


def _all_keys(k):
    return [''.join(map(str, bits)) for bits in product((0, 1), repeat=k)]


def _key_to_ones(key):
    ones = 0
    for j, ch in enumerate(key):
        if ch == '1':
            ones |= 1 << j
    return ones


def _build_truth_table(eval_cases, k, freq_cut, cons_cut):
    """加权模糊真值表。恰0.5案例不参与角点归属；冲突行与余项分别标记。"""
    rows = {key: {'cases': [], 'n': 0.0, 'num': 0.0, 'den': 0.0} for key in _all_keys(k)}
    ambiguous = []
    for case in eval_cases:
        if any(v == 0.5 for v in case['x']):
            ambiguous.append({'id': case['id'], 'reasons': ['隶属度恰为0.5，不参与角点归属']})
            continue
        bits = tuple(1 if v > 0.5 else 0 for v in case['x'])
        key = ''.join(map(str, bits))
        m = 1.0
        for v, b in zip(case['x'], bits):
            m = min(m, v if b else 1.0 - v)
        r = rows[key]
        r['cases'].append({'id': case['id'], 'membership': _r(m), 'weight': _r(case['w'])})
        r['n'] += case['w']
        r['num'] += case['w'] * min(m, case['y'])
        r['den'] += case['w'] * m
    out = []
    for key in sorted(rows):
        r = rows[key]
        cons = r['num'] / r['den'] if r['den'] > 0 else None
        if r['n'] <= 0.0:
            cls = ROW_REMAINDER_UNOBSERVED
        elif r['n'] < freq_cut:
            cls = ROW_REMAINDER_BELOW_FREQ
        elif cons is not None and cons >= cons_cut:
            cls = ROW_OUTCOME
        else:
            cls = ROW_CONTRADICTORY
        out.append({'key': key,
                    'n_weighted': _r(r['n']),
                    'n_cases': len(r['cases']),
                    'consistency': _r(cons),
                    'classification': cls,
                    'cases': sorted(r['cases'], key=lambda c: c['id'])})
    return out, ambiguous


def _necessity(eval_cases, conditions, cutoff):
    """逐条件在场/缺席的必要性：一致性、覆盖、支持与反例。"""
    rows = []
    for j, c in enumerate(conditions):
        for value in (1, 0):
            num = den_cons = den_cov = 0.0
            devs = []
            for case in eval_cases:
                x = case['x'][j] if value == 1 else 1.0 - case['x'][j]
                y = case['y']
                w = case['w']
                num += w * min(y, x)
                den_cons += w * y
                den_cov += w * x
                if y > x and w > 0:
                    devs.append({'id': case['id'],
                                 'condition_membership': _r(x),
                                 'outcome_membership': _r(y),
                                 'gap_weighted': _r(w * (y - x))})
            devs.sort(key=lambda d: (-d['gap_weighted'], d['id']))
            row = {'literals': {c: value},
                   'consistency': _r(num / den_cons) if den_cons > 0 else None,
                   'coverage': _r(num / den_cov) if den_cov > 0 else None,
                   'support_weighted': _r(den_cov),
                   'n_cases': len(eval_cases),
                   'deviating_cases': devs[:_MAX_DEVIATING],
                   'n_deviating': len(devs)}
            if cutoff is not None:
                row['passes_cutoff'] = row['consistency'] is not None and row['consistency'] >= cutoff
            rows.append(row)
    return rows


def _imp_sort_key(ones, dash, k):
    return tuple(-1 if (dash >> j) & 1 else (ones >> j) & 1 for j in range(k))


def _quine_mccluskey(on_keys, dc_keys, k, budget, deadline):
    """全质蕴涵项生成。蕴涵项=(ones,dash,on_mask,rem_mask)，掩码相对传入键序。
    只保留覆盖至少一个结果角点的蕴涵项；余项角点不会出现在掩码之外，
    冲突角点不在输入中，结构上不会被覆盖。"""
    on_idx = {key: i for i, key in enumerate(on_keys)}
    dc_idx = {key: i for i, key in enumerate(dc_keys)}
    current = {}
    for key in sorted(set(on_keys) | set(dc_keys)):
        om = (1 << on_idx[key]) if key in on_idx else 0
        rm = (1 << dc_idx[key]) if key in dc_idx else 0
        current[(_key_to_ones(key), 0)] = (om, rm)
    primes = []
    while current:
        if len(current) > budget.max_implicants or time.monotonic() > deadline:
            raise BudgetExceeded
        groups = {}
        for ones, dash in current:
            groups.setdefault((dash, bin(ones).count('1')), []).append(ones)
        combined = set()
        nxt = {}
        for (dash, pc), lows in groups.items():
            ups = groups.get((dash, pc + 1))
            if not ups:
                continue
            for a in lows:
                for b in ups:
                    d = a ^ b
                    if d and not (d & (d - 1)):
                        combined.add((a, dash))
                        combined.add((b, dash))
                        key = (a & b, dash | d)
                        om_a, rm_a = current[(a, dash)]
                        om_b, rm_b = current[(b, dash)]
                        om0, rm0 = nxt.get(key, (0, 0))
                        nxt[key] = (om0 | om_a | om_b, rm0 | rm_a | rm_b)
                        if len(nxt) > budget.max_implicants:
                            raise BudgetExceeded
        for imp, masks in current.items():
            if imp not in combined and masks[0]:
                primes.append((imp[0], imp[1], masks[0], masks[1]))
        current = nxt
    primes.sort(key=lambda p: (k - bin(p[1]).count('1'), _imp_sort_key(p[0], p[1], k)))
    return primes


def _run_qm(on_keys, dc_keys, k, budget, deadline):
    if not on_keys:
        return [], True
    try:
        return _quine_mccluskey(on_keys, dc_keys, k, budget, deadline), True
    except BudgetExceeded:
        return [], False


def _enumerate_covers(n_on, primes, k, budget, deadline):
    """枚举全部达到（项数, 文字数）字典序最小的覆盖。
    返回(解列表[PI下标frozenset], 是否完备, 访问节点数)。"""
    masks = [p[2] for p in primes]
    lits = [k - bin(p[1]).count('1') for p in primes]
    covering = [[] for _ in range(n_on)]
    for pi_i, m in enumerate(masks):
        mm = m
        while mm:
            lsb = mm & (-mm)
            covering[lsb.bit_length() - 1].append(pi_i)
            mm ^= lsb
    for i in range(n_on):
        if not covering[i]:
            raise ValueError('存在不可覆盖的结果角点')
    essentials = set()
    for i in range(n_on):
        if len(covering[i]) == 1:
            essentials.add(covering[i][0])
    base_mask = 0
    base_lits = 0
    for pi_i in essentials:
        base_mask |= masks[pi_i]
        base_lits += lits[pi_i]
    order = sorted(range(len(primes)), key=lambda i: (lits[i], _imp_sort_key(primes[i][0], primes[i][1], k)))
    full = (1 << n_on) - 1
    best = [None]
    sols = []
    seen = set()
    nodes = [0]
    complete = [True]

    def dfs(uncovered, chosen, n_terms, n_lits):
        nodes[0] += 1
        if nodes[0] > budget.max_cover_nodes or time.monotonic() > deadline:
            complete[0] = False
            raise BudgetExceeded
        if not uncovered:
            key = (n_terms, n_lits)
            if best[0] is None or key < best[0]:
                best[0] = key
                sols.clear()
                seen.clear()
            if key == best[0] and chosen not in seen:
                if len(sols) >= budget.max_solutions:
                    complete[0] = False
                    raise BudgetExceeded
                seen.add(chosen)
                sols.append(chosen)
            return
        if best[0] is not None:
            if n_terms + 1 > best[0][0]:
                return
            if n_terms + 1 == best[0][0] and n_lits >= best[0][1]:
                return
        pick = -1
        pick_count = None
        m = uncovered
        while m:
            lsb = m & (-m)
            i = lsb.bit_length() - 1
            cnt = len(covering[i])
            if pick_count is None or cnt < pick_count:
                pick_count = cnt
                pick = i
            m ^= lsb
        for pi_i in order:
            if (masks[pi_i] >> pick) & 1:
                dfs(uncovered & ~masks[pi_i], chosen | {pi_i}, n_terms + 1, n_lits + lits[pi_i])

    try:
        dfs(full & ~base_mask, frozenset(essentials), len(essentials), base_lits)
    except BudgetExceeded:
        pass
    sols.sort(key=lambda s: sorted(s))
    return sols, complete[0], nodes[0]


def _mask_keys(mask, keys):
    out = []
    i = 0
    while mask:
        if mask & 1:
            out.append(keys[i])
        mask >>= 1
        i += 1
    return out


def _pi_literals(ones, dash, conditions):
    return {c: (ones >> j) & 1 for j, c in enumerate(conditions) if not (dash >> j) & 1}


def _term_eval(lit_idx, eval_cases, sum_wy):
    """单解项在评估案例集上的模糊指标与反例。"""
    num = den = 0.0
    devs = []
    mems = []
    n_gt = 0
    for case in eval_cases:
        m = 1.0
        for j, d in lit_idx.items():
            v = case['x'][j]
            m = min(m, v if d else 1.0 - v)
        mems.append(m)
        if m > 0.5:
            n_gt += 1
        w = case['w']
        y = case['y']
        num += w * min(m, y)
        den += w * m
        if m > y and w > 0:
            devs.append({'id': case['id'],
                         'term_membership': _r(m),
                         'outcome_membership': _r(y),
                         'gap_weighted': _r(w * (m - y))})
    devs.sort(key=lambda d: (-d['gap_weighted'], d['id']))
    stats = {'consistency': _r(num / den) if den > 0 else None,
             'raw_coverage': _r(num / sum_wy) if sum_wy > 0 else None,
             'support_weighted': _r(den),
             'n_cases_gt_half': n_gt,
             'deviating_cases': devs[:_MAX_DEVIATING],
             'n_deviating': len(devs)}
    return stats, mems


def _solution_output(cover, primes, conditions, eval_cases, sum_wy, on_keys, dc_keys, rows_by_key):
    k = len(conditions)
    terms = []
    mem_cols = []
    for pi_i in sorted(cover):
        ones, dash, on_mask, rem_mask = primes[pi_i]
        lit_idx = {j: (ones >> j) & 1 for j in range(k) if not (dash >> j) & 1}
        stats, mems = _term_eval(lit_idx, eval_cases, sum_wy)
        covers = _mask_keys(on_mask, on_keys)
        borrowed = _mask_keys(rem_mask, dc_keys)
        cases = sorted({c['id'] for key in covers for c in rows_by_key[key]['cases']})
        terms.append({'literals': _pi_literals(ones, dash, conditions),
                      'n_literals': k - bin(dash).count('1'),
                      **stats,
                      'covers_configurations': covers,
                      'cases': cases,
                      'borrowed_remainders': borrowed})
        mem_cols.append(mems)
    sol_num = sol_den = 0.0
    for i, case in enumerate(eval_cases):
        m = max(col[i] for col in mem_cols)
        sol_num += case['w'] * min(m, case['y'])
        sol_den += case['w'] * m
    sol_cons = sol_num / sol_den if sol_den > 0 else None
    sol_cov = sol_num / sum_wy if sum_wy > 0 else None
    for t_i, term in enumerate(terms):
        num_wo = 0.0
        for i, case in enumerate(eval_cases):
            m = max((col[i] for j, col in enumerate(mem_cols) if j != t_i), default=0.0)
            num_wo += case['w'] * min(m, case['y'])
        cov_wo = num_wo / sum_wy if sum_wy > 0 else None
        term['unique_coverage'] = _r(sol_cov - cov_wo) if sol_cov is not None else None
    return {'terms': terms,
            'solution_consistency': _r(sol_cons),
            'solution_coverage': _r(sol_cov),
            'borrowed_remainders': sorted({key for t in terms for key in t['borrowed_remainders']}),
            'cases': sorted({c for t in terms for c in t['cases']})}


def _core_peripheral(solutions):
    """所有最小解共同使用的文字（核心）与仅部分解使用的文字（边缘）。"""
    lit_sets = []
    for sol in solutions:
        s = set()
        for term in sol['terms']:
            for c, v in term['literals'].items():
                s.add((c, v))
        lit_sets.append(s)
    if not lit_sets:
        return [], []
    core = set.intersection(*lit_sets)
    peripheral = set.union(*lit_sets) - core
    return ([{c: v} for c, v in sorted(core)],
            [{c: v} for c, v in sorted(peripheral)])


def _assemble_family(primes, pi_complete, on_keys, dc_keys, conditions, eval_cases,
                     sum_wy, rows_by_key, budget, deadline, notes=()):
    k = len(conditions)
    family = {'status': STATUS_COMPLETE,
              'notes': list(notes),
              'prime_implicants': [],
              'prime_implicants_complete': pi_complete,
              'minimal_criterion': ['项数', '文字数'],
              'enumeration_complete': False,
              'nodes_visited': 0,
              'solutions': [],
              'core_literals': None,
              'peripheral_literals': None}
    if not on_keys:
        family.update({'status': STATUS_UNAVAILABLE,
                       'reason': '真值表中没有达到频数与一致性门槛的结果行',
                       'prime_implicants_complete': True,
                       'enumeration_complete': True})
        return family
    if not pi_complete:
        family.update({'status': STATUS_UNAVAILABLE,
                       'reason': '质蕴涵项生成超出资源预算，已认证范围不含本解族'})
        return family
    family['prime_implicants'] = [
        {'literals': _pi_literals(ones, dash, conditions),
         'n_literals': k - bin(dash).count('1'),
         'covers_configurations': _mask_keys(om, on_keys),
         'borrowed_remainders': _mask_keys(rm, dc_keys)}
        for ones, dash, om, rm in primes]
    sols, complete, nodes = _enumerate_covers(len(on_keys), primes, k, budget, deadline)
    family['enumeration_complete'] = complete
    family['nodes_visited'] = nodes
    family['solutions'] = [_solution_output(cover, primes, conditions, eval_cases, sum_wy,
                                            on_keys, dc_keys, rows_by_key) for cover in sols]
    if not complete:
        family['status'] = STATUS_PARTIAL
        family['notes'].append('解枚举超出资源预算；当前解为已找到的最优者，'
                               '未获完备认证，核心/边缘条件不给出')
        return family
    core, peripheral = _core_peripheral(family['solutions'])
    family['core_literals'] = core
    family['peripheral_literals'] = peripheral
    return family


def _expectation_ok(imp, de_idx):
    ones, dash = imp[0], imp[1]
    for j, v in de_idx.items():
        if not (dash >> j) & 1 and ((ones >> j) & 1) != v:
            return False
    return True


def _merge_primes(pi_lists, k):
    acc = {}
    for imp in pi_lists:
        ones, dash, om, rm = imp
        om0, rm0 = acc.get((ones, dash), (0, 0))
        acc[(ones, dash)] = (om0 | om, rm0 | rm)
    merged = [(ones, dash, om, rm) for (ones, dash), (om, rm) in acc.items()]
    merged.sort(key=lambda p: (k - bin(p[1]).count('1'), _imp_sort_key(p[0], p[1], k)))
    return merged


def _certification(families):
    cert = {'truth_table': 'complete', 'necessity': 'complete'}
    for name, fam in families.items():
        cert[name] = {'status': fam['status'],
                      'prime_implicants_complete': fam['prime_implicants_complete'],
                      'enumeration_complete': fam['enumeration_complete']}
    return cert


def analyze_snapshot(snapshot, options=None):
    """执行一次模糊集组态分析。options见docs/fsqca.md；缺测不补0，排除范围随结果公开。"""
    meta = snapshot.metadata
    resolved, conditions, budget = _resolve_options(meta, options)
    k = len(conditions)
    deadline = time.monotonic() + budget.time_seconds
    eval_cases, excluded = _prepare_cases(snapshot, conditions, resolved['negated_outcome'])
    rows, ambiguous = _build_truth_table(eval_cases, k,
                                         resolved['frequency_cutoff'],
                                         resolved['consistency_cutoff'])
    rows_by_key = {r['key']: r for r in rows}
    on_keys = sorted(r['key'] for r in rows if r['classification'] == ROW_OUTCOME)
    remainder_keys = sorted(r['key'] for r in rows if r['classification'] in REMAINDER_CLASSES)
    rp = resolved['remainder_policy']
    if rp == 'all':
        dc_keys = list(remainder_keys)
    else:
        bad = sorted(set(rp['include']) - set(remainder_keys))
        if bad:
            raise ValueError(f'remainder_policy.include含非逻辑余项配置'
                             f'（仅未观察或低于频数门槛者可借用）: {bad}')
        dc_keys = sorted(rp['include'])
    sum_wy = sum(c['w'] * c['y'] for c in eval_cases)
    necessity = _necessity(eval_cases, conditions, resolved['necessity_cutoff'])
    # 共享搜索：复杂解与简约解各做一次质蕴涵生成；中间解复用两者结果，不再搜索。
    cx_primes, cx_ok = _run_qm(on_keys, [], k, budget, deadline)
    pa_primes, pa_ok = _run_qm(on_keys, dc_keys, k, budget, deadline)
    families = {}
    families['complex'] = _assemble_family(
        cx_primes, cx_ok, on_keys, [], conditions, eval_cases, sum_wy,
        rows_by_key, budget, deadline, notes=['不借用逻辑余项'])
    families['parsimonious'] = _assemble_family(
        pa_primes, pa_ok, on_keys, dc_keys, conditions, eval_cases, sum_wy,
        rows_by_key, budget, deadline,
        notes=[f"声明的余项借用政策: {json.dumps(rp, ensure_ascii=False, sort_keys=True)}"])
    families['parsimonious']['remainder_policy'] = rp
    de = resolved['directional_expectations']
    if cx_ok and pa_ok:
        de_idx = {j: de[c] for j, c in enumerate(conditions) if c in de}
        candidates = _merge_primes(
            [imp for imp in pa_primes if _expectation_ok(imp, de_idx)] + cx_primes, k)
        in_notes = ['候选＝方向预期允许的简约解质蕴涵项 ∪ 不借用反事实的复杂解质蕴涵项']
        if not de:
            in_notes.append('未声明方向预期；中间解退化为简约解口径')
        families['intermediate'] = _assemble_family(
            candidates, True, on_keys, dc_keys, conditions, eval_cases, sum_wy,
            rows_by_key, budget, deadline, notes=in_notes)
        families['intermediate']['directional_expectations'] = {c: de[c] for c in sorted(de)}
    else:
        families['intermediate'] = _assemble_family(
            [], False, on_keys, dc_keys, conditions, eval_cases, sum_wy,
            rows_by_key, budget, deadline,
            notes=['上游质蕴涵项未完备，中间解不可用'])
    return {'schema': SCHEMA_ID,
            'framing': FRAMING,
            'input_digest': snapshot.digest,
            'data_version': meta.get('data_version'),
            'calibration': meta.get('calibration'),
            'outcome_direction': 'absence' if resolved['negated_outcome'] else 'presence',
            'conditions': conditions,
            'options': resolved,
            'cases': {'total': len(eval_cases) + len(excluded),
                      'evaluated': len(eval_cases),
                      'excluded': excluded},
            'truth_table': {'assignment_excluded': ambiguous, 'rows': rows},
            'necessity': necessity,
            'solutions': families,
            'certification': _certification(families)}


def _identity(result):
    return {'input_digest': result['input_digest'],
            'data_version': result['data_version'],
            'calibration': result['calibration'],
            'outcome_direction': result['outcome_direction']}


def _family_term_map(family):
    """解族内全部最小解的项按文字集合并，用于版本对照。"""
    m = {}
    for sol in family.get('solutions', []):
        for term in sol['terms']:
            key = json.dumps(term['literals'], ensure_ascii=False, sort_keys=True)
            e = m.setdefault(key, {'literals': term['literals'], 'cases': set(),
                                   'consistency': term['consistency'],
                                   'raw_coverage': term['raw_coverage'],
                                   'in_solutions': 0})
            e['cases'].update(term['cases'])
            e['in_solutions'] += 1
    return m


def _delta(a, b):
    if a is None or b is None:
        return None
    return _r(b - a)


def diff_results(a, b):
    """对照两次分析结果：哪些路径消失、哪些新增、哪些只是支持案例变化。
    路径身份＝项的文字集；真值表行按配置键对照。要求同方向、同条件口径。"""
    if not isinstance(a, dict) or not isinstance(b, dict) \
            or a.get('schema') != SCHEMA_ID or b.get('schema') != SCHEMA_ID:
        raise ValueError('输入应为fsqca分析结果')
    if a['outcome_direction'] != b['outcome_direction']:
        raise ValueError('结果方向不同（正向/否定），不可对照；否定结果须与同方向结果对照')
    if a['conditions'] != b['conditions']:
        raise ValueError('条件口径不同，不可对照')
    opts_a, opts_b = a['options'], b['options']
    options_changed = {k: {'a': opts_a.get(k), 'b': opts_b.get(k)}
                       for k in sorted(set(opts_a) | set(opts_b))
                       if opts_a.get(k) != opts_b.get(k)}
    rows_a = {r['key']: r for r in a['truth_table']['rows']}
    rows_b = {r['key']: r for r in b['truth_table']['rows']}
    tt_changes = []
    for key in sorted(set(rows_a) | set(rows_b)):
        ra, rb = rows_a.get(key), rows_b.get(key)
        if ra is None or rb is None:
            tt_changes.append({'key': key,
                               'classification': {'from': None if ra is None else ra['classification'],
                                                  'to': None if rb is None else rb['classification']},
                               'consistency': {'from': None if ra is None else ra['consistency'],
                                               'to': None if rb is None else rb['consistency']},
                               'cases_added': [] if ra is None else sorted(c['id'] for c in ra['cases']),
                               'cases_removed': [] if rb is None else sorted(c['id'] for c in rb['cases'])})
            continue
        ca = {c['id'] for c in ra['cases']}
        cb = {c['id'] for c in rb['cases']}
        if ra['classification'] != rb['classification'] or ca != cb or ra['consistency'] != rb['consistency']:
            tt_changes.append({'key': key,
                               'classification': {'from': ra['classification'], 'to': rb['classification']},
                               'consistency': {'from': ra['consistency'], 'to': rb['consistency']},
                               'cases_added': sorted(cb - ca),
                               'cases_removed': sorted(ca - cb)})
    fams = {}
    for name in FAMILIES:
        fa, fb = a['solutions'][name], b['solutions'][name]
        entry = {'status_a': fa['status'], 'status_b': fb['status']}
        if fa['status'] == STATUS_UNAVAILABLE or fb['status'] == STATUS_UNAVAILABLE:
            entry.update({'comparable': False, 'reason': '至少一侧解族不可用'})
        else:
            ta, tb = _family_term_map(fa), _family_term_map(fb)
            persistent = []
            for key in sorted(set(ta) & set(tb)):
                ea, eb = ta[key], tb[key]
                persistent.append({'literals': ea['literals'],
                                   'cases_added': sorted(eb['cases'] - ea['cases']),
                                   'cases_removed': sorted(ea['cases'] - eb['cases']),
                                   'consistency_delta': _delta(ea['consistency'], eb['consistency']),
                                   'raw_coverage_delta': _delta(ea['raw_coverage'], eb['raw_coverage'])})
            entry.update({'comparable': True,
                          'complete': fa['enumeration_complete'] and fb['enumeration_complete'],
                          'disappeared_terms': [ta[key]['literals'] for key in sorted(set(ta) - set(tb))],
                          'new_terms': [tb[key]['literals'] for key in sorted(set(tb) - set(ta))],
                          'persistent_terms': persistent})
            if not entry['complete']:
                entry['warning'] = '至少一侧解枚举未完备，路径对照可能不完整'
        fams[name] = entry
    return {'schema': DIFF_SCHEMA_ID,
            'framing': FRAMING,
            'a': _identity(a),
            'b': _identity(b),
            'same_input': a['input_digest'] == b['input_digest'],
            'options_changed': options_changed,
            'truth_table_changes': tt_changes,
            'families': fams}
