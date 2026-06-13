"""Parse Timetta OData $metadata (EDMX XML) into compact entity/field info."""

from __future__ import annotations

import xml.etree.ElementTree as ET

# OData namespace URIs used in EDMX documents.
_EDM = "http://docs.oasis-open.org/odata/ns/edm"
_EDMX = "http://docs.oasis-open.org/odata/ns/edmx"

_Q = {
    "EntitySet": f"{{{_EDM}}}EntitySet",
    "EntityType": f"{{{_EDM}}}EntityType",
    "Property": f"{{{_EDM}}}Property",
    "NavigationProperty": f"{{{_EDM}}}NavigationProperty",
}


def parse_entities(metadata_xml: str) -> list[str]:
    """Return the names of all queryable EntitySets (e.g. 'Users')."""
    root = ET.fromstring(metadata_xml)
    return [es.get("Name") for es in root.iter(_Q["EntitySet"]) if es.get("Name")]


def parse_entity_schema(metadata_xml: str, entity: str) -> dict:
    """Return properties and navigation properties for one EntitySet.

    Raises ValueError if the entity set is not found.
    """
    root = ET.fromstring(metadata_xml)

    type_ref = None
    for es in root.iter(_Q["EntitySet"]):
        if es.get("Name") == entity:
            type_ref = es.get("EntityType")
            break
    if type_ref is None:
        raise ValueError(f"Unknown entity: {entity}")

    type_name = type_ref.split(".")[-1]
    for et in root.iter(_Q["EntityType"]):
        if et.get("Name") == type_name:
            properties = [
                {
                    "name": p.get("Name"),
                    "type": p.get("Type"),
                    "nullable": p.get("Nullable", "true") != "false",
                }
                for p in et.iter(_Q["Property"])
            ]
            navigation = [
                {"name": n.get("Name"), "type": n.get("Type")}
                for n in et.iter(_Q["NavigationProperty"])
            ]
            return {
                "entity": entity,
                "type": type_name,
                "properties": properties,
                "navigationProperties": navigation,
            }

    raise ValueError(f"Unknown entity type for: {entity}")
