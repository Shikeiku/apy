#!/usr/bin/env python
"""A script to interact with the Anki database"""

import click


class Anki:
    """My Anki collection wrapper class."""

    def __init__(self, base=None):
        import os
        import sys
        from sqlite3 import OperationalError
        sys.path.append('/usr/share/anki')
        import anki
        from aqt.profiles import ProfileManager

        # Update LaTeX commands
        # (based on "Edit LaTeX build process"; addon #1546037973)
        anki.latex.pngCommands = [
            ["latex", "-interaction=nonstopmode", "tmp.tex"],
            ["dvipng", "-D", "200", "-T", "tight", "-bg", "Transparent",
             "tmp.dvi", "-o", "tmp.png"]
        ]
        anki.latex.svgCommands = [
            ["latex", "-interaction=nonstopmode", "tmp.tex"],
            ["dvisvgm", "--no-fonts", "-Z", "2", "tmp.dvi", "-o", "tmp.svg"]
        ]

        self.modified = False

        # Initialize a profile manager to get an interface to the profile
        # settings and main database path
        pm = ProfileManager(base)
        pm.setupMeta()
        pm.load(pm.profiles()[0])
        self.pm = pm

        # Load the main Anki database/collection
        save_cwd = os.getcwd()
        path = pm.collectionPath()
        try:
            self.col = anki.Collection(path)
        except AssertionError:
            click.echo('Path to database is not valid!')
            click.echo(f'path = {path}')
            raise click.Abort()
        except OperationalError:
            click.echo('Database is NA/locked!')
            raise click.Abort()

        # Restore CWD (because it get's changed by Anki)
        os.chdir(save_cwd)

        self.model_names = [m['name'] for m in self.col.models.all()]
        self.model_name_to_id = {m['name']: m['id']
                                 for m in self.col.models.all()}

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        if self.modified:
            click.echo('Database was modified.')
            click.secho('Remember to sync!', fg='blue')
            self.col.close()


    def sync(self):
        """Sync collection to AnkiWeb"""
        if not self.pm.profile['syncKey']:
            click.echo('No sync auth registered in profile')
            return

        import os
        from anki.sync import (Syncer, MediaSyncer,
                               RemoteServer, RemoteMediaServer)

        # Initialize servers and sync clients
        hkey = self.pm.profile['syncKey']
        hostNum = self.pm.profile.get('hostNum')
        server = RemoteServer(hkey, hostNum=hostNum)
        main_client = Syncer(self.col, server)
        media_client = MediaSyncer(self.col,
                                   RemoteMediaServer(self.col, hkey,
                                                     server.client,
                                                     hostNum=hostNum))

        # Perform main sync
        try:
            click.echo('Syncing deck ... ', nl=False)
            ret = main_client.sync()
        except Exception as e:
            if 'sync cancelled' in str(e):
                server.abort()
            click.secho('Error during sync!', fg='red')
            click.echo(e)
            raise click.Abort()

        # Parse return value
        if ret == "noChanges":
            click.echo('done (no changes)!')
        elif ret == "success":
            click.echo('done!')
        elif ret == "serverAbort":
            click.echo('aborted!')
            return
        elif ret == "fullSync":
            click.echo('aborted!')
            click.secho('Full sync required!', fg='red')
            return
        else:
            click.echo('failed!')
            click.echo(f'Message: {ret}')
            return

        # Perform media sync
        try:
            click.echo('Syncing media ... ', nl=False)
            save_cwd = os.getcwd()
            os.chdir(self.col.media.dir())
            ret = media_client.sync()
            os.chdir(save_cwd)
        except Exception as e:
            if "sync cancelled" in str(e):
                return
            raise

        if ret == "noChanges":
            click.echo('done (no changes)!')
        elif ret in ("sanityCheckFailed", "corruptMediaDB"):
            click.echo('failed!')
        else:
            click.echo('done!')


    def check_media(self):
        """Check media (will rebuild missing LaTeX files)"""
        # This needs more work
        self.col.media.check()


    def find_cards(self, query):
        """Find card ids in Collection that match query"""
        return self.col.findCards(query)

    def find_notes(self, query):
        """Find notes in Collection and return MyNote objects"""
        return (MyNote(self, self.col.getNote(i))
                for i in self.col.findNotes(query))

    def delete_notes(self, ids):
        """Delete notes by note ids"""
        if not isinstance(ids, list):
            ids = [ids]

        self.col.remNotes(ids)
        self.modified = True


    def set_model(self, model_string):
        """Set current model based on model name"""
        current = self.col.models.current()
        if current['name'] == model_string:
            return current

        model = self.col.models.get(self.model_name_to_id.get(model_string))
        if model is None:
            click.secho(f'Model "{model_string}" was not recognized!')
            raise click.Abort()

        self.col.models.setCurrent(model)
        return model


    def add_notes_with_editor(self, tags='', model_name=None, template=None):
        """Add new notes to collection with editor"""
        import os
        import tempfile

        if isinstance(template, MyNote):
            input_string = template.get_template()
        else:
            if model_name is None or model_name.lower() == 'ask':
                model_name = choose(sorted(self.model_names), "Choose model:")

            model = self.set_model(model_name)

            input_lines = [
                f'model: {model_name}',
                f'tags: {tags}',
            ]

            if model_name != 'Basic':
                input_lines += ['markdown: false']

            input_lines += ['\n# Note\n']

            input_lines += [x for y in
                            [[f'## {field["name"]}', ''] for field in model['flds']]
                            for x in y]
            if model_name == 'Basic':
                input_lines.insert(-3, "**CATEGORY**")

            input_string = '\n'.join(input_lines) + '\n'

        with tempfile.NamedTemporaryFile(mode='w+',
                                         dir=os.getcwd(),
                                         prefix='note_',
                                         suffix='.md',
                                         delete=False) as tf:
            tf.write(input_string)
            tf.flush()
            retcode = editor(tf.name)

            if retcode != 0:
                click.echo(f'Editor return with exit code {retcode}!')
                return []

            return self.add_notes_from_file(tf.name)

    def add_notes_from_file(self, filename, tags=''):
        """Add new notes to collection from Markdown file"""
        return self.add_notes_from_list(parse_notes_from_markdown(filename),
                                        tags)

    def add_notes_from_list(self, parsed_notes, tags=''):
        """Add new notes to collection from note list (from parsed file)"""
        notes = []
        for note in parsed_notes:
            model_name = note['model']
            model = self.set_model(model_name)
            model_field_names = [field['name'] for field in model['flds']]

            field_names = note['fields'].keys()
            field_values = note['fields'].values()

            if len(field_names) != len(model_field_names):
                click.echo(f'Error: Not enough fields for model {model_name}!')
                self.modified = False
                raise click.Abort()

            for x, y in zip(model_field_names, field_names):
                if x != y:
                    click.echo('Warning: Inconsistent field names '
                               f'({x} != {y})')

            notes.append(self._add_note(field_values,
                                        f"{tags} {note['tags']}",
                                        note['markdown']))

        return notes

    def _add_note(self, fields, tags, markdown=True):
        """Add new note to collection"""
        note = self.col.newNote()

        if markdown:
            note.fields = [markdown_to_html(x) for x in fields]
        else:
            note.fields = list(fields)

        tags = tags.strip().split()
        for tag in tags:
            note.addTag(tag)

        if not note.dupeOrEmpty():
            self.col.addNote(note)
            self.modified = True
        else:
            click.secho('Dupe detected, note was not added!', fg='red')
            click.echo('Question:')
            click.echo(list(fields)[0])

        return MyNote(self, note)


