import pytest

from core.domain.projects.errors import (
    InvalidProjectDescription,
    InvalidProjectName,
    InvalidProjectSettings,
    InvalidProjectSlug,
)
from core.domain.projects.value_objects import (
    CloudProvider,
    ProjectSettings,
    normalize_project_description,
    normalize_project_name,
    normalize_slug,
    slugify,
)

RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)  # built at runtime so the source has no invisible characters

# --- slugs --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Food Delivery Platform", "food-delivery-platform"),
        ("  Food   Delivery  ", "food-delivery"),
        ("Café Crème 2.0", "cafe-creme-2-0"),
        ("ACME / Payments & Ledger", "acme-payments-ledger"),
        ("über-app", "uber-app"),
        ("__init__", "init"),
    ],
)
def test_slugify_is_deterministic_and_url_safe(name: str, slug: str) -> None:
    assert slugify(name) == slug
    assert slugify(name) == slugify(name)


def test_slugs_are_capped_on_a_word_boundary() -> None:
    long_name = " ".join(["segment"] * 20)  # 159 characters
    slug = slugify(long_name)
    assert len(slug) <= 63
    assert slug.endswith("segment")
    assert not slug.endswith("-")


def test_a_cut_exactly_between_words_keeps_the_last_whole_word() -> None:
    # "abc-ddd...d-efg": the 63-character cut ends exactly after the d-word.
    name = "abc " + "d" * 59 + " efg"
    assert slugify(name) == "abc-" + "d" * 59


def test_one_overlong_word_is_hard_cut() -> None:
    assert slugify("x" * 100) == "x" * 63


@pytest.mark.parametrize("name", ["日本語のプロジェクト", "!!!", "---"])
def test_names_without_latin_characters_need_an_explicit_slug(name: str) -> None:
    with pytest.raises(InvalidProjectSlug) as raised:
        slugify(name)
    assert raised.value.details == {"reason": "cannot_derive_from_name"}


def test_explicit_slugs_are_trimmed_and_lower_cased_but_never_rewritten() -> None:
    assert normalize_slug("  Food-Delivery ") == "food-delivery"


@pytest.mark.parametrize(
    "raw", ["", "food delivery", "food_delivery", "-food", "food-", "food--x", "a" * 64, "fööd"]
)
def test_invalid_explicit_slugs(raw: str) -> None:
    with pytest.raises(InvalidProjectSlug):
        normalize_slug(raw)


# --- names and descriptions ---------------------------------------------------------------------


def test_names_are_collapsed_and_bounded() -> None:
    assert normalize_project_name("  Food   Delivery ") == "Food Delivery"
    for bad in ["", "   ", "x" * 101, "Food" + RIGHT_TO_LEFT_OVERRIDE + "Delivery", "Food" + chr(0)]:
        with pytest.raises(InvalidProjectName):
            normalize_project_name(bad)


def test_descriptions_keep_paragraphs_but_not_control_characters() -> None:
    assert normalize_project_description("  Line one\r\nLine two\n ") == "Line one\nLine two"
    assert normalize_project_description("") == ""
    with pytest.raises(InvalidProjectDescription):
        normalize_project_description("x" * 2001)
    with pytest.raises(InvalidProjectDescription):
        normalize_project_description("hidden" + chr(0x200E) + "command")


# --- settings -----------------------------------------------------------------------------------


def test_settings_round_trip() -> None:
    settings = ProjectSettings.from_dict({"cloud_provider": "aws", "currency": "eur"})
    assert settings == ProjectSettings(cloud_provider=CloudProvider.AWS, currency="EUR")
    assert ProjectSettings.from_dict(settings.to_dict()) == settings
    assert ProjectSettings.from_dict({}) == ProjectSettings()


@pytest.mark.parametrize(
    ("raw", "setting"),
    [
        ({"cloud_provider": "oracle"}, "cloud_provider"),
        ({"currency": "EURO"}, "currency"),
        ({"currency": 978}, "currency"),
        ({"region": "us-east-1"}, "region"),
        ({"__class__": "x"}, "__class__"),
    ],
)
def test_invalid_or_unknown_settings(raw: dict[str, object], setting: str) -> None:
    with pytest.raises(InvalidProjectSettings) as raised:
        ProjectSettings.from_dict(raw)
    assert raised.value.details["setting"] == setting
