import json
from pathlib import Path

import pytest

from scripts.build_sdl_property_history import build_artifact


_ROOT = Path(__file__).resolve().parents[1]
_PRIVATE_KEYS = {"owner", "mailing", "mailing_address", "agent", "permit_agent"}


def _read_json(path):
    return json.loads((_ROOT / path).read_text(encoding="utf-8"))


def _private_key_paths(value, path="$"):
    matches = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key.casefold() in _PRIVATE_KEYS:
                matches.append(child_path)
            matches.extend(_private_key_paths(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(_private_key_paths(child, f"{path}[{index}]"))
    return matches


def _territory(*pins):
    return {
        "features": [
            {
                "properties": {
                    "PAMS_PIN": pin,
                    "PCLBLOCK": pin.split("_")[1],
                    "PCLLOT": pin.split("_")[2],
                    "PROP_LOC": address,
                }
            }
            for pin, address in pins
        ]
    }


def test_build_artifact_keeps_one_house_together_and_enriches_roof_permit():
    detail_url = "https://www.sdlportal.com/example/permits/C-1"
    raw = {
        "collected_at": "2026-08-19T02:00:03.456Z",
        "source": {"name": "SDL Portal — Ramsey Borough"},
        "authorization_basis": "User represented authorization.",
        "records": [
            {
                "pams_pin": "0248_1_2",
                "collection_status": "collected",
                "source_page": {
                    "url": "https://www.sdlportal.com/example/properties/1/2"
                },
                "location": {"street": "12 Oak Street", "block": "1", "lot": "2"},
                "property_details": {"acreage": "0.25 acres²"},
                "geoareas": {"zoning": "R-1"},
                "assessed_valuation": {"total": "$500000"},
                "owner": {"address": "must not survive"},
                "property_data": {
                    "tax_maps": [],
                    "attachments": [],
                    "online_forms": [],
                },
                "construction": {
                    "permit_applications": [
                        {
                            "control_number": "C-1",
                            "permit_number": "20240001",
                            "work_description": "Roof replacement",
                            "agent": "must not survive",
                            "detail_url": detail_url,
                        }
                    ],
                    "inspections": [{"permit_number": "20240001", "result": "Pass"}],
                    "violations": [],
                },
            }
        ],
    }
    roof = {
        "properties": [
            {
                "pams_pin": "0248_1_2",
                "permits": [
                    {
                        "detail_url": detail_url,
                        "retrieval_batches": ["2024-H1"],
                        "keyword_relevance": {
                            "classification": "explicit_roof_work",
                            "evidence": "Roof replacement",
                        },
                        "detail_page": {
                            "permit_fee": "$100",
                            "agent": "must not survive",
                            "mailing_address": {"street": "must not survive"},
                            "issuing_officer": "Allowed Official",
                        },
                    }
                ],
            }
        ]
    }

    artifact = build_artifact(raw, _territory(("0248_1_2", "12 OAK ST")), roof)

    assert artifact["summary"] == {
        "properties_total": 1,
        "property_pages_collected": 1,
        "property_pages_not_collected": 0,
        "properties_with_permits": 1,
        "property_page_permit_applications_total": 1,
        "supplemental_roof_permits_total": 0,
        "permit_applications_total": 1,
        "inspections_total": 1,
        "violations_total": 0,
        "roof_keyword_details_attached": 1,
    }
    assert artifact["generated_at"] == raw["collected_at"]
    assert artifact == build_artifact(raw, _territory(("0248_1_2", "12 OAK ST")), roof)
    house = artifact["properties"][0]
    assert house["address"] == "12 OAK ST"
    assert house["location"]["street"] == "12 Oak Street"
    assert "owner" not in house
    permit = house["construction"]["permit_applications"][0]
    assert "agent" not in permit
    assert permit["roof_keyword_enrichment"]["detail_page"]["permit_fee"] == "$100"
    assert (
        permit["roof_keyword_enrichment"]["detail_page"]["issuing_officer"]
        == "Allowed Official"
    )
    assert _private_key_paths(artifact) == []


def test_unmatched_roof_record_is_retained_as_a_supplemental_permit():
    raw = {"records": []}
    roof = {
        "properties": [
            {
                "pams_pin": "0248_1_2",
                "permits": [
                    {
                        "control_number": "99",
                        "permit_number": "20000099",
                        "detail_url": "https://example.test/permits/99",
                        "work_description": "Re-roof",
                        "detail_page": {
                            "permit_fee": "$75",
                            "permit_agent": "must not survive",
                            "owner": {"name": "must not survive"},
                            "inspector": "Allowed Inspector",
                        },
                        "mailing": "must not survive",
                    }
                ],
            }
        ]
    }

    artifact = build_artifact(raw, _territory(("0248_1_2", "12 OAK ST")), roof)

    permit = artifact["properties"][0]["construction"]["permit_applications"][0]
    assert artifact["properties"][0]["collection_status"] == "not_collected"
    assert permit["source_scope"] == "roof_keyword_search_supplement"
    assert permit["control_number"] == "99"
    assert permit["roof_keyword_enrichment"]["detail_page"]["permit_fee"] == "$75"
    assert (
        permit["roof_keyword_enrichment"]["detail_page"]["inspector"]
        == "Allowed Inspector"
    )
    assert _private_key_paths(artifact) == []
    assert artifact["summary"]["supplemental_roof_permits_total"] == 1
    assert artifact["summary"]["roof_keyword_details_attached"] == 1


def test_missing_page_stays_in_output_with_collection_status():
    artifact = build_artifact(
        {"records": []},
        _territory(("0248_1_2", "12 OAK ST"), ("0248_1_3", "14 OAK ST")),
    )

    assert len(artifact["properties"]) == 2
    assert artifact["properties"][0]["collection_status"] == "not_collected"
    assert artifact["summary"]["property_pages_not_collected"] == 2


def test_duplicate_raw_property_records_are_rejected():
    raw = {
        "records": [
            {"pams_pin": "0248_1_2", "location": {"block": "1", "lot": "2"}},
            {"pams_pin": "0248_1_2", "location": {"block": "1", "lot": "2"}},
        ]
    }

    with pytest.raises(ValueError, match="duplicate SDL property record"):
        build_artifact(raw, _territory(("0248_1_2", "12 OAK ST")))


@pytest.mark.parametrize(
    ("territory", "message"),
    [
        (
            {"features": [{"properties": {"PAMS_PIN": ""}}]},
            "empty PAMS PIN",
        ),
        (
            _territory(
                ("0248_1_2", "12 OAK ST"),
                ("0248_1_2", "12 OAK ST"),
            ),
            "duplicate territory PAMS PIN",
        ),
    ],
)
def test_invalid_territory_pins_are_rejected(territory, message):
    with pytest.raises(ValueError, match=message):
        build_artifact({"records": []}, territory)


def test_raw_property_outside_territory_is_rejected():
    raw = {"records": [{"pams_pin": "0248_9_9"}]}

    with pytest.raises(ValueError, match="outside territory"):
        build_artifact(raw, _territory(("0248_1_2", "12 OAK ST")))


@pytest.mark.parametrize(
    "record",
    [
        {
            "pams_pin": "0248_1_2",
            "location": {"block": "1", "lot": "3"},
        },
        {
            "pams_pin": "0248_1_2",
            "location": {"block": "1", "lot": "2"},
            "source_page": {
                "url": "https://www.sdlportal.com/example/properties/1/3"
            },
        },
    ],
)
def test_mislabeled_raw_property_identity_is_rejected(record):
    with pytest.raises(ValueError, match="mismatch"):
        build_artifact(
            {"records": [record]},
            _territory(("0248_1_2", "12 OAK ST")),
        )


@pytest.mark.parametrize(
    "roof_property",
    [
        {"pams_pin": "0248_9_9", "permits": []},
        {"pams_pin": "0248_1_2", "block": "9", "lot": "2", "permits": []},
        {"pams_pin": "0248_1_2", "block": "1", "lot": "9", "permits": []},
    ],
)
def test_mislabeled_roof_property_identity_is_rejected(roof_property):
    with pytest.raises(ValueError, match="outside territory|mismatch"):
        build_artifact(
            {"records": []},
            _territory(("0248_1_2", "12 OAK ST")),
            {"properties": [roof_property]},
        )


def test_checked_in_property_history_is_a_reproducible_integrity_snapshot():
    raw = _read_json("data/sdl_property_pages_raw.json")
    territory = _read_json("data/territory.geojson")
    roof = _read_json("data/sdl_roof_permits_territory.json")
    checked_in = _read_json("data/sdl_property_history_territory.json")

    artifact = build_artifact(raw, territory, roof)
    assert artifact == build_artifact(raw, territory, roof)
    assert artifact == checked_in
    assert artifact["generated_at"] == raw["collected_at"]
    assert artifact["summary"] == {
        "properties_total": 540,
        "property_pages_collected": 532,
        "property_pages_not_collected": 8,
        "properties_with_permits": 525,
        "property_page_permit_applications_total": 3648,
        "supplemental_roof_permits_total": 14,
        "permit_applications_total": 3662,
        "inspections_total": 6168,
        "violations_total": 91,
        "roof_keyword_details_attached": 210,
    }

    expected_pins = [
        str(feature["properties"]["PAMS_PIN"])
        for feature in territory["features"]
    ]
    actual_pins = [prop["pams_pin"] for prop in artifact["properties"]]
    assert len(expected_pins) == len(set(expected_pins)) == 540
    assert actual_pins == expected_pins

    permits = [
        permit
        for prop in artifact["properties"]
        for permit in prop["construction"]["permit_applications"]
    ]
    assert sum("roof_keyword_enrichment" in permit for permit in permits) == 210
    assert (
        sum(
            permit.get("source_scope") == "roof_keyword_search_supplement"
            for permit in permits
        )
        == 14
    )
    for prop in artifact["properties"]:
        detail_urls = [
            str(permit.get("detail_url"))
            for permit in prop["construction"]["permit_applications"]
            if permit.get("detail_url")
        ]
        assert len(detail_urls) == len(set(detail_urls)), prop["pams_pin"]

    assert _private_key_paths(artifact) == []
