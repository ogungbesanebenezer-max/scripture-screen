"""Two languages share page turns, and neither loses a line."""
import pytest

from verse_screen.app import split_evenly


@pytest.mark.parametrize("size,count", [(1, 1), (3, 1), (4, 2), (7, 3), (2, 3), (9, 3)])
def test_every_line_is_kept_in_order(size, count):
    lines = [str(i) for i in range(size)]
    pages = split_evenly(lines, count)
    assert len(pages) == count
    assert [line for page in pages for line in page.split("\n") if line] == lines


def test_no_page_is_over_three_lines_when_count_is_rounded_up():
    assert max(page.count("\n") + 1 for page in split_evenly(list("abcdefg"), 3)) <= 3
