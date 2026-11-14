"""模糊集组态分析入口：绑定资料身份，输出充分/必要分析与多解最小化结果。

用法：
  python -m app.fsqca_service analyze 资料.json [选项.json] [--negated|--both]
  python -m app.fsqca_service diff 结果A.json 结果B.json

否定结果（--negated/--both）在 1-y 上完整重算，不由正向结论取反。
"""
import json
import sys
from pathlib import Path
from .catalog import load
from .fsqca import analyze_snapshot, diff_results

SCHEMAS = {'cases': ['memberships', 'outcome', 'weight']}

_USAGE = ('用法:\n'
          '  python -m app.fsqca_service analyze 资料.json [选项.json] [--negated|--both]\n'
          '  python -m app.fsqca_service diff 结果A.json 结果B.json')


def analyze(payload, options=None):
    """对业务资料负载执行一次模糊集组态分析（默认正向结果）。"""
    return analyze_snapshot(load(payload, SCHEMAS), options)


def diff(result_a, result_b):
    """对照两次分析结果：路径消失/新增/仅支持案例变化。"""
    return diff_results(result_a, result_b)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        raise SystemExit(_USAGE)
    cmd, rest = args[0], args[1:]
    if cmd == 'analyze':
        flags = {a for a in rest if a.startswith('--')}
        paths = [a for a in rest if not a.startswith('--')]
        if not 1 <= len(paths) <= 2 or flags - {'--negated', '--both'}:
            raise SystemExit(_USAGE)
        payload = json.loads(Path(paths[0]).read_text(encoding='utf-8'))
        options = json.loads(Path(paths[1]).read_text(encoding='utf-8')) if len(paths) == 2 else None
        if '--both' in flags:
            base = dict(options or {})
            out = {'positive': analyze(payload, {**base, 'negated_outcome': False}),
                   'negated': analyze(payload, {**base, 'negated_outcome': True})}
        elif '--negated' in flags:
            out = analyze(payload, {**(options or {}), 'negated_outcome': True})
        else:
            out = analyze(payload, options)
    elif cmd == 'diff':
        if len(rest) != 2:
            raise SystemExit(_USAGE)
        out = diff(json.loads(Path(rest[0]).read_text(encoding='utf-8')),
                   json.loads(Path(rest[1]).read_text(encoding='utf-8')))
    else:
        raise SystemExit(_USAGE)
    print(json.dumps(out, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == '__main__':
    main()
