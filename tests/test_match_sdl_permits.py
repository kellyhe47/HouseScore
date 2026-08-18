from scripts.match_sdl_permits import build_artifact, canonical_sdl_address


def test_canonical_sdl_address_handles_portal_spelling_and_landmark_suffix():
    assert canonical_sdl_address("173 East Main Street - Ramsey Post Office") == "173 E MAIN ST"
    assert canonical_sdl_address("503 N. Franklin Turnpike") == "503 N FRANKLIN TPK"


def test_build_artifact_matches_only_one_exact_normalized_situs_address():
    raw = {
        "records": [
            {"control_number": "C-1", "location": "12 Oak Street"},
            {"control_number": "C-2", "location": "99 Other Road"},
        ]
    }
    territory = {
        "features": [
            {
                "properties": {
                    "PAMS_PIN": "0248_1_1",
                    "PCLBLOCK": "1",
                    "PCLLOT": "1",
                    "PROP_LOC": "12 OAK ST",
                }
            }
        ]
    }

    artifact = build_artifact(raw, territory)

    assert artifact["summary"]["properties_total"] == 1
    assert artifact["summary"]["portal_records_matched"] == 1
    assert artifact["properties"][0]["permits"][0]["control_number"] == "C-1"
    assert artifact["unmatched_records"][0]["control_number"] == "C-2"


def test_address_range_is_not_expanded_to_an_individual_house():
    assert canonical_sdl_address("50-54 Peach Hill Court") == "50 54 PEACH HILL CT"
    assert canonical_sdl_address("50 Peach Hill Court") == "50 PEACH HILL CT"


def test_detail_page_data_is_attached_by_source_url():
    url = "https://example.test/permit/C-1"
    raw = {
        "records": [
            {"control_number": "C-1", "location": "12 Oak St", "detail_url": url}
        ]
    }
    territory = {
        "features": [
            {
                "properties": {
                    "PAMS_PIN": "0248_1_1",
                    "PCLBLOCK": "1",
                    "PCLLOT": "1",
                    "PROP_LOC": "12 OAK STREET",
                }
            }
        ]
    }
    details = {"records": {url: {"comments": "RE-ROOF", "permit_fee": "$100"}}}

    artifact = build_artifact(raw, territory, details)

    permit = artifact["properties"][0]["permits"][0]
    assert permit["detail_page"]["comments"] == "RE-ROOF"
    assert permit["keyword_relevance"]["classification"] == "explicit_roof_work"
    assert artifact["summary"]["matched_detail_pages_collected"] == 1


def test_child_proof_is_flagged_as_a_portal_keyword_false_positive():
    url = "https://example.test/permit/C-1"
    raw = {
        "records": [
            {"control_number": "C-1", "location": "12 Oak St", "detail_url": url}
        ]
    }
    territory = {
        "features": [
            {
                "properties": {
                    "PAMS_PIN": "0248_1_1",
                    "PCLBLOCK": "1",
                    "PCLLOT": "1",
                    "PROP_LOC": "12 OAK STREET",
                }
            }
        ]
    }
    details = {"records": {url: {"comments": "POOL WITH CHILD PROOF GATE"}}}

    artifact = build_artifact(raw, territory, details)

    permit = artifact["properties"][0]["permits"][0]
    assert permit["keyword_relevance"]["classification"] == "portal_keyword_only"
    assert artifact["summary"]["portal_keyword_only_records"] == 1