class MyNote:
    """A Note wrapper class"""

    def __init__(self, anki, note):
        self.a = anki
        self.n = note
        self.model_name = note.model()['name']
        self.fields = [x for x, y in self.n.items()]
        self.suspended = any([c.queue == -1 for c in self.n.cards()])


    def __repr__(self):
        """Convert note to Markdown format"""
        lines = [
            f'# Note ID: {self.n.id}',
            f'model: {self.model_name}',
            f'tags: {self.get_tag_string()}',
        ]

        if not any([is_generated_html(x) for x in self.n.values()]):
            lines += ['markdown: false']

        lines += ['']

        for key, val in self.n.items():
            if is_generated_html(val):
                key += ' (md)'

            lines.append('## ' + key)
            lines.append(html_to_screen(val, parseable=True))
            lines.append('')

        return '\n'.join(lines)

    def get_template(self):
        """Convert note to Markdown format as a template for new notes"""
        lines = [
            f'model: {self.model_name}',
            f'tags: {self.get_tag_string()}',
        ]

        if not any([is_generated_html(x) for x in self.n.values()]):
            lines += ['markdown: false']

        lines += ['']
        lines += ['# Note']
        lines += ['']

        for key, val in self.n.items():
            if is_generated_html(val):
                key += ' (md)'

            lines.append('## ' + key)
            lines.append(html_to_screen(val, parseable=True))
            lines.append('')

        return '\n'.join(lines)

    def print(self):
        """Print to screen (similar to __repr__ but with colors)"""
        lines = [
            click.style(f'# Note ID: {self.n.id}', fg='green'),
            click.style('model: ', fg='yellow')
            + f'{self.model_name} ({len(self.n.cards())} cards)',
            click.style('tags: ', fg='yellow') + self.get_tag_string(),
        ]

        if not any([is_generated_html(x) for x in self.n.values()]):
            lines += [f"{click.style('markdown:', fg='yellow')} false"]

        if self.suspended:
            lines[0] += f" ({click.style('suspended', fg='red')})"

        lines += ['']

        for key, val in self.n.items():
            if is_generated_html(val):
                key += ' (md)'

            lines.append(click.style('# ' + key, fg='blue'))
            lines.append(html_to_screen(val))
            lines.append('')

        click.echo('\n'.join(lines))

    def print_short(self):
        """Print short version to screen"""
        import os
        import re

        try:
            width = os.get_terminal_size()[0]
        except OSError:
            width = 120

        first_field = html_to_screen(self.n.values()[0])
        first_field = first_field.replace('\n', ' ')
        first_field = re.sub(r'\s\s\s+', ' ', first_field)
        first_field = first_field[:width-14] + click.style('', reset=True)

        if self.suspended:
            color = 'red'
        elif 'marked' in self.n.tags:
            color = 'yellow'
        else:
            color = 'green'

        model = f'{self.model_name[:13]:14s}'
        click.echo(click.style(model, fg=color) + first_field)


    def edit(self):
        """Edit tags and fields of current note"""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode='w+',
                                         dir=os.getcwd(),
                                         prefix='edit_note_',
                                         suffix='.md') as tf:
            tf.write(str(self))
            tf.flush()

            retcode = editor(tf.name)
            if retcode != 0:
                click.echo(f'Editor return with exit code {retcode}!')
                return

            notes = parse_notes_from_markdown(tf.name)

        if not notes:
            click.echo(f'Something went wrong when editing note!')
            return

        if len(notes) > 1:
            self.a.add_notes_from_list(notes[1:])
            click.confirm(f'\nAdded {len(notes) - 1} new notes while editing.'
                          '\nPress <cr> to continue.',
                          prompt_suffix='', show_default=False)

        note = notes[0]

        new_tags = note['tags'].split()
        if new_tags != self.n.tags:
            self.n.tags = new_tags

        for i, value in enumerate(note['fields'].values()):
            if note['markdown']:
                value = markdown_to_html(value)
            self.n.fields[i] = value

        self.n.flush()
        self.a.modified = True
        if self.n.dupeOrEmpty():
            click.confirm('The updated note is now a dupe!',
                          prompt_suffix='', show_default=False)

    def delete(self):
        """Delete the note"""
        self.a.delete_notes(self.n.id)


    def toggle_marked(self):
        """Toggle marked tag for note"""
        if 'marked' in self.n.tags:
            self.n.delTag('marked')
        else:
            self.n.addTag('marked')
        self.n.flush()
        self.a.modified = True

    def toggle_suspend(self):
        """Toggle suspend for note"""
        cids = [c.id for c in self.n.cards()]

        if self.suspended:
            self.a.col.sched.unsuspendCards(cids)
        else:
            self.a.col.sched.suspendCards(cids)

        self.suspended = not self.suspended
        self.a.modified = True

    def toggle_markdown(self, index=None):
        """Toggle markdown on a field"""
        if index is None:
            fields = self.fields
            field = choose(fields, 'Toggle markdown for field:')
            index = fields.index(field)

        field_value = self.n.fields[index]

        if is_generated_html(field_value):
            self.n.fields[index] = html_to_markdown(field_value)
        else:
            self.n.fields[index] = markdown_to_html(field_value)

        self.n.flush()
        self.a.modified = True


    def get_field(self, index_or_name):
        """Return field with given index or name"""
        if isinstance(index_or_name, str):
            index = self.fields.index(index_or_name)
        else:
            index = index_or_name

        reply = self.n.fields[index]

        if is_generated_html(reply):
            reply = html_to_markdown(reply)

        return reply


    def get_tag_string(self):
        """Get tag string"""
        return ', '.join(self.n.tags)


