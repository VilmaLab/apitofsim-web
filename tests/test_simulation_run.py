import re

from playwright.sync_api import expect

REALIZATIONS = 5

# Generous: covers Ray installing the task's runtime_env on first launch plus
# the skimmer preliminaries
RUN_TIMEOUT = 15 * 60 * 1000


def test_default_run_completes(page, server):
    page_errors = []
    page.on("pageerror", lambda error: page_errors.append(error))
    page.set_default_timeout(60_000)

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
    page.fill("#simulation-realizations", str(REALIZATIONS))

    page.click('button[type="submit"]')
    expect(page).to_have_url(re.compile(r"/analysis\?jobid="))

    expect(page.locator('li[data-tab="apitof"]')).to_have_attribute(
        "data-status", "done", timeout=RUN_TIMEOUT
    )

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
