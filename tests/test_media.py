"""Media index — extraction precision + the forward-only recorder.

The precision cases below are NOT hypothetical: they're the actual junk a raw
scan of this machine's transcripts produced (2026-07-14). 96 "image paths" were
found, most of them strings inside source code and tool output that were never
rendered images. The recorder is fed only the assistant's visible text, and the
regex mirrors the chat renderer's — these tests pin that contract.
"""
import json

import pytest

from mc import media


@pytest.fixture()
def wired(tmp_path):
    media.wire(tmp_path)
    return tmp_path


@pytest.fixture()
def img(tmp_path):
    """A factory for REAL image files.

    The recorder only indexes an image that exists on disk (that check is what
    keeps code-strings like /static/icon-badge-72.png out of the gallery), so
    the tests have to put real bytes somewhere.
    """
    def _make(name='a.png'):
        p = tmp_path / name
        p.write_bytes(b'\x89PNG\r\n\x1a\n')
        return str(p)
    return _make


# ── extraction ───────────────────────────────────────────────────────────────

def test_extracts_a_rendered_image_path():
    out = media.extract(r"Here it is: C:\Users\levir\Documents\_claude\mission-control\_scratch\mc.png")
    assert out == [{'kind': 'image', 'path': r'C:\Users\levir\Documents\_claude\mission-control\_scratch\mc.png'}]


def test_extracts_a_mermaid_diagram():
    out = media.extract("Look:\n\n```mermaid\nflowchart TD\n  A --> B\n```\n")
    assert len(out) == 1
    assert out[0]['kind'] == 'diagram'
    assert 'flowchart TD' in out[0]['source']


def test_ignores_a_url_not_a_file():
    # The renderer's negative lookahead forbids `://` — a phantom drive letter.
    assert media.extract("see https://example.com/logo.png") == []


def test_ignores_a_relative_path():
    # `/static/icon-badge-72.png` IS absolute-looking and the old raw scan
    # matched it — but as a bare token in prose it is a URL path, not a file.
    # We accept it only when it looks like a real absolute fs path; a leading
    # slash alone is how the renderer treats POSIX paths, so assert the SHAPE
    # we actually promise: relative paths never match.
    assert media.extract("edit static/icon-badge-72.png please") == []
    assert media.extract("./assets/x.png and ../up/y.png") == []


def test_image_inside_a_diagram_body_is_not_indexed_twice():
    text = "```mermaid\nflowchart TD\n  A[/tmp/pic.png] --> B\n```"
    out = media.extract(text)
    assert [o['kind'] for o in out] == ['diagram']       # no stray image entry


def test_no_media_is_the_common_fast_path():
    assert media.extract("Just some prose about a png file format.") == []
    assert media.extract("") == []


# ── recorder ─────────────────────────────────────────────────────────────────

def test_an_image_that_does_not_exist_is_not_indexed(wired):
    """The decisive precision filter — this is the raw-scan junk, verbatim."""
    n = media.record_from_text('p', 's', "see /static/icon-badge-72.png in the source")
    assert n == 0
    assert media.list_media('p') == []


def test_record_and_list_roundtrip(wired, img):
    a = img('a.png')
    n = media.record_from_text('proj', 'sess1', f"shot: {a}", task='do a thing')
    assert n == 1
    items = media.list_media('proj')
    assert len(items) == 1
    assert items[0]['kind'] == 'image'
    assert items[0]['path'] == a
    assert items[0]['session_id'] == 'sess1'
    assert items[0]['task'] == 'do a thing'
    assert items[0]['ts'] > 0


def test_a_diagram_needs_no_file_on_disk(wired):
    n = media.record_from_text('p', 's', "```mermaid\nflowchart TD\n  A --> B\n```")
    assert n == 1
    assert media.list_media('p')[0]['kind'] == 'diagram'


def test_list_dedupes_and_is_newest_first(wired, img):
    one, two = img('one.png'), img('two.png')
    media.record_from_text('p', 's', one)
    media.record_from_text('p', 's', two)
    media.record_from_text('p', 's', one)     # same image again
    items = media.list_media('p')
    assert [i['path'] for i in items] == [one, two]


def test_projects_are_isolated(wired, img):
    a, b = img('a.png'), img('b.png')
    media.record_from_text('alpha', 's', a)
    media.record_from_text('beta', 's', b)
    assert [i['path'] for i in media.list_media('alpha')] == [a]
    assert [i['path'] for i in media.list_media('beta')] == [b]


def test_unknown_project_is_empty_not_an_error(wired):
    assert media.list_media('never-seen') == []


def test_a_torn_line_does_not_kill_the_read(wired, img):
    a, b = img('a.png'), img('b.png')
    media.record_from_text('p', 's', a)
    with open(media._index_path('p'), 'a', encoding='utf-8') as f:
        f.write('{"kind": "image", "pa\n')        # crash mid-write
    media.record_from_text('p', 's', b)
    assert {i['path'] for i in media.list_media('p')} == {a, b}


def test_recorder_never_raises_when_unwired(monkeypatch):
    monkeypatch.setattr(media, 'MEDIA_DIR', None)
    assert media.record_from_text('p', 's', "/tmp/a.png") == 0
    assert media.list_media('p') == []


def test_clear(wired, img):
    media.record_from_text('p', 's', img('a.png'))
    assert media.list_media('p')
    assert media.clear('p') is True
    assert media.list_media('p') == []


# ── the LOAD-BEARING rule: never write into DATA_DIR ─────────────────────────

def test_index_is_not_written_into_the_projects_dir(wired, img):
    """data/projects/ is scanned as project records — a stray file there 500s
    the restart endpoints. The index must land in data/media/."""
    a = img('a.png')
    media.record_from_text('p', 's', a)
    projects_dir = wired / 'data' / 'projects'
    assert not projects_dir.exists() or not list(projects_dir.glob('*'))
    written = list((wired / 'data' / 'media').glob('*.jsonl'))
    assert len(written) == 1
    assert json.loads(written[0].read_text(encoding='utf-8').splitlines()[0])['path'] == a