def parse_notes_from_markdown(filename):
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

    # Set default markdown flag
    def_markdown = True
    if 'markdown' in defaults:
        def_markdown = defaults['markdown'] in ('true', 'yes')
        defaults.pop('markdown')
    elif 'md' in defaults:
        def_markdown = defaults['md'] in ('true', 'yes')
        defaults.pop('md')

    if 'tags' in defaults:
        defaults['tags'] = defaults['tags'].replace(',', '')

    # Ensure each note has all necessary properties
    for note in notes:
        if 'model' not in note:
            note['model'] = defaults.get('model', 'Basic')

        if 'tags' in note:
            note['tags'] = note['tags'].replace(',', '')
        else:
            note['tags'] = defaults.get('tags', 'marked')

        if 'markdown' in note:
            note['markdown'] = note['markdown'] in ('true', 'yes')
        elif 'md' in note:
            note['markdown'] = note['md'] in ('true', 'yes')
            note.pop('md')
        else:
            note['markdown'] = def_markdown

    return notes

def _parse_file(filename):
    """Get data from file"""
    import re

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
        else:
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


def markdown_to_html(plain):
    """Convert Markdown to HTML"""
    import re
    import base64
    from bs4 import BeautifulSoup
    import markdown
    from markdown.extensions.abbr import AbbrExtension
    from markdown.extensions.codehilite import CodeHiliteExtension
    from markdown.extensions.def_list import DefListExtension
    from markdown.extensions.fenced_code import FencedCodeExtension
    from markdown.extensions.footnotes import FootnoteExtension

    # Don't convert if plain text is really plain
    if re.match(r"[a-zA-Z0-9æøåÆØÅ ,.?+-]*$", plain):
        return plain

    # Fix whitespaces in input
    plain = plain.replace("\xc2\xa0", " ").replace("\xa0", " ")

    # For convenience: Fix mathjax escaping
    plain = plain.replace(r"\[", r"\\[")
    plain = plain.replace(r"\]", r"\\]")
    plain = plain.replace(r"\(", r"\\(")
    plain = plain.replace(r"\)", r"\\)")

    html = markdown.markdown(plain, extensions=[
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

    tag = get_first_tag(html_tree)
    if not tag:
        if not html:
            # Add space to prevent input field from shrinking in UI
            html = "&nbsp;"
        html_tree = BeautifulSoup(f"<div>{html}</div>", "html.parser")
        tag = get_first_tag(html_tree)

    # Store original text as data-attribute on tree root
    # Note: convert newlines to <br> to make text readable in the Anki viewer
    original_html = base64.b64encode(
        plain.replace("\n", "<br />").encode('utf-8')).decode()
    tag['data-original-markdown'] = original_html

    return str(html_tree)

def clean_html(html):
    """Remove some extra things from html"""
    import re
    return re.sub(r'\<style\>.*\<\/style\>', '', html, flags=re.S)

def html_to_screen(html, parseable=False):
    """Convert html for printing to screen"""
    import re
    plain = html
    if is_generated_html(plain):
        plain = html_to_markdown(plain)

    plain = plain.replace(r'&lt;', '<')
    plain = plain.replace(r'&gt;', '>')
    plain = plain.replace(r'&nbsp;', ' ')

    plain = plain.replace('<br>', '\n')
    plain = plain.replace('<br/>', '\n')
    plain = plain.replace('<br />', '\n')
    plain = plain.replace('<div>', '\n')
    plain = plain.replace('</div>', '')

    # For convenience: Fix mathjax escaping
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

def html_to_markdown(html):
    """Extract Markdown from generated HTML"""
    import base64
    from bs4 import BeautifulSoup
    tag = get_first_tag(BeautifulSoup(html, 'html.parser'))
    encoded_bytes = tag['data-original-markdown'].encode()
    markdown = base64.b64decode(encoded_bytes).decode('utf-8')
    return markdown.replace("<br>", "\n").replace("<br />", "\n")

def is_generated_html(html):
    """Check if text is a generated HTML"""
    from bs4 import BeautifulSoup
    if html is None:
        return False

    tag = get_first_tag(BeautifulSoup(html, 'html.parser'))

    return (tag is not None
            and tag.attrs is not None
            and 'data-original-markdown' in tag.attrs)

def get_first_tag(tree):
    """Get first tag among children of tree"""
    from bs4 import Tag
    for child in tree.children:
        if isinstance(child, Tag):
            return child

    return None


def editor(filepath):
    """Use EDITOR to edit file at given path"""
    import os
    from subprocess import call
    return call([os.environ.get('EDITOR', 'vim'), filepath])

def edit_text(input_text, prefix=None):
    """Use EDITOR to edit text (from a temporary file)"""
    import os
    import tempfile

    if prefix is not None:
        prefix = prefix + "_"

    with tempfile.NamedTemporaryFile(mode='w+',
                                     dir=os.getcwd(),
                                     prefix=prefix,
                                     suffix=".md") as tf:
        tf.write(input_text)
        tf.flush()
        editor(tf.name)
        tf.seek(0)
        edited_message = tf.read().strip()

    return edited_message

def choose(items, text="Choose from list:"):
    """Choose from list of items"""
    import readchar

    click.echo(text)
    for i, element in enumerate(items):
        click.echo(f"{i+1}: {element}")
    click.echo("> ", nl=False)

    while True:
        choice = readchar.readchar()

        try:
            index = int(choice)
        except ValueError:
            continue

        try:
            reply = items[index-1]
            click.echo(index)
            return reply
        except IndexError:
            continue


BASE = '/home/lervag/documents/anki'

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])
@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option('-d', '--debug', is_flag=True,
              help="Use my temporary Anki base folder")
