#!/usr/bin/env python3
"""Match manually collected SDL permit results to the Ramsey territory.

The input is the browser-saved JSON produced from SDL's public result tables.
This script performs no network access. It preserves every source field and URL,
then adds only an exact, normalized situs-address match to the territory parcels.
Ambiguous addresses are never guessed.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from houseaccount.normalize import normalize_address


_DIRECTION_WORDS = {
    "NORTH": "N",
    "SOUTH": "S",
    "EAST": "E",
    "WEST": "W",
}

_EXTRA_STREET_TYPES = {
    "TURNPIKE": "TPK",
    "TPKE": "TPK",
    "TPIKE": "TPK",
    "TERRACE": "TER",
}

_EXPLICIT_ROOF_WORK = re.compile(
    r"(?:^|[^A-Z])(?:RE[- ]?ROOF|ROOF(?:ING|TOP)?|SHINGL(?:E|ES|ING)|ICE SHIELD)(?:[^A-Z]|$)",
    re.IGNORECASE,
)


def canonical_sdl_address(raw: str | None) -> str:
    """Return a conservative address key for one SDL location.

    SDL sometimes appends a landmark description after ``" - "``. The prefix
    is accepted only when it starts with a house number; address ranges remain
    ranges and therefore cannot be silently assigned to one house.
    """

    text = str(raw or "").strip().upper()
    if " - " in text:
        prefix = text.split(" - ", 1)[0].strip()
        if re.match(r"^\d", prefix):
            text = prefix

    # Municipality text is presentation, not part of the situs join key.
    text = re.sub(r",?\s+RAMSEY(?:\s*,?\s*NJ(?:\s+07446)?)?$", "", text)
    key = normalize_address(text)
    tokens = key.split()
    if not tokens:
        return ""

    tokens = [_DIRECTION_WORDS.get(token, token) for token in tokens]
    tokens[-1] = _EXTRA_STREET_TYPES.get(tokens[-1], tokens[-1])
    return " ".join(tokens)


def build_artifact(
    raw: Mapping[str, Any],
    territory: Mapping[str, Any],
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    features = list(territory.get("features") or [])
    records = [row for row in raw.get("records") or [] if isinstance(row, Mapping)]
    detail_records = dict((details or {}).get("records") or {})

    parcels_by_address: dict[str, list[dict[str, Any]]] = defaultdict(list)
    properties: list[dict[str, Any]] = []
    for feature in features:
        props = dict(feature.get("properties") or {})
        address = str(props.get("PROP_LOC") or "").strip()
        canonical = canonical_sdl_address(address)
        item = {
            "pams_pin": str(props.get("PAMS_PIN") or ""),
            "block": str(props.get("PCLBLOCK") or ""),
            "lot": str(props.get("PCLLOT") or ""),
            "address": address,
            "normalized_address": canonical,
            "permits": [],
        }
        properties.append(item)
        if canonical:
            parcels_by_address[canonical].append(item)

    unmatched: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    matched_records = 0
    for record in records:
        source_location = str(record.get("location") or "")
        canonical = canonical_sdl_address(source_location)
        candidates = parcels_by_address.get(canonical, []) if canonical else []
        permit = dict(record)
        permit["normalized_location"] = canonical
        detail_url = str(permit.get("detail_url") or "")
        if detail_url in detail_records:
            permit["detail_page"] = detail_records[detail_url]
            permit["keyword_relevance"] = _keyword_relevance(permit)

        if len(candidates) == 1:
            permit["match_method"] = "exact_normalized_situs_address"
            candidates[0]["permits"].append(permit)
            matched_records += 1
        elif len(candidates) > 1:
            ambiguous.append(
                {
                    **permit,
                    "match_reason": "normalized address identifies multiple territory parcels",
                    "candidate_pams_pins": [item["pams_pin"] for item in candidates],
                }
            )
        else:
            unmatched.append(
                {
                    **permit,
                    "match_reason": "normalized address is not a territory situs address",
                }
            )

    for item in properties:
        item["permits"].sort(key=_date_sort_key, reverse=True)

    properties_with_matches = sum(bool(item["permits"]) for item in properties)
    explicit_roof_records = sum(
        permit.get("keyword_relevance", {}).get("classification")
        == "explicit_roof_work"
        for item in properties
        for permit in item["permits"]
    )
    properties_with_explicit_roof_work = sum(
        any(
            permit.get("keyword_relevance", {}).get("classification")
            == "explicit_roof_work"
            for permit in item["permits"]
        )
        for item in properties
    )
    duplicate_territory_addresses = {
        address: [item["pams_pin"] for item in items]
        for address, items in parcels_by_address.items()
        if len(items) > 1
    }

    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": raw.get("source") or {},
        "authorization_basis": raw.get("authorization_basis"),
        "coverage": raw.get("coverage") or {},
        "accepted_batches": raw.get("accepted_batches") or [],
        "match_policy": {
            "method": "exact normalized situs address",
            "ambiguous_addresses_are_matched": False,
            "address_ranges_are_expanded": False,
            "owner_or_occupant_data_used": False,
        },
        "summary": {
            "properties_total": len(properties),
            "properties_with_matches": properties_with_matches,
            "properties_without_matches": len(properties) - properties_with_matches,
            "portal_records_total": len(records),
            "portal_records_matched": matched_records,
            "portal_records_unmatched": len(unmatched),
            "portal_records_ambiguous": len(ambiguous),
            "matched_detail_pages_collected": sum(
                "detail_page" in permit
                for item in properties
                for permit in item["permits"]
            ),
            "explicit_roof_work_records": explicit_roof_records,
            "portal_keyword_only_records": matched_records - explicit_roof_records,
            "properties_with_explicit_roof_work": properties_with_explicit_roof_work,
            "duplicate_territory_address_keys": len(duplicate_territory_addresses),
        },
        "properties": properties,
        "ambiguous_records": ambiguous,
        "unmatched_records": unmatched,
        "duplicate_territory_addresses": duplicate_territory_addresses,
    }


def _date_sort_key(record: Mapping[str, Any]) -> tuple[int, str]:
    raw = str(record.get("issue_date") or "").strip()
    try:
        parsed = datetime.strptime(raw, "%m/%d/%Y")
        return (1, parsed.strftime("%Y-%m-%d"))
    except ValueError:
        return (0, raw)


def _keyword_relevance(permit: Mapping[str, Any]) -> dict[str, str]:
    detail = permit.get("detail_page") or {}
    fields = {
        "search_work_description": str(permit.get("work_description") or ""),
        "detail_description": str(detail.get("description") or ""),
        "detail_comments": str(detail.get("comments") or ""),
    }
    evidence = next((value for value in fields.values() if _EXPLICIT_ROOF_WORK.search(value)), "")
    return {
        "classification": "explicit_roof_work" if evidence else "portal_keyword_only",
        "evidence": evidence,
        "note": (
            "A whole-word roofing term appears in the displayed fields."
            if evidence
            else "Returned by SDL's 'roof' keyword search, but no whole-word roofing term appears in the displayed fields."
        ),
    }


def _read_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--territory", type=Path, required=True)
    parser.add_argument("--details", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    details = _read_json(args.details) if args.details else None
    artifact = build_artifact(
        _read_json(args.raw),
        _read_json(args.territory),
        details,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(artifact["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
