"""The buildings Homie is working on right now: demo buildings by default, real ones after a search."""

from homie.config import PUBLIC_URL, env, load_buildings

BUILDINGS: dict[str, dict] = {b["id"]: b for b in load_buildings()} if env("DEMO_BUILDINGS", "0") == "1" else {}


def use(buildings: list[dict]) -> None:
    BUILDINGS.clear()
    BUILDINGS.update({b["id"]: b for b in buildings})


def name(building_id: str) -> str:
    return BUILDINGS.get(building_id, {}).get("name", building_id)


def listing_url(building: dict) -> str:
    return building.get("website") or f"{PUBLIC_URL}/site/listing/{building['id']}"


def can_call_now(building: dict) -> tuple[bool, str]:
    """Only phone a real office when it's open (or a 24/7 toll-free line). CALL_ANYTIME=1 overrides for testing."""
    if not building.get("real") or env("CALL_ANYTIME", "0") == "1":
        return True, ""
    if building.get("open_now"):
        return True, ""
    if (building.get("phone") or "").startswith(("+1800", "+1833", "+1844", "+1855", "+1866", "+1877", "+1888")):
        return True, ""
    return False, "closed right now"
