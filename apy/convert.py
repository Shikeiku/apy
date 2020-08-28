"""Convert between formats/targets"""

import re
import base64
import markdown
import click
from bs4 import BeautifulSoup, Tag
from markdown.extensions.abbr import AbbrExtension
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.def_list import DefListExtension
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.footnotes import FootnoteExtension

from apy.config import cfg

def markdown_file_to_notes(filename):
    """Parse notes data from Markdown file

    The following example should adequately specify the syntax.

        //input.md
        model: Basic
        tags: marked

        # Note 1
        ## Front
        Question?

        ## Back
        Answer.

        # Note 2
        tag: silly-tag

        ## Front
        Question?

        ## Back
        Answer

        # Note 3
        model: NewModel
        markdown: false (default is true)

        ## NewFront
        FieldOne

        ## NewBack
        FieldTwo

        ## FieldThree
        FieldThree
    """
    defaults, notes = _parse_file(filename)

    # Parse markdown flag
    if 'markdown' in defaults:
        defaults['markdown'] = defaults['markdown'] in ('true', 'yes')
    elif 'md' in defaults:
        defaults['markdown'] = defaults['md'] in ('true', 'yes')
        defaults.pop('md')

    # Remove comma from tag list
    if 'tags' in defaults:
        defaults['tags'] = defaults['tags'].replace(',', '')

    # Add some explicit defaults (unless added in file)
    defaults = {**{
        'model': 'Basic',
        'markdown': True,
        'tags': '',
    }, **defaults}

    # Ensure each note has all necessary properties
    for note in notes:
        # Parse markdown flag
        if 'markdown' in note:
            note['markdown'] = note['markdown'] in ('true', 'yes')
        elif 'md' in note:
            note['markdown'] = note['md'] in ('true', 'yes')
            note.pop('md')

        # Remove comma from tag list
        if 'tags' in note:
            note['tags'] = note['tags'].replace(',', '')

        # note = {**defaults, **note}
        note.update({k: v for k, v in defaults.items()
                     if not k in note})

    return notes

def _parse_file(filename):
    """Get data from file"""
    defaults = {}
    notes = []
    note = {}
    codeblock = False
    field = None
    for line in open(filename, 'r'):
        if codeblock:
            if field:
                note['fields'][field] += line
            match = re.match(r'```\s*$', line)
            if match:
                codeblock = False
            continue

        match = re.match(r'```\w*\s*$', line)
        if match:
            codeblock = True
            if field:
                note['fields'][field] += line
            continue

        if not field:
            match = re.match(r'(\w+): (.*)', line)
            if match:
                k, v = match.groups()
                k = k.lower()
                if k == 'tag':
                    k = 'tags'
                note[k] = v.strip()
                continue

        match = re.match(r'(#+)\s*(.*)', line)
        if not match:
            if field:
                note['fields'][field] += line
            continue

        level, title = match.groups()

        if len(level) == 1:
            if note:
                if field:
                    note['fields'][field] = note['fields'][field].strip()
                    notes.append(note)
                else:
                    defaults.update(note)

            note = {'title': title, 'fields': {}}
            field = None
            continue

        if len(level) == 2:
            if field:
                note['fields'][field] = note['fields'][field].strip()

            if title in note:
                click.echo(f'Error when parsing {filename}!')
                raise click.Abort()

            field = title
            note['fields'][field] = ''

    if note and field:
        note['fields'][field] = note['fields'][field].strip()
        notes.append(note)

    return defaults, notes

def config_styles(plain, subfield, replace):
    plain = re.sub(r'<('+subfield+r')>([\w\s\d.,&\n]*?)</'+subfield+r'>', replace['begin'] + r'\2' + replace['end'], plain)
    return plain

