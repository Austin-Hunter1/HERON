"""Minimal S-expression reader/writer for KiCad files."""
import re
TOK = re.compile(r'\s*(\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)')
class Sym(str):
    """Unquoted atom."""
def parse(text):
    pos = 0; stack = [[]]
    for m in TOK.finditer(text):
        t = m.group(1)
        if t == '(':
            stack.append([])
        elif t == ')':
            x = stack.pop(); stack[-1].append(x)
        elif t.startswith('"'):
            stack[-1].append(re.sub(r'\\(.)', lambda m: '\n' if m.group(1) == 'n' else m.group(1), t[1:-1]))
        else:
            stack[-1].append(Sym(t))
    return stack[0][0]
def dump(x, ind=0):
    if isinstance(x, list):
        simple = all(not isinstance(e, list) for e in x)
        if simple:
            return '(' + ' '.join(dump(e) for e in x) + ')'
        out = '('
        for i, e in enumerate(x):
            if isinstance(e, list):
                out += '\n' + '  ' * (ind + 1) + dump(e, ind + 1)
            else:
                out += (' ' if i else '') + dump(e)
        return out + ')'
    if isinstance(x, Sym):
        return str(x)
    if isinstance(x, bool):
        return 'yes' if x else 'no'
    if isinstance(x, (int, float)):
        s = ('%.4f' % x).rstrip('0').rstrip('.') if isinstance(x, float) else str(x)
        return '0' if s in ('-0', '') else s
    s = str(x).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return '"' + s + '"'
def find(x, key):
    return [e for e in x if isinstance(e, list) and e and e[0] == key]
def find1(x, key):
    r = find(x, key); return r[0] if r else None
