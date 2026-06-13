import pytest

from timetta_mcp import metadata

SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Default">
      <EntityType Name="User">
        <Key><PropertyRef Name="id"/></Key>
        <Property Name="id" Type="Edm.Guid" Nullable="false"/>
        <Property Name="name" Type="Edm.String"/>
        <NavigationProperty Name="TimeEntries" Type="Collection(Default.TimeEntry)"/>
      </EntityType>
      <EntityType Name="TimeEntry">
        <Property Name="id" Type="Edm.Guid" Nullable="false"/>
        <Property Name="hours" Type="Edm.Double"/>
      </EntityType>
      <EntityContainer Name="Container">
        <EntitySet Name="Users" EntityType="Default.User"/>
        <EntitySet Name="TimeEntries" EntityType="Default.TimeEntry"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


def test_parse_entities_lists_entity_sets():
    assert metadata.parse_entities(SAMPLE_XML) == ["Users", "TimeEntries"]


def test_parse_entity_schema_returns_properties():
    schema = metadata.parse_entity_schema(SAMPLE_XML, "Users")
    assert schema["entity"] == "Users"
    assert schema["type"] == "User"
    assert {"name": "id", "type": "Edm.Guid", "nullable": False} in schema["properties"]
    assert {"name": "name", "type": "Edm.String", "nullable": True} in schema["properties"]
    assert {"name": "TimeEntries", "type": "Collection(Default.TimeEntry)"} in schema["navigationProperties"]


def test_parse_entity_schema_unknown_entity_raises():
    with pytest.raises(ValueError, match="Nope"):
        metadata.parse_entity_schema(SAMPLE_XML, "Nope")