@click.pass_context
def main(ctx, debug):
    """A script to interact with the Anki database."""
    if debug:
        # pylint: disable=global-statement
        global BASE
        BASE = '/home/lervag/.local/share/Anki2'
    if ctx.invoked_subcommand is None:
        ctx.invoke(info)


@cli.command()
@click.option('-t', '--tags', default='marked',
              help='Specify tags for new cards.')
@click.option('-m', '--model', default='Basic',
              help=('Specify model for new cards.'))
def add(tags, model):
    """Add notes interactively from terminal.

    Examples:

    \b
        # Add notes with tags 'my-tag' and 'new-tag'
        apy add -t "my-tag new-tag"

    \b
        # Ask for the model for each new card
        apy add -m ASK
    """
    with Anki(BASE) as a:
        notes = a.add_notes_with_editor(tags, model)
        number_of_notes = len(notes)
        click.echo(f'Added {number_of_notes} notes')
        if click.confirm('Review added notes?'):
            for i, note in enumerate(notes):
                _review_note(a, note, i, number_of_notes,
                             remove_actions=['Abort'])


@cli.command('add-from-file')
@click.argument('file', type=click.Path(exists=True, dir_okay=False))
@click.option('-t', '--tags', default='',
              help='Specify default tags.')
def add_from_file(file, tags):
    """Add notes from Markdown file.

    For input file syntax specification, see docstring for
    parse_notes_from_markdown().
    """
    with Anki(BASE) as a:
        notes = a.add_notes_from_file(file, tags)
        number_of_notes = len(notes)
        click.echo(f'Added {number_of_notes} notes')
        if click.confirm('Review added notes?'):
            for i, note in enumerate(notes):
                _review_note(a, note, i, number_of_notes,
                             remove_actions=['Abort'])


