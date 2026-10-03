import argparse
import ast
from pathlib import Path

import freqtrade_truth.adapters as adapters
from freqtrade_truth.adapters import JsonHttpTransport, ReadOnlyTradeAdapter
from freqtrade_truth.cli import build_parser

BLOCKED_PUBLIC_METHOD_PREFIXES = (
    "post",
    "put",
    "delete",
    "patch",
    "place",
    "cancel",
    "withdraw",
    "transfer",
    "set_leverage",
    "create_order",
)


def public_callable_names(value: type[object]) -> set[str]:
    return {
        name
        for name, member in vars(value).items()
        if not name.startswith("_") and callable(member)
    }


def test_read_only_protocol_surfaces_remain_narrow() -> None:
    assert public_callable_names(JsonHttpTransport) == {"get_json"}
    assert public_callable_names(ReadOnlyTradeAdapter) == {
        "capabilities",
        "fetch_closed_trades",
    }


def test_public_adapter_classes_do_not_expose_mutation_named_methods() -> None:
    violations: list[str] = []

    for export_name in adapters.__all__:
        exported = getattr(adapters, export_name)
        if not isinstance(exported, type):
            continue

        for method_name in public_callable_names(exported):
            if method_name.startswith(BLOCKED_PUBLIC_METHOD_PREFIXES):
                violations.append(f"{export_name}.{method_name}")

    assert violations == []


def test_adapter_http_requests_are_literal_get_only() -> None:
    adapters_dir = Path(__file__).resolve().parents[1] / "src" / "freqtrade_truth" / "adapters"
    violations: list[str] = []

    for path in sorted(adapters_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "request":
                continue
            if not node.args:
                violations.append(f"{path.name}:{node.lineno}: request without literal method")
                continue

            method = node.args[0]
            if not isinstance(method, ast.Constant) or method.value != "GET":
                violations.append(f"{path.name}:{node.lineno}: non-GET request")

    assert violations == []


def test_cli_exposes_only_reviewed_read_only_commands() -> None:
    parser = build_parser()
    subparser_actions = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]

    assert len(subparser_actions) == 1
    assert set(subparser_actions[0].choices) == {"bybit-check"}