def markdown_to_html(plain):
    """Convert Markdown to HTML"""
    # Don't convert if plain text is really plain
    if re.match(r"[a-zA-Z0-9æøåÆØÅ ,.?+-]*$", plain):
        return plain

    # For convenience: Escape some common LaTeX constructs
    plain = plain.replace(r"\\", r"\\\\")
    plain = plain.replace(r"\{", r"\\{")
    plain = plain.replace(r"\}", r"\\}")
    plain = plain.replace(r"*}", r"\*}")

    # Fix whitespaces in input
    plain = plain.replace("\xc2\xa0", " ").replace("\xa0", " ")

    # For convenience: Fix mathjax escaping
    plain = plain.replace(r"\[", r"\\[")
    plain = plain.replace(r"\]", r"\\]")
    plain = plain.replace(r"\(", r"\\(")
    plain = plain.replace(r"\)", r"\\)")

    # Personal styling
    if 'subfields' in cfg.keys():
        for subfield in cfg['subfields']:
            plain = config_styles(plain, subfield, cfg['subfields'][subfield])

    html = markdown.markdown(plain, extensions=[
        'tables',
        AbbrExtension(),
        CodeHiliteExtension(
            noclasses=True,
            linenums=False,
            pygments_style='friendly',
            guess_lang=False,
        ),
        DefListExtension(),
        FencedCodeExtension(),
        FootnoteExtension(),
        ], output_format="html5")

    html_tree = BeautifulSoup(html, 'html.parser')

    tag = _get_first_tag(html_tree)
    if not tag:
        if not html:
            # Add space to prevent input field from shrinking in UI
            html = "&nbsp;"
        html_tree = BeautifulSoup(f"<div>{html}</div>", "html.parser")
        tag = _get_first_tag(html_tree)

    # Store original text as data-attribute on tree root
    # Note: convert newlines to <br> to make text readable in the Anki viewer
    original_html = base64.b64encode(
        plain.replace("\n", "<br />").encode('utf-8')).decode()
    tag['data-original-markdown'] = original_html

    return str(html_tree)

def plain_to_html(plain):
    """Convert plain text to html"""
    # Minor clean up
    plain = plain.replace(r'&lt;', '<')
    plain = plain.replace(r'&gt;', '>')
    plain = plain.replace(r'&amp;', '&')
    plain = plain.replace(r'&nbsp;', ' ')
    plain = re.sub(r'\<b\>\s*\<\/b\>', '', plain)
    plain = re.sub(r'\<i\>\s*\<\/i\>', '', plain)
    plain = re.sub(r'\<div\>\s*\<\/div\>', '', plain)

    # Convert newlines to <br> tags
    plain = plain.replace('\n', '<br />')

    return plain.strip()

def html_to_markdown(html):
    """Extract Markdown from generated HTML"""
    tag = _get_first_tag(BeautifulSoup(html, 'html.parser'))
    encoded_bytes = tag['data-original-markdown'].encode()
    converted = base64.b64decode(encoded_bytes).decode('utf-8')
    return converted.replace("<br>", "\n").replace("<br />", "\n")

def html_to_screen(html, pprint=True, parseable=False):
    """Convert html for printing to screen"""
    if not pprint:
        soup = BeautifulSoup(html.replace('\n', ''),
                             features='html5lib').next.next.next
        return "".join([el.prettify() if isinstance(el, Tag) else el
                        for el in soup.contents])

    html = re.sub(r'\<style\>.*\<\/style\>', '', html, flags=re.S)

    plain = html
    generated = is_generated_html(plain)
    if generated:
        plain = html_to_markdown(plain)

    # For convenience: Un-escape some common LaTeX constructs
    plain = plain.replace(r"\\\\", r"\\")
    plain = plain.replace(r"\\{", r"\{")
    plain = plain.replace(r"\\}", r"\}")
    plain = plain.replace(r"\*}", r"*}")

    plain = plain.replace(r'&lt;', '<')
    plain = plain.replace(r'&gt;', '>')
    plain = plain.replace(r'&amp;', '&')
    plain = plain.replace(r'&nbsp;', ' ')

    plain = plain.replace('<br>', '\n')
    plain = plain.replace('<br/>', '\n')
    plain = plain.replace('<br />', '\n')
    plain = plain.replace('<div>', '\n')
    plain = plain.replace('</div>', '')

    # For convenience: Fix mathjax escaping (but only if the html is generated)
    if generated:
        plain = plain.replace(r"\[", r"[")
        plain = plain.replace(r"\]", r"]")
        plain = plain.replace(r"\(", r"(")
        plain = plain.replace(r"\)", r")")

    plain = re.sub(r'\<b\>\s*\<\/b\>', '', plain)

    if not parseable:
        plain = re.sub(r'\*\*(.*?)\*\*',
                       click.style(r'\1', bold=True),
                       plain, re.S)

        plain = re.sub(r'\<b\>(.*?)\<\/b\>',
                       click.style(r'\1', bold=True),
                       plain, re.S)

        plain = re.sub(r'_(.*?)_',
                       click.style(r'\1', underline=True),
                       plain, re.S)

        plain = re.sub(r'\<i\>(.*?)\<\/i\>',
                       click.style(r'\1', underline=True),
                       plain, re.S)

    return plain.strip()

def is_generated_html(html):
    """Check if text is a generated HTML"""
    if html is None:
        return False

    tag = _get_first_tag(BeautifulSoup(html, 'html.parser'))

    return (tag is not None
            and tag.attrs is not None
            and 'data-original-markdown' in tag.attrs)


def _get_first_tag(tree):
    """Get first tag among children of tree"""
    for child in tree.children:
        if isinstance(child, Tag):
            return child

    return None
