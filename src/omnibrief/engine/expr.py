"""Safe filter expressions (design-v3 §7), e.g.
    fields.magnitude >= 4.5 or (fields.country == 'Austria' and fields.magnitude >= 3.0)

Parsed with `ast` and evaluated by a whitelist walker: no eval, no calls, no imports, no attribute
access except `fields.<name>`. Names: fields, severity, source, title, key, status. Missing values
are None; comparisons involving None are False instead of raising.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.Compare, ast.Name,
           ast.Load, ast.Constant, ast.List, ast.Tuple, ast.Attribute, *_CMP)
NAMES = {"fields", "severity", "source", "title", "key", "status"}


def compile_expr(text: str) -> ast.Expression:
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED):
            raise ValueError(f"not allowed: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in NAMES:
            raise ValueError(f"unknown name {node.id!r} (use {', '.join(sorted(NAMES))})")
        if isinstance(node, ast.Attribute) and not (isinstance(node.value, ast.Name) and node.value.id == "fields"):
            raise ValueError("attribute access is only allowed as fields.<name>")
    return tree


def evaluate(tree: ast.Expression, ctx: dict[str, Any]) -> bool:
    return bool(_eval(tree.body, ctx))


def _eval(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return ctx.get(node.id)
    if isinstance(node, ast.Attribute):
        return (ctx.get("fields") or {}).get(node.attr)
    if isinstance(node, ast.List | ast.Tuple):
        return [_eval(e, ctx) for e in node.elts]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, ctx)
        return (not v) if isinstance(node.op, ast.Not) else (-v if v is not None else None)
    if isinstance(node, ast.BoolOp):
        vals = (_eval(v, ctx) for v in node.values)
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comp in zip(node.ops, node.comparators, strict=True):
            right = _eval(comp, ctx)
            try:
                if left is None or right is None or not _CMP[type(op)](left, right):
                    return False
            except TypeError:
                return False
            left = right
        return True
    raise ValueError(f"not allowed: {type(node).__name__}")
