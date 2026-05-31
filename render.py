#!/usr/bin/env python3
"""
render.py — fills template.html.j2 with a v2 report model.

The model is produced by metrics/narrative.py (verdicts + issues) and the
engines (R1..R4). Charts come from report/charts.py as {html, caption} dicts.
This module only wires data into the template; it computes nothing.
"""
from types import SimpleNamespace
from jinja2 import Environment, FileSystemLoader, select_autoescape
import os


def _ns(o):
    if isinstance(o, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in o.items()})
    if isinstance(o, list):
        return [_ns(x) for x in o]
    return o


def render(model: dict, template_dir: str, out_path: str) -> str:
    env = Environment(loader=FileSystemLoader(template_dir),
                      autoescape=select_autoescape(["html"]))
    r = _ns(model)
    r.appendix.versions = model["appendix"]["versions"]  # keep dict for .items()
    html = env.get_template("template.html.j2").render(r=r)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


if __name__ == "__main__":
    from sample_model import build_model
    here = os.path.dirname(os.path.abspath(__file__))
    out = render(build_model(), here, os.path.join(here, "report_v2_sample.html"))
    print("wrote", out)
