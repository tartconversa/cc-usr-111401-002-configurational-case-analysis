"""现有业务摘要，不推断未观察到的业务关系。"""
def analyze(snapshot):
    groups = {}
    conditions = snapshot.metadata['conditions']
    for row in snapshot.rows('cases'):
        values = [row['memberships'].get(c) for c in conditions]
        if any(v is None or v == 0.5 for v in values):
            continue
        key = ''.join('1' if v > 0.5 else '0' for v in values)
        groups.setdefault(key, []).append(row['id'])
    return {k: sorted(v) for k, v in sorted(groups.items())}
