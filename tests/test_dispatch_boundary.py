"""Static generation-boundary audit plus strict local destination regression cases.

This is an import/source policy check, not a Python sandbox or OS network control.
Provider adapter unit tests are intentionally outside this production-source scan.
"""

import ast
from pathlib import Path

import pytest

from backend.config.endpoints import validate_endpoint

pytestmark = pytest.mark.privacy

ROOT = Path(__file__).resolve().parents[1]
_CONCRETE_MODULES = frozenset({"cloud_openai_compat", "ollama_slm", "mock"})
_CONCRETE_CLASSES = frozenset(
    {"CloudProvider", "CloudDestination", "OllamaProvider", "OllamaRuntimeControl", "MockProvider"}
)
_DISPATCH_METHODS = frozenset({"generate", "warm_up", "health", "list_models", "pull", "unload"})
# Step 17 reserves ``pull``/``unload``/``remove`` to the lifecycle coordinator; it is
# the single owner-approved entry point for runtime control.
_LIFECYCLE_METHODS = frozenset({"pull", "unload", "remove"})
_LIFECYCLE_FILE = Path("backend/model_management/lifecycle.py")


def _is_concrete_import(module: str, names: tuple[str, ...] = ()) -> bool:
    prefix = "backend.providers."
    if module.startswith(prefix) and module[len(prefix) :].split(".")[0] in _CONCRETE_MODULES:
        return True
    return module == "backend.providers" and bool(
        set(names) & (_CONCRETE_MODULES | _CONCRETE_CLASSES)
    )


def _resolved_import(node: ast.ImportFrom, relative: Path) -> str:
    if not node.level:
        return node.module or ""
    package = list(relative.parent.parts)
    # One leading dot keeps the current package; each additional dot ascends.
    keep = len(package) - node.level + 1
    return ".".join((*package[: max(0, keep)], *((node.module or "").split("."))))


def _dispatcher_receivers(tree: ast.AST, relative: Path) -> set[str]:
    constructors = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and _resolved_import(node, relative) == "backend.broker.orchestrator"
        for alias in node.names
        if alias.name == "GuardedDispatcher"
    }
    return {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in constructors
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _violations(source: str, relative: Path) -> list[tuple[int, str]]:
    """Inspect imports and references, including bound-method alias extraction."""
    if relative.parts[:2] == ("backend", "providers") or relative == Path(
        "backend/broker/orchestrator.py"
    ):
        return []
    tree = ast.parse(source, filename=str(relative))
    dispatcher_receivers = _dispatcher_receivers(tree, relative)
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _DISPATCH_METHODS
            and not (isinstance(node.value, ast.Name) and node.value.id in dispatcher_receivers)
            and not (relative == _LIFECYCLE_FILE and node.attr in _LIFECYCLE_METHODS)
        ):
            violations.append((node.lineno, "provider operation outside orchestrator"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _DISPATCH_METHODS
        ):
            violations.append((node.lineno, "dynamic provider operation outside orchestrator"))
        if relative.parts[0] != "backend":
            # CLI demos may construct mocks/adapters as configuration, but may
            # only use the guarded dispatcher to invoke them.
            continue
        if isinstance(node, ast.Import):
            if any(_is_concrete_import(alias.name) for alias in node.names):
                violations.append((node.lineno, "concrete provider import outside orchestrator"))
        elif isinstance(node, ast.ImportFrom):
            if _is_concrete_import(
                _resolved_import(node, relative), tuple(alias.name for alias in node.names)
            ):
                violations.append((node.lineno, "concrete provider import outside orchestrator"))
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            function = node.func
            dynamic_import = (
                isinstance(function, ast.Name)
                and function.id == "__import__"
                or isinstance(function, ast.Attribute)
                and function.attr == "import_module"
            )
            module = node.args[0].value
            if dynamic_import and isinstance(module, str) and _is_concrete_import(module):
                violations.append((node.lineno, "dynamic concrete provider import"))
    return violations


def test_production_generation_and_provider_import_boundary() -> None:
    findings: list[str] = []
    for directory in ("backend", "scripts", "evaluation/scripts"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            relative = path.relative_to(ROOT)
            findings.extend(
                f"{relative}:{line}: {reason}"
                for line, reason in _violations(path.read_text(encoding="utf-8"), relative)
            )
    assert findings == [], "\n".join(findings)


@pytest.mark.parametrize(
    "source",
    [
        "from backend.providers.mock import MockProvider\n",
        "from ..providers.ollama_slm import OllamaProvider\n",
        "import backend.providers.cloud_openai_compat as hidden\n",
        "import importlib\nprovider = importlib.import_module('backend.providers.mock')\n",
        "await provider.generate(payload, 'model', options, 1)\n",
        "dispatch = provider.warm_up\n",
        "call = getattr(provider, 'generate')\n",
    ],
)
def test_boundary_audit_rejects_import_and_call_bypasses(source: str) -> None:
    assert _violations(source, Path("backend/broker/bypass.py"))


def test_cli_may_call_the_guarded_dispatcher_for_synthetic_warmup() -> None:
    safe = (
        "from backend.broker.orchestrator import GuardedDispatcher\n"
        "dispatcher = GuardedDispatcher({})\n"
        "await dispatcher.warm_up(session, 'synthetic')\n"
    )
    unsafe = (
        "from backend.providers.mock import MockProvider\n"
        "dispatcher = MockProvider({})\n"
        "await dispatcher.warm_up(session, 'synthetic')\n"
    )
    assert not _violations(safe, Path("scripts/demo.py"))
    assert _violations(unsafe, Path("scripts/demo.py"))


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
        ("http://localhost:11434", "http://127.0.0.1:11434"),
        ("https://[::1]:11434", "https://[::1]:11434"),
    ],
)
def test_local_destinations_are_numeric_loopback(endpoint: str, expected: str) -> None:
    assert validate_endpoint(endpoint, local=True) == expected


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.invalid:11434",
        "http://localhost.example.invalid:11434",
        "http://127.0.0.1.example.invalid:11434",
        "http://0.0.0.0:11434",
        "http://192.168.1.10:11434",
        "http://169.254.169.254:11434",
        "http://2130706433:11434",
        "http://127.1:11434",
        "http://[::ffff:127.0.0.1]:11434",
        "http://user:password@127.0.0.1:11434",
        "http://127.0.0.1:11434/?token=synthetic",
        "http://127.0.0.1:11434/api/generate",
        "http://127.0.0.1:11434/../",
        "http://127.0.0.1:0",
    ],
)
def test_remote_or_ambiguous_local_endpoints_are_rejected(endpoint: str) -> None:
    with pytest.raises(ValueError, match="destination"):
        validate_endpoint(endpoint, local=True)
