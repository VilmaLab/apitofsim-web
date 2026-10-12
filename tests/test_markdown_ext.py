import pytest
from jinja2 import Environment, TemplateSyntaxError

from vms.markdown_ext import MarkdownExtension


@pytest.fixture
def render():
    # Same setup as the app: autoescaping, extension registered on the environment
    env = Environment(extensions=[MarkdownExtension], autoescape=True)

    def render(source, **context):
        return env.from_string(source).render(context)

    return render


def test_inline_markdown(render):
    html = render(
        "{% markdown %}\n# Title\n\nSome *emphasis* and a [link](https://example.com).\n{% endmarkdown %}"
    )
    assert "<h1>Title</h1>" in html
    assert "<em>emphasis</em>" in html
    assert (
        '<a class="text-blue-600 visited:text-purple-600 hover:underline"'
        ' href="https://example.com">link</a>' in html
    )


def test_list_styles(render):
    unordered = render("{% markdown %}\n- one\n- two\n{% endmarkdown %}")
    assert '<ul class="mb-4 list-disc space-y-1 pl-4 last:mb-0">' in unordered
    assert '<li class="pl-1">one</li>' in unordered

    ordered = render("{% markdown %}\n1. one\n2. two\n{% endmarkdown %}")
    assert '<ol class="mb-4 list-decimal space-y-1 pl-4 last:mb-0">' in ordered


def test_definition_list_styles(render):
    html = render("{% markdown %}\nTerm\n: Definition\n{% endmarkdown %}")
    assert '<dl class="mb-4 last:mb-0">' in html
    assert '<dt class="font-semibold">Term</dt>' in html
    assert '<dd class="mb-2 pl-4 last:mb-0">Definition</dd>' in html


def test_paragraph_in_list_item_is_last_child(render):
    # A nested list makes markdown wrap the preceding item in a <p>, which must
    # not add its own bottom margin on top of the list spacing
    html = render("{% markdown %}\n- one\n\n  - nested\n{% endmarkdown %}")
    assert '<p class="mb-4 last:mb-0">' in html


def test_tag_takes_no_attributes(render):
    with pytest.raises(TemplateSyntaxError):
        render('{% markdown extensions="tables" %}x{% endmarkdown %}')


def test_converter_is_reused(render):
    source = "{% markdown %}\n# Title\n\n- one\n{% endmarkdown %}"
    assert render(source) == render(source)
