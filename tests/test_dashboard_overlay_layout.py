from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_overlay_layout_extension_loads_after_existing_owner_layers() -> None:
    html = _text("dashboard/career-review/site/index.html")
    assert html.index("assets/external-links.js") < html.index("assets/overlay-layout.js")


def test_overlay_close_preserves_board_scroll_and_focus_does_not_scroll() -> None:
    source = _text("dashboard/career-review/site/assets/overlay-layout.js")
    assert "overlayOpenScrollY = window.scrollY" in source
    assert "overlayOpenScrollX = window.scrollX" in source
    assert "window.scrollTo({ left: overlayOpenScrollX, top: overlayOpenScrollY" in source
    assert "card.focus({ preventScroll: true })" in source


def test_status_is_always_visible_on_right_side_of_metadata_strip() -> None:
    source = _text("dashboard/career-review/site/assets/overlay-layout.js")
    assert "owner-overlay-status-control" in source
    assert "owner-overlay-stage-select" in source
    assert "meta.append(control)" in source
    assert "margin-left: auto" in source
    assert "await moveRole(role, nextStage, nextStage === 'applied')" in source


def test_submission_cv_selector_is_expanded_above_resume_and_old_menu_removed() -> None:
    source = _text("dashboard/career-review/site/assets/overlay-layout.js")
    assert "group.querySelector('#ov-template-options')" in source
    assert "resumeWorkspace.prepend(templateGroup)" in source
    assert "menu.remove()" in source
    assert "owner-resume-selector #ov-template-options" in source
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in source
