import markdown
from jinja2 import nodes
from jinja2.ext import Extension as JinjaExtension
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

# This file contains code cribbed from datasette which is released under an Apache license


class TailwindExtension(Extension):
    """An extension to add classes to tags"""

    def extendMarkdown(self, md):
        # Priority 15: after the inline processor (20) so that inline-generated
        # tags such as links exist, but before prettify (10).
        md.treeprocessors.register(TailwindTreeProcessor(md), "tailwind", 15)


class TailwindTreeProcessor(Treeprocessor):
    """Walk the root node and modify any discovered tag"""

    classes = {
        "a": "text-blue-600 visited:text-purple-600 hover:underline",
        "p": "mb-4 last:mb-0",
        "ul": "mb-4 list-disc space-y-1 pl-4 last:mb-0",
        "ol": "mb-4 list-decimal space-y-1 pl-4 last:mb-0",
        "li": "pl-1",
        "dl": "mb-4 last:mb-0",
        "dt": "font-semibold",
        "dd": "mb-2 pl-4 last:mb-0",
    }

    def run(self, root):
        for node in root.iter():
            tag_classes = self.classes.get(node.tag)
            if tag_classes:
                node.attrib["class"] = tag_classes


# Markdown instances hold parsing state as well as the extension instances, so
# reuse one rather than rebuilding it for every block on every render.
_converter = markdown.Markdown(
    extensions=[TailwindExtension(), "def_list"],
    output_format="html5",
)


class MarkdownExtension(JinjaExtension):
    tags = {"markdown"}

    def parse(self, parser):
        # We need this for reporting errors
        lineno = next(parser.stream).lineno
        body = parser.parse_statements(["name:endmarkdown"], drop_needle=True)
        return nodes.CallBlock(
            self.call_method("_render_markdown"),
            [],
            [],
            body,
        ).set_lineno(lineno)

    def _render_markdown(self, caller):
        return _converter.convert(caller())
