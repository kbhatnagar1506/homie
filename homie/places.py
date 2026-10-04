"""Find real apartment buildings with Google Places (New), authenticated with the GCP service account."""

import logging
import re

import httpx

log = logging.getLogger(__name__)
FIELDS = ("places.id,places.displayName,places.formattedAddress,places.internationalPhoneNumber,places.nationalPhoneNumber,"
          "places.websiteUri,places.rating,places.userRatingCount,places.currentOpeningHours.openNow,"
          "places.regularOpeningHours.weekdayDescriptions,places.location,places.googleMapsUri")


def _token() -> tuple[str, str]:
    import google.auth
    import google.auth.transport.requests

    creds, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token, project


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]


async def search_apartments(area: str, limit: int = 10) -> list[dict]:
    token, project = _token()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={"Authorization": f"Bearer {token}", "X-Goog-User-Project": project, "X-Goog-FieldMask": FIELDS},
            json={"textQuery": f"apartments {area}", "maxResultCount": min(limit, 20)},
        )
        r.raise_for_status()
    buildings = []
    for p in r.json().get("places", []):
        phone = re.sub(r"[^\d+]", "", p.get("internationalPhoneNumber") or "")
        buildings.append({
            "id": _slug(p["displayName"]["text"]),
            "name": p["displayName"]["text"],
            "address": p.get("formattedAddress", ""),
            "phone": phone,
            "phone_display": p.get("nationalPhoneNumber", ""),
            "website": p.get("websiteUri"),
            "maps": p.get("googleMapsUri"),
            "rating": p.get("rating"),
            "reviews": p.get("userRatingCount"),
            "open_now": (p.get("currentOpeningHours") or {}).get("openNow"),
            "hours": (p.get("regularOpeningHours") or {}).get("weekdayDescriptions") or [],
            "real": True,
        })
    return buildings
