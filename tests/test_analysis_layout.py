from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import expect

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def analysis_page(page):
    # Exercise the real templates, CSS and Alpine without running a simulation.
    env = Environment(
        loader=FileSystemLoader(ROOT / "src/vms/templates"),
        autoescape=select_autoescape(),
    )
    html = env.get_template("analysis/analysis.html").render(
        url_for=lambda name, path: f"/static{path}",
        completed=True,
        jobid="layout-test",
        job_status="done",
        info={"status": "done", "result": {}},
        statuses=dict.fromkeys(("queue", "skimmer", "apitof"), "done"),
    )

    def serve(route):
        path = route.request.url.split("layout.test", 1)[1]
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


def test_mobile_actions_are_compact_and_stay_at_bottom(analysis_page):
    page = analysis_page
    page.set_viewport_size({"width": 390, "height": 844})
    labels = page.locator(".page-action .action-label")
    for label in labels.all():
        expect(label).to_be_hidden()
    expect(page.get_by_role("link", name="New experiment", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Job/results", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")

    # On a short phone screen the plot can overlap the compact actions.
    page.set_viewport_size({"width": 390, "height": 360})
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    assert page.locator(".page-action").evaluate(
        "el => Math.abs(el.getBoundingClientRect().bottom - (innerHeight - 8)) < 1"
    )

    page.get_by_role("link", name="Queue", exact=True).click()
    for label in labels.all():
        expect(label).to_be_visible()
    # Desktop content must remain scrollable above the reserved action space.
    page.set_viewport_size({"width": 1280, "height": 720})
    page.locator('div[name="queue"]').evaluate("el => el.style.height = '1600px'")
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    assert page.locator('div[name="queue"]').evaluate(
        "el => el.getBoundingClientRect().bottom <= "
        "document.querySelector('.page-action').getBoundingClientRect().top"
    )
