import re

from playwright.sync_api import expect

REALIZATIONS = 5

# Generous: covers Ray installing the task's runtime_env on first launch plus
# the skimmer preliminaries
RUN_TIMEOUT = 15 * 60 * 1000


def submit_experiment(page, server, realizations):
    page.goto(server)

    # Pick the one cluster which has fragmentation pathways in the test database
    page.click('a[href="#fragmentationpathways"]')
    page.select_option("#cluster", index=1)
    # Wait for the htmx swap of /fragments/pathways to land
    expect(page.locator("#mass-spectrogram")).to_be_visible()
    expect(
        page.locator('input[name^="pathways-"][name$="-enabled"]')
    ).not_to_have_count(0)

    # Everything else stays at its default
    page.click('a[href="#simulation"]')
    page.fill("#simulation-realizations", str(realizations))

    page.click('button[type="submit"]')


def test_default_run_completes(page, server):
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(error))
    page.set_default_timeout(60_000)

    submit_experiment(page, server, REALIZATIONS)
    expect(page).to_have_url(re.compile(r"/analysis\?jobid="))
    expect(page.locator('a[href="#explorer"]')).to_have_count(0)
    expect(page.locator('a[href="#spectrogram"]')).to_have_count(0)

    expect(page.locator('li[data-tab="apitof"]')).to_have_attribute(
        "data-status", re.compile(r"^(done|incomplete)$"), timeout=RUN_TIMEOUT
    )
    if (
        page.locator('li[data-tab="apitof"]').get_attribute("data-status")
        == "incomplete"
    ):
        expect(page.get_by_role("alert")).to_contain_text(
            "Simulation output incomplete"
        )
        expect(page.get_by_role("alert")).to_contain_text("Partial results")

    for tab in ("explorer", "spectrogram"):
        expect(page.locator(f'a[href="#{tab}"]')).to_be_visible()

    page.click('a[href="#explorer"]')
    explorer = page.frame_locator('iframe[title="Explorer plot"]')
    expect(explorer.locator(".bk-Canvas")).not_to_have_count(0, timeout=60_000)

    page.click('a[href="#spectrogram"]')
    spectrogram = page.frame_locator('iframe[title="Spectrogram plot"]')
    expect(spectrogram.locator(".bk-Canvas")).not_to_have_count(0, timeout=60_000)

    page.reload()
    expect(page.locator('a[href="#explorer"]')).to_be_visible()
    page.click('a[href="#explorer"]')
    expect(
        page.frame_locator('iframe[title="Explorer plot"]').locator(".bk-Canvas")
    ).not_to_have_count(0, timeout=60_000)
    page.click('a[href="#spectrogram"]')
    expect(
        page.frame_locator('iframe[title="Spectrogram plot"]').locator(".bk-Canvas")
    ).not_to_have_count(0, timeout=60_000)
    assert page.request.get(f"{server}/analysis/plots/not-a-job/explorer").status == 404

    page.click('a[href="#apitof"]')
    expect(page.locator("#iterations")).to_have_text(f"{REALIZATIONS}/{REALIZATIONS}")

    pane = page.locator('div[name="apitof"]')
    fragmented = int(re.search(r"Fragmented: (\d+)", pane.inner_text()).group(1))
    survived = int(re.search(r"Survived: (\d+)", pane.inner_text()).group(1))
    assert fragmented + survived == REALIZATIONS

    body = page.locator("body").inner_text()
    assert "Traceback" not in body
    assert "Internal Server Error" not in body
    assert page_errors == []


def test_cancel_preserves_partial_results_and_history(page, server, tmp_path):
    page.set_default_timeout(60_000)
    submit_experiment(page, server, 10_000_000)
    expect(page).to_have_url(re.compile(r"/analysis\?jobid="))
    result_url = page.url
    expect(page.locator("#iterations")).to_contain_text(
        re.compile(r"^[1-9]"), timeout=RUN_TIMEOUT
    )

    # A second job from the same browser stays queued behind this long run.
    queued = page.context.new_page()
    submit_experiment(queued, server, REALIZATIONS)
    expect(queued).to_have_url(re.compile(r"/analysis\?jobid="))
    queued_url = queued.url
    expect(queued.locator('li[data-tab="queue"]')).to_have_attribute(
        "data-status", "processing"
    )
    queued.get_by_role("button", name="Cancel", exact=True).click()
    expect(queued.get_by_role("heading", name="Simulation cancelled")).to_be_visible()
    expect(queued.locator('li[data-tab="queue"]')).to_have_attribute(
        "data-status", "cancelled"
    )
    queued.reload()
    expect(queued.get_by_role("heading", name="Simulation cancelled")).to_be_visible()
    queued.get_by_role("link", name="Job/results", exact=True).click()
    expect(queued.locator("#job-list")).not_to_contain_text("No jobs yet")
    assert (
        queued_url.split("#")[0].split("?", 1)[1]
        not in queued.locator("#job-list").inner_html()
    )

    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(
        page.get_by_role("heading", name="Finished — cancelled early")
    ).to_be_visible(timeout=RUN_TIMEOUT)
    expect(page.locator('li[data-tab="apitof"]')).to_have_attribute(
        "data-status", "cancelled"
    )
    expect(page.get_by_role("button", name="Cancel", exact=True)).to_be_hidden()
    for tab, title in (
        ("explorer", "Explorer plot"),
        ("spectrogram", "Spectrogram plot"),
    ):
        page.locator(f'a[href="#{tab}"]').click()
        expect(
            page.frame_locator(f'iframe[title="{title}"]').locator(".bk-Canvas")
        ).not_to_have_count(0)
    page.screenshot(path=str(tmp_path / "cancelled-results.png"), full_page=True)
    page.reload()
    expect(page.locator('li[data-tab="apitof"]')).to_have_attribute(
        "data-status", "cancelled"
    )
    expect(page.locator('a[href="#explorer"]')).to_be_visible()
    expect(
        page.get_by_role("heading", name="Finished — cancelled early")
    ).to_be_visible()
    page.get_by_role("link", name="Job/results", exact=True).click()
    expect(page.locator("#job-list")).to_contain_text("Finished — cancelled early")
    expect(page.locator("#job-list a").first).to_have_attribute(
        "href", result_url.split("#")[0].replace(server, "")
    )
    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_role("link", name="New experiment", exact=True)).to_be_visible()
    page.screenshot(path=str(tmp_path / "jobs-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("link", name="New experiment", exact=True).click()
    expect(page).to_have_url(server + "/")
    expect(page.get_by_role("link", name="Job/results", exact=True)).to_be_visible()
    queued.close()
