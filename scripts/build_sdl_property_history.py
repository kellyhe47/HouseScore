#!/usr/bin/env python3
"""Build one privacy-minimized SDL history record per Ramsey territory parcel.

The inputs are local JSON snapshots collected through SDL's public browser UI.
This script performs no network access. It deliberately excludes the SDL owner
section and permit-agent field, then attaches the already-collected roof permit
detail page when the property permit row has the same SDL detail URL.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlparse

from houseaccount.normalize import normalize_address


_PRIVATE_KEYS = {
    "owner",
    "mailing",
    "mailing_address",
    "agent",
    "permit_agent",
}


def build_artifact(
    raw: Mapping[str, Any],
    territory: Mapping[str, Any],
    roof: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a territory-complete, per-property SDL history artifact."""

    territory_by_pin = _build_territory_index(territory)
    raw_by_pin: dict[str, Mapping[str, Any]] = {}
    for record in raw.get("records") or []:
        if not isinstance(record, Mapping):
            raise ValueError("SDL property records must be JSON objects")
        pams_pin = str(record.get("pams_pin") or "")
        if not pams_pin:
            raise ValueError("SDL property record has an empty PAMS PIN")
        territory_props = territory_by_pin.get(pams_pin)
        if territory_props is None:
            raise ValueError(f"SDL property record is outside territory: {pams_pin}")
        if pams_pin in raw_by_pin:
            raise ValueError(f"duplicate SDL property record for {pams_pin}")
        _validate_raw_property_identity(record, territory_props)
        raw_by_pin[pams_pin] = record

    roof_by_pin_and_url, roof_by_pin = _build_roof_indexes(
        roof or {}, territory_by_pin
    )
    properties: list[dict[str, Any]] = []

    for feature in territory.get("features") or []:
        territory_props = dict(feature.get("properties") or {})
        pams_pin = str(territory_props.get("PAMS_PIN") or "")
        raw_record = raw_by_pin.get(pams_pin)
        item = _empty_property(territory_props)
        if raw_record is not None:
            item["collection_status"] = str(
                raw_record.get("collection_status") or "collected"
            )
            for key in (
                "source_page",
                "location",
                "property_details",
                "geoareas",
                "assessed_valuation",
                "map",
                "property_data",
            ):
                if key in raw_record:
                    item[key] = _privacy_copy(raw_record[key])

        construction = (raw_record or {}).get("construction") or {}
        permits = []
        matched_roof_urls: set[str] = set()
        for source_permit in construction.get("permit_applications") or []:
            permit = _privacy_copy(source_permit)
            detail_url = str(permit.get("detail_url") or "")
            enrichment = roof_by_pin_and_url.get((pams_pin, detail_url))
            if enrichment is not None:
                permit["roof_keyword_enrichment"] = _privacy_copy(enrichment)
                matched_roof_urls.add(detail_url)
            permits.append(permit)

        for roof_permit in roof_by_pin.get(pams_pin, []):
            detail_url = str(roof_permit.get("detail_url") or "")
            if detail_url in matched_roof_urls:
                continue
            supplement = {
                key: _privacy_copy(value)
                for key, value in roof_permit.items()
                if str(key).casefold() not in _PRIVATE_KEYS
                and key
                not in {
                    "detail_page",
                    "keyword_relevance",
                    "match_method",
                    "normalized_location",
                    "retrieval_batches",
                }
            }
            supplement["source_scope"] = "roof_keyword_search_supplement"
            supplement["roof_keyword_enrichment"] = _privacy_copy(
                roof_by_pin_and_url[(pams_pin, detail_url)]
            )
            permits.append(supplement)

        item["construction"] = {
            "permit_applications": permits,
            "inspections": _privacy_copy(construction.get("inspections") or []),
            "violations": _privacy_copy(construction.get("violations") or []),
        }
        properties.append(item)

    collected = sum(
        item["collection_status"] == "collected" for item in properties
    )
    permit_count = sum(
        len(item["construction"]["permit_applications"]) for item in properties
    )
    supplemental_roof_permit_count = sum(
        permit.get("source_scope") == "roof_keyword_search_supplement"
        for item in properties
        for permit in item["construction"]["permit_applications"]
    )
    property_page_permit_count = permit_count - supplemental_roof_permit_count
    roof_details_attached = sum(
        "roof_keyword_enrichment" in permit
        for item in properties
        for permit in item["construction"]["permit_applications"]
    )
    inspection_count = sum(
        len(item["construction"]["inspections"]) for item in properties
    )
    violation_count = sum(
        len(item["construction"]["violations"]) for item in properties
    )

    return {
        "schema_version": 1,
        "generated_at": raw.get("collected_at"),
        "source": _privacy_copy(raw.get("source") or {}),
        "authorization_basis": raw.get("authorization_basis"),
        "collection": _privacy_copy(raw.get("collection") or {}),
        "privacy": {
            "owner_section_collected": False,
            "owner_or_occupant_data_used": False,
            "permit_agent_collected": False,
            "supporting_documents_downloaded": False,
            "note": (
                "Owner and mailing fields and the permit-agent field are excluded. "
                "Only public property, construction, inspection, violation, and "
                "supporting-record metadata displayed on SDL property pages is retained."
            ),
        },
        "summary": {
            "properties_total": len(properties),
            "property_pages_collected": collected,
            "property_pages_not_collected": len(properties) - collected,
            "properties_with_permits": sum(
                bool(item["construction"]["permit_applications"])
                for item in properties
            ),
            "property_page_permit_applications_total": property_page_permit_count,
            "supplemental_roof_permits_total": supplemental_roof_permit_count,
            "permit_applications_total": permit_count,
            "inspections_total": inspection_count,
            "violations_total": violation_count,
            "roof_keyword_details_attached": roof_details_attached,
        },
        "properties": properties,
        "collection_errors": _privacy_copy(raw.get("errors") or []),
    }


