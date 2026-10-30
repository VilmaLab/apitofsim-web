from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from vms import assets


def test_asset_versions_follow_built_content(monkeypatch, tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    script = static / "index.js"
    stylesheet = static / "index.css"
    script.write_text("old script")
    stylesheet.write_text("styles")
    monkeypatch.setattr(assets, "PACKAGE", tmp_path)
    original_script = assets.asset_version("index.js")
    original_stylesheet = assets.asset_version("index.css")
    assert assets.asset_version("index.js") == original_script

    script.write_text("new script")
    assert assets.asset_version("index.js") != original_script
    assert assets.asset_version("index.css") == original_stylesheet
    stylesheet.write_text("new styles")
    assert assets.asset_version("index.css") != original_stylesheet


def test_pages_request_versioned_scripts_and_styles():
    env = Environment(
        loader=FileSystemLoader(
            Path(__file__).resolve().parents[1] / "src/vms/templates"
        )
    )
    html = env.get_template("base.html").render(
        asset_version=assets.asset_version,
        url_for=lambda name, path: f"/static{path}",
    )
    for name in ("index.js", "index.css"):
        assert f'/static/{name}?v={assets.asset_version(name)}"' in html
    assert "@import" not in html
