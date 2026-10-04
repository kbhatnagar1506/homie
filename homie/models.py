"""Messages the Landed agents send each other."""

from uagents import Model


class CallRequest(Model):
    request_id: str = ""
    building_id: str
    purpose: str  # "quote" | "negotiate" | "repair"
    context: dict = {}


class CallResult(Model):
    request_id: str = ""
    building_id: str
    purpose: str
    answered: bool
    price: int | None = None
    discount: str | None = None
    discount_day: int | None = None
    ssn_alternative: str | None = None
    fees: int | None = None
    matched: bool | None = None
    payment: str | None = None
    repair_slot: str | None = None
    summary: str = ""


class NegotiateRequest(Model):
    request_id: str = ""
    offers: list[dict]
    want_free_month: bool = True


class NegotiateResult(Model):
    request_id: str = ""
    offers: list[dict]
    best_building_id: str | None = None
    note: str = ""


class PaperworkRequest(Model):
    request_id: str = ""
    building_id: str
    ssn_alternative: str
    payment: str | None = None
    move_in: str | None = None


class PaperworkResult(Model):
    request_id: str = ""
    documents: list[str]
    payment_plan: str
    application_status: str


class RepairRequest(Model):
    request_id: str = ""
    building_id: str
    issue: str
    photo_url: str | None = None


class RepairResult(Model):
    request_id: str = ""
    ticket_id: str
    slot: str | None
    channel: str  # "call" | "email"
    note: str


class PolicyRequest(Model):
    request_id: str = ""
    question: str
    building_id: str | None = None


class PolicyResult(Model):
    request_id: str = ""
    answer: str


class PicturesRequest(Model):
    request_id: str = ""
    building_ids: list[str] = []
    url: str | None = None


class PicturesResult(Model):
    request_id: str = ""
    images: list[str] = []


class MemoryRequest(Model):
    request_id: str = ""
    question: str = ""
    remember: str = ""


class MemoryResult(Model):
    request_id: str = ""
    answer: str = ""
    facts: list[str] = []