def _build_territory_index(
    territory: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for feature in territory.get("features") or []:
        if not isinstance(feature, Mapping):
            raise ValueError("territory features must be JSON objects")
        props = feature.get("properties") or {}
        if not isinstance(props, Mapping):
            raise ValueError("territory feature properties must be a JSON object")
        pams_pin = str(props.get("PAMS_PIN") or "")
        if not pams_pin:
            raise ValueError("territory feature has an empty PAMS PIN")
        if pams_pin in index:
            raise ValueError(f"duplicate territory PAMS PIN: {pams_pin}")
        index[pams_pin] = props
    return index


def _validate_raw_property_identity(
    record: Mapping[str, Any], territory_props: Mapping[str, Any]
) -> None:
    pams_pin = str(record.get("pams_pin") or "")
    expected_block = str(territory_props.get("PCLBLOCK") or "")
    expected_lot = str(territory_props.get("PCLLOT") or "")
    location = record.get("location") or {}
    if not isinstance(location, Mapping):
        raise ValueError(f"SDL location must be an object for {pams_pin}")
    actual_block = str(location.get("block") or "")
    actual_lot = str(location.get("lot") or "")
    if (actual_block, actual_lot) != (expected_block, expected_lot):
        raise ValueError(
            f"SDL location mismatch for {pams_pin}: expected "
            f"{expected_block}/{expected_lot}, got {actual_block}/{actual_lot}"
        )

    source_page = record.get("source_page") or {}
    if not isinstance(source_page, Mapping):
        raise ValueError(f"SDL source_page must be an object for {pams_pin}")
    source_url = str(source_page.get("url") or "")
    if source_url:
        url_block, url_lot = _property_url_identity(source_url, pams_pin)
        if (url_block, url_lot) != (expected_block, expected_lot):
            raise ValueError(
                f"SDL property URL mismatch for {pams_pin}: expected "
                f"{expected_block}/{expected_lot}, got {url_block}/{url_lot}"
            )


def _property_url_identity(source_url: str, pams_pin: str) -> tuple[str, str]:
    parts = [unquote(part) for part in urlparse(source_url).path.split("/") if part]
    try:
        marker = len(parts) - 1 - parts[::-1].index("properties")
        block, lot = parts[marker + 1 : marker + 3]
    except (ValueError, IndexError):
        raise ValueError(f"invalid SDL property URL for {pams_pin}: {source_url}") from None
    if marker + 3 != len(parts):
        raise ValueError(f"invalid SDL property URL for {pams_pin}: {source_url}")
    return block, lot


def _privacy_copy(value: Any) -> Any:
    """Deep-copy source data while removing exact private-field names."""

    if isinstance(value, Mapping):
        return {
            key: _privacy_copy(child)
            for key, child in value.items()
            if not (isinstance(key, str) and key.casefold() in _PRIVATE_KEYS)
        }
    if isinstance(value, list):
        return [_privacy_copy(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_privacy_copy(child) for child in value)
    return copy.deepcopy(value)


def _empty_property(territory_props: Mapping[str, Any]) -> dict[str, Any]:
    address = str(territory_props.get("PROP_LOC") or "").strip()
    return {
        "pams_pin": str(territory_props.get("PAMS_PIN") or ""),
        "block": str(territory_props.get("PCLBLOCK") or ""),
        "lot": str(territory_props.get("PCLLOT") or ""),
        "address": address,
        "normalized_address": normalize_address(address),
        "collection_status": "not_collected",
        "source_page": {},
        "location": {},
        "property_details": {},
        "geoareas": {},
        "assessed_valuation": {},
        "map": {},
        "property_data": {
            "tax_maps": [],
            "attachments": [],
            "online_forms": [],
        },
    }


def _build_roof_indexes(
    roof: Mapping[str, Any],
    territory_by_pin: Mapping[str, Mapping[str, Any]],
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    by_pin: dict[str, list[dict[str, Any]]] = {}
    for prop in roof.get("properties") or []:
        if not isinstance(prop, Mapping):
            raise ValueError("roof property records must be JSON objects")
        pams_pin = str(prop.get("pams_pin") or "")
        territory_props = territory_by_pin.get(pams_pin)
        if territory_props is None:
            raise ValueError(f"roof property record is outside territory: {pams_pin}")
        expected_block = str(territory_props.get("PCLBLOCK") or "")
        expected_lot = str(territory_props.get("PCLLOT") or "")
        actual_block = str(prop.get("block") or "")
        actual_lot = str(prop.get("lot") or "")
        if actual_block and actual_block != expected_block:
            raise ValueError(
                f"roof block mismatch for {pams_pin}: expected "
                f"{expected_block}, got {actual_block}"
            )
        if actual_lot and actual_lot != expected_lot:
            raise ValueError(
                f"roof lot mismatch for {pams_pin}: expected {expected_lot}, got {actual_lot}"
            )
        for permit in prop.get("permits") or []:
            if not isinstance(permit, Mapping):
                continue
            detail_url = str(permit.get("detail_url") or "")
            if not pams_pin or not detail_url:
                continue
            by_pin.setdefault(pams_pin, []).append(_privacy_copy(permit))
            index[(pams_pin, detail_url)] = {
                "keyword": "roof",
                "retrieval_batches": _privacy_copy(
                    permit.get("retrieval_batches") or []
                ),
                "keyword_relevance": _privacy_copy(
                    permit.get("keyword_relevance") or {}
                ),
                "detail_page": _privacy_copy(permit.get("detail_page") or {}),
            }
    return index, by_pin


def _read_json(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--territory", type=Path, required=True)
    parser.add_argument("--roof-territory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    artifact = build_artifact(
        _read_json(args.raw),
        _read_json(args.territory),
        _read_json(args.roof_territory) if args.roof_territory else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(artifact["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
