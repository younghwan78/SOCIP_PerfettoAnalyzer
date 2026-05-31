from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


def render_report(model: dict[str, Any], out_path: Path | str, template_path: Path | str | None = None) -> Path:
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    template = Path(template_path) if template_path else _default_template_path()
    env = Environment(
        loader=FileSystemLoader(str(template.parent)),
        autoescape=select_autoescape(["html"]),
    )
    r = _namespace(model)
    r.appendix.versions = model["appendix"]["versions"]
    html = env.get_template(template.name).render(r=r)
    output.write_text(html, encoding="utf-8")
    return output


def _default_template_path() -> Path:
    return Path(__file__).resolve().parents[2] / "template.html.j2"


def _namespace(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{key: _namespace(child) for key, child in value.items()})
    if isinstance(value, list):
        return [_namespace(item) for item in value]
    return value
