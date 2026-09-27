"""FIFO ordering tests for the Chaoxing course queue.

The user ticks courses in the order they want them learned. The frontend keeps
that order in an array, but the backend used to iterate its own course list as
the OUTER loop, so the tick order was silently discarded and the queue ran in
whatever order Chaoxing returned courses. These tests pin the contract: the
selector order wins.
"""

from app.services.course.chaoxing.learning_manager import ChaoxingLearningManager


def _course(course_id: str, clazz_id: str, cpi: str = "1", name: str = "") -> dict:
    return {"courseId": course_id, "clazzId": clazz_id, "cpi": cpi, "courseName": name or course_id}


# Deliberately NOT in the order the user will ask for.
ALL_COURSES = [
    _course("100", "10"),
    _course("200", "20"),
    _course("300", "30"),
]


def _select(selectors):
    # _select_courses is a pure helper; no instance state is touched.
    return ChaoxingLearningManager._select_courses(None, ALL_COURSES, selectors)


def test_selection_follows_the_selector_order():
    """The tick order must decide the run order, not the backend's list order."""
    selected = _select(["300_30", "100_10", "200_20"])

    assert [course["courseId"] for course in selected] == ["300", "100", "200"]


def test_reversed_selection_reverses_the_run_order():
    forward = _select(["100_10", "200_20", "300_30"])
    backward = _select(["300_30", "200_20", "100_10"])

    assert [c["courseId"] for c in forward] == ["100", "200", "300"]
    assert [c["courseId"] for c in backward] == ["300", "200", "100"]


def test_partial_selection_keeps_the_requested_order():
    selected = _select(["200_20", "100_10"])

    assert [course["courseId"] for course in selected] == ["200", "100"]


def test_empty_selectors_return_every_course_in_backend_order():
    """No selection means "everything", where the backend order is all we have."""
    selected = _select([])

    assert [course["courseId"] for course in selected] == ["100", "200", "300"]


def test_duplicate_selectors_are_deduplicated_but_keep_first_position():
    selected = _select(["200_20", "100_10", "200_20"])

    assert [course["courseId"] for course in selected] == ["200", "100"]


def test_course_only_selector_matches_and_keeps_order():
    """A selector may omit clazz/cpi; it must still match and keep its place."""
    selected = _select(["300", "100"])

    assert [course["courseId"] for course in selected] == ["300", "100"]


def test_unmatched_selectors_are_skipped_without_reordering_the_rest():
    selected = _select(["999_99", "300_30", "100_10"])

    assert [course["courseId"] for course in selected] == ["300", "100"]


def test_wrong_clazz_does_not_match_but_the_course_still_resolves_by_id():
    """A mismatched clazz must not silently match a different class of the same course."""
    courses = [_course("100", "10"), _course("100", "11")]
    selected = ChaoxingLearningManager._select_courses(None, courses, ["100_11"])

    assert [course["clazzId"] for course in selected] == ["11"]
