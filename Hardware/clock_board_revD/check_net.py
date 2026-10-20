"""Connectivity check on the exported netlist (stands in for ERC on KiCad 7 CLI)."""
import sys
from sexp import parse, find, find1
net = parse(open(sys.argv[1]).read())
nets = find1(net, 'nets')
comps = {find1(c,'ref')[1]: find1(c,'value')[1] for c in find(find1(net,'components'),'comp')}
bad = 0
summary = {}
for n in find(nets, 'net'):
    name = find1(n, 'name')[1]
    nodes = [(find1(x,'ref')[1], find1(x,'pin')[1], (find1(x,'pintype') or [0,''])[1]) for x in find(n,'node')]
    summary[name] = nodes
    if len(nodes) < 2 and 'NC' not in [p for p in []]:
        print('SINGLE-NODE NET:', name, nodes); bad += 1
for name in sorted(summary, key=lambda k: -len(summary[k])):
    nodes = summary[name]
    print(f"{name:38s} {len(nodes):3d}  " + ' '.join(f"{r}.{p}" for r,p,_ in nodes[:14]) + (' ...' if len(nodes)>14 else ''))
print('components', len(comps), 'nets', len(summary), 'problems', bad)
