from pathlib import Path
from urllib.parse import urlsplit

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import expect

from vms.assets import asset_version

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def analysis_page(page):
    # Exercise the real templates, CSS and Alpine without running a simulation.
    env = Environment(
        loader=FileSystemLoader(ROOT / "src/vms/templates"),
        autoescape=select_autoescape(),
    )
    env.globals["asset_version"] = asset_version
    html = env.get_template("analysis/analysis.html").render(
        url_for=lambda name, path: f"/static{path}",
        completed=True,
        jobid="layout-test",
        job_status="done",
        info={"status": "done", "result": {}},
        statuses=dict.fromkeys(("queue", "skimmer", "apitof"), "done"),
    )

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/static/"):
            route.fulfill(path=ROOT / "src/vms" / path.lstrip("/"))
        elif path.startswith("/analysis/results/"):
            route.fulfill(body="<!doctype html><title>Plot</title>")
        else:
            route.fulfill(body=html, content_type="text/html")

    page.route("http://layout.test/**", serve)
    page.goto("http://layout.test/#explorer")
    expect(page.locator('iframe[title="Explorer plot"]')).to_be_visible()
    return page


@pytest.mark.parametrize("height", [720, 1600])
def test_explorer_fills_available_height(analysis_page, height):
    page = analysis_page
    page.set_viewport_size({"width": 1280, "height": height})
    page.wait_for_function("""() => {
        const frame = document.querySelector('iframe[title="Explorer plot"]');
        const actions = document.querySelector('.page-action').getBoundingClientRect();
        const plot = frame.getBoundingClientRect();
        const content = document.querySelector('#default-tab-content');
        const padding = parseFloat(getComputedStyle(content).paddingBottom) +
            parseFloat(getComputedStyle(frame.parentElement).paddingBottom);
        return Math.abs(actions.bottom - (innerHeight - 16)) < 1 &&
            Math.abs(plot.bottom - (content.getBoundingClientRect().bottom - padding)) < 1 &&
            actions.top - plot.bottom >= 0 && actions.top - plot.bottom <= 64 &&
            document.documentElement.scrollHeight <= innerHeight;
    }""")
    expect(page.locator(".page-action .action-label").first).to_be_visible()


def test_mobile_explorer_overlays(analysis_page):
    page = analysis_page
    toggle = page.get_by_role("button", name="Toggle Explorer controls", exact=True)
    for width, height in ((390, 844), (844, 390), (932, 430)):
        page.set_viewport_size({"width": width, "height": height})
        expect(toggle).to_be_visible()
        expect(page.locator("#menu-panel")).to_be_hidden()
        expect(page.locator("#actions-panel")).to_be_hidden()
        frame = page.locator('iframe[title="Explorer plot"]').bounding_box()
        assert frame["y"] <= 20
        assert frame["y"] + frame["height"] >= height - 20
        assert page.evaluate("document.documentElement.scrollHeight <= innerHeight")
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(page.get_by_role("link", name="Queue", exact=True)).to_be_visible()
        expect(
            page.get_by_role("link", name="New experiment", exact=True)
        ).to_be_visible()
        expect(page.locator(".top-tabs")).to_have_css("flex-direction", "row")
        assert page.locator(".top-tabs").bounding_box()["y"] == 8
        actions = page.locator(".page-action").bounding_box()
        assert actions["y"] + actions["height"] == height - 8
        assert page.locator('iframe[title="Explorer plot"]').bounding_box() == frame
        toggle.click()
        expect(page.locator("#menu-panel")).to_be_hidden()
        expect(page.locator("#actions-panel")).to_be_hidden()

    toggle.press("Enter")
    expect(page.locator("#menu-panel")).to_be_visible()
    toggle.press("Escape")
    expect(page.locator("#menu-panel")).to_be_hidden()
    expect(toggle).to_be_focused()
    toggle.click()
    page.get_by_role("link", name="Queue", exact=True).click()
    expect(toggle).to_be_hidden()
    expect(page.locator(".top-tabs")).to_be_visible()


def test_explorer_toggle_drag_does_not_toggle_controls(analysis_page):
    page = analysis_page
    page.set_viewport_size({"width": 390, "height": 844})
    toggle = page.get_by_role("button", name="Toggle Explorer controls", exact=True)
    before = toggle.bounding_box()
    x, y = before["x"] + 28, before["y"] + 28
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 150, y - 200, steps=10)
    page.mouse.up()
    after = toggle.bounding_box()
    assert after["x"] == before["x"] + 150
    assert after["y"] == before["y"] - 200
    expect(toggle).to_have_attribute("aria-expanded", "false")
    toggle.click()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    toggle.press("ArrowRight")
    assert toggle.bounding_box()["x"] == after["x"] + 8
    page.set_viewport_size({"width": 844, "height": 390})
    bounds = toggle.bounding_box()
    assert 8 <= bounds["y"] <= 390 - bounds["height"] - 8


def test_actions_collapse_only_when_labels_do_not_fit(analysis_page):
    page = analysis_page
    page.set_viewport_size({"width": 1280, "height": 720})
    page.get_by_role("link", name="Queue", exact=True).click()
    row = page.locator(".page-action")
    labels = row.locator(".action-label")
    for width, icons in ((390, "false"), (320, "true"), (390, "false")):
        page.set_viewport_size({"width": width, "height": 844})
        expect(row).to_have_attribute("data-icons", icons)
        bounds = row.locator("a").evaluate_all(
            "els => els.map(el => el.getBoundingClientRect().top)"
        )
        assert len(set(bounds)) == 1
        for label in labels.all():
            if icons == "true":
                expect(label).to_be_hidden()
            else:
                expect(label).to_be_visible()
    # The threshold follows actual label width, even on a desktop viewport.
    page.set_viewport_size({"width": 1280, "height": 720})
    labels.first.evaluate("el => el.textContent = 'Long action label '.repeat(20)")
    expect(row).to_have_attribute("data-icons", "true")
    labels.first.evaluate("el => el.textContent = 'New experiment'")
    expect(row).to_have_attribute("data-icons", "false")
    page.locator('div[name="queue"]').evaluate("el => el.style.height = '1600px'")
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    assert page.locator('div[name="queue"]').evaluate(
        "el => el.getBoundingClientRect().bottom <= "
        "document.querySelector('.page-action').getBoundingClientRect().top"
    )
