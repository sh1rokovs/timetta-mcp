"""Parse Timetta OData $metadata (EDMX XML) into compact entity/field info."""

from __future__ import annotations

import xml.etree.ElementTree as ET

# ElementTree's findall supports the "{*}Tag" namespace wildcard, matching the
# local tag name in any XML namespace, so we don't hard-code OData EDMX namespaces.


def parse_entities(metadata_xml: str) -> list[str]:
    """Return the names of all queryable EntitySets (e.g. 'Users')."""
    root = ET.fromstring(metadata_xml)
    return [name for es in root.findall(".//{*}EntitySet") if (name := es.get("Name"))]


def parse_entity_schema(metadata_xml: str, entity: str) -> dict:
    """Return properties and navigation properties for one EntitySet.

    Raises ValueError if the entity set is not found.
    """
    root = ET.fromstring(metadata_xml)

    type_ref = None
    for es in root.findall(".//{*}EntitySet"):
        if es.get("Name") == entity:
            type_ref = es.get("EntityType")
            break
    if type_ref is None:
        raise ValueError(f"Unknown entity: {entity}")

    type_name = type_ref.split(".")[-1]
    for et in root.findall(".//{*}EntityType"):
        if et.get("Name") == type_name:
            properties = [
                {
                    "name": p.get("Name"),
                    "type": p.get("Type"),
                    "nullable": p.get("Nullable", "true") != "false",
                }
                for p in et.findall("{*}Property")
            ]
            navigation = [
                {"name": n.get("Name"), "type": n.get("Type")}
                for n in et.findall("{*}NavigationProperty")
            ]
            return {
                "entity": entity,
                "type": type_name,
                "properties": properties,
                "navigationProperties": navigation,
            }

    raise ValueError(f"Unknown entity type for: {entity}")