@cli.command('check-media')
def check_media():
    """Check media"""
    with Anki(BASE) as a:
        a.check_media()


@cli.command()
def info():
    """Print some basic statistics."""
    with Anki(BASE) as a:
        click.echo(f"Collecton path:          {a.col.path}")
        click.echo(f"Scheduler version:       {a.col.schedVer()}")
        click.echo(f"Number of notes:         {a.col.noteCount()}")
        click.echo(f"Number of cards:         {a.col.cardCount()}")
        click.echo(f"Number of cards (due):   {len(a.col.findNotes('is:due'))}")
        click.echo(f"Number of marked cards:  {len(a.col.findNotes('tag:marked'))}")

        models = sorted(a.model_names)
        click.echo(f"Number of models:        {len(models)}")
        for m in models:
            click.echo(f"  - {m}")


@cli.command()
@click.option('-q', '--query', default='tag:marked',
              help=('Review cards that match query [default: marked cards].'))
def review(query):
    """Review marked notes."""
    with Anki(BASE) as a:
        notes = list(a.find_notes(query))
        number_of_notes = len(notes)
        for i, note in enumerate(notes):
            if not _review_note(a, note, i, number_of_notes):
                break

def _review_note(anki, note, i=None, number_of_notes=None,
                 remove_actions=None):
    """Review note i of n"""
    import os
    import readchar

    actions = {
        'c': 'Continue',
        'e': 'Edit',
        'd': 'Delete',
        'm': 'Toggle markdown',
        '*': 'Toggle marked',
        'z': 'Toggle suspend',
        'a': 'Add new',
        's': 'Save and stop',
        'x': 'Abort',
    }

    if remove_actions:
        actions = {key: val for key, val in actions.items()
                   if val not in remove_actions}

    while True:
        click.clear()
        if i is None:
            click.secho('Reviewing note\n', fg='white')
        elif number_of_notes is None:
            click.secho(f'Reviewing note {i+1}\n', fg='white')
        else:
            click.secho(f'Reviewing note {i+1} of {number_of_notes}\n',
                        fg='white')

        for x, y in actions.items():
            click.echo(click.style(x, fg='blue') + ': ' + y)

        width = os.get_terminal_size()[0]
        click.echo('\n' + '-'*width + '\n')

        note.print()

        choice = readchar.readchar()
        action = actions.get(choice)

        if action == 'Continue':
            return True

        if action == 'Edit':
            note.edit()
            continue

        if action == 'Delete':
            if click.confirm('Are you sure you want to delete the note?'):
                note.delete()
            return True

        if action == 'Toggle markdown':
            note.toggle_markdown()
            continue

        if action == 'Toggle marked':
            note.toggle_marked()
            continue

        if action == 'Toggle suspend':
            note.toggle_suspend()
            continue

        if action == 'Add new':
            click.echo('-'*width + '\n')

            notes = anki.add_notes_with_editor(
                tags=note.get_tag_string(),
                model_name=note.model_name,
                template=note)

            number_of_notes = len(notes)
            click.echo(f'Added {number_of_notes} notes')
            click.confirm('Press any key to continue.',
                          prompt_suffix='', show_default=False)
            continue

        if action == 'Save and stop':
            click.echo('Stopped')
            return False

        if action == 'Abort':
            if anki.modified:
                if not click.confirm(
                        'Abort: Changes will be lost. Continue [y/n]?',
                        show_default=False):
                    continue
                anki.modified = False
            raise click.Abort()


@cli.command('list')
@click.argument('query', required=False, default='tag:marked')
def list_notes(query):
    """List notes that match a given query."""
    with Anki(BASE) as a:
        for note in a.find_notes(query):
            note.print_short()


@cli.command('list-cards')
@click.argument('query', required=False, default='tag:marked')
def list_cards(query):
    """List cards that match a given query."""
    with Anki(BASE) as a:
        for cid in a.find_cards(query):
            c = a.col.getCard(cid)
            question = html_to_screen(clean_html(c.q())).replace('\n', ' ')
            # answer = html_to_screen(clean_html(c.a())).replace('\n', ' ')
            click.echo(f'lapses: {c.lapses:2d}  ease: {c.factor/10}%  Q: '
                       + question[:80])


@cli.command()
def sync():
    """Synchronize collection with AnkiWeb."""
    with Anki(BASE) as a:
        a.sync()


if __name__ == '__main__':
    # pylint: disable=no-value-for-parameter
    cli()