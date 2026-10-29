import re

from playwright.sync_api import expect

REALIZATIONS = 5

# Generous: covers Ray installing the task's runtime_env on first launch plus
# the skimmer preliminaries
RUN_TIMEOUT = 15 * 60 * 1000


def expect_content_sized_frame(page, title):
    page.wait_for_function(
        """title => {
            const frame = document.querySelector(`iframe[title="${title}"]`);
            const doc = frame?.contentDocument;
            return doc &&
                Math.abs(frame.clientHeight - doc.body.getBoundingClientRect().height) <= 1 &&
                doc.documentElement.scrollHeight <= frame.clientHeight + 1;
        }""",
        arg=title,
    )


def check_explorer_layout(page, tmp_path):
    frame = page.locator('iframe[title="Explorer plot"]')
    explorer = page.frame_locator('iframe[title="Explorer plot"]')
    body = explorer.locator("body")
    root = explorer.locator(".explorer-bokeh-frame")
    height = frame.evaluate("el => el.clientHeight")

    def expect_host_fit():
        page.wait_for_function("""() => {
            const iframe = document.querySelector('iframe[title="Explorer plot"]');
            const doc = iframe.contentDocument;
            const root = doc.querySelector('.explorer-bokeh-frame');
            const padding = parseFloat(getComputedStyle(doc.body).paddingTop) +
                parseFloat(getComputedStyle(doc.body).paddingBottom);
            return root && Math.abs(root.clientHeight - (iframe.clientHeight - padding)) <= 1 &&
                doc.documentElement.scrollHeight <= iframe.clientHeight &&
                doc.documentElement.scrollWidth <= iframe.clientWidth;
        }""")

    expect_host_fit()
    expect(root).to_have_css("overflow", "hidden")
    explorer.get_by_role("checkbox", name="Facet pathways", exact=True).check()
    explorer.get_by_role("checkbox", name="Cumulative", exact=True).check()
    explorer.get_by_role("checkbox", name="Bar chart", exact=True).check()
    explorer.get_by_role("button", name="☷", exact=True).click()
    expect(explorer.get_by_role("button", name="Hide details »")).to_be_visible()
    # Supply long details so all three regions overflow independently.
    body.evaluate("""() => {
        const details = [...Bokeh.documents[0].all_models]
            .find(model => model.css_classes?.includes('explorer-details'));
        details.children[0].text = '<div style="height:1600px">Long details</div>';
    }""")
    geometry = """() => {
        const classes = ['explorer-controls', 'explorer-center', 'explorer-details'];
        return classes.map(cls => {
            const model = [...Bokeh.documents[0].all_models]
                .find(model => model.css_classes?.includes(cls));
            const el = Bokeh.index.find_one(model).el;
            const rect = el.getBoundingClientRect();
            return [rect.x, rect.y, rect.width, rect.height, el.scrollTop];
        }).concat([[window.scrollX, window.scrollY]]);
    }"""
    for index, selector in enumerate(
        (".explorer-controls", ".explorer-center", ".explorer-details")
    ):
        region = explorer.locator(selector)
        expect(region).to_have_css("overflow", "auto")
        page.wait_for_function(
            """cls => {
                const target = document.querySelector('iframe[title="Explorer plot"]').contentWindow;
                const model = [...target.Bokeh.documents[0].all_models]
                    .find(model => model.css_classes?.includes(cls));
                const el = target.Bokeh.index.find_one(model).el;
                return el.scrollHeight > el.clientHeight + 25;
            }""",
            arg=selector[1:],
        )
        before = body.evaluate(geometry)
        outer_scroll = page.evaluate("window.scrollY")
        region.evaluate("el => el.scrollTop = 25")
        expect(region).to_have_js_property("scrollTop", 25)
        before[index][-1] = 25
        assert body.evaluate(geometry) == before
        assert page.evaluate("window.scrollY") == outer_scroll
        region.evaluate("el => el.scrollTop = 0")
    assert frame.evaluate("el => el.clientHeight") == height
    expect_host_fit()

    explorer.get_by_role("button", name="Fullscreen", exact=True).click()
    page.wait_for_function("""() => document.querySelector('iframe[title="Explorer plot"]')
        .contentDocument.fullscreenElement !== null""")
    expect(explorer.locator(".explorer-center")).to_have_css("overflow", "auto")
    body.evaluate("document.exitFullscreen()")
    page.wait_for_function("""() => document.querySelector('iframe[title="Explorer plot"]')
        .contentDocument.fullscreenElement === null""")
    expect_host_fit()
    assert frame.evaluate("el => el.clientHeight") == height

    # Resizing the host alone must resize the Explorer, without viewport changes.
    frame.evaluate("el => el.style.height = '360px'")
    expect_host_fit()
    assert frame.evaluate("el => el.clientHeight") == 360
    frame.evaluate("el => el.style.removeProperty('height')")
    page.set_viewport_size({"width": 390, "height": 844})
    expect_host_fit()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / "explorer-mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1280, "height": 720})
    expect_host_fit()


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


def test_default_run_completes(page, server, tmp_path):
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
    check_explorer_layout(page, tmp_path)

    page.click('a[href="#spectrogram"]')
    spectrogram = page.frame_locator('iframe[title="Spectrogram plot"]')
    expect(spectrogram.locator(".bk-Canvas")).not_to_have_count(0, timeout=60_000)
    expect_content_sized_frame(page, "Spectrogram plot")
    page.set_viewport_size({"width": 390, "height": 844})
    expect_content_sized_frame(page, "Spectrogram plot")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size({"width": 1280, "height": 720})

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
