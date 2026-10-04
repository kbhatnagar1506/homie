"""Messages the Landed agents send each other."""

from uagents import Model


class CallRequest(Model):
    request_id: str = ""
    user: str = ""
    building_id: str
    purpose: str  # "quote" | "negotiate" | "repair"
    context: dict = {}


class CallResult(Model):
    request_id: str = ""
    user: str = ""
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
    user: str = ""
    offers: list[dict]
    want_free_month: bool = True


class NegotiateResult(Model):
    request_id: str = ""
    user: str = ""
    offers: list[dict]
    best_building_id: str | None = None
    note: str = ""


class PaperworkRequest(Model):
    request_id: str = ""
    user: str = ""
    building_id: str
    ssn_alternative: str
    payment: str | None = None
    move_in: str | None = None


class PaperworkResult(Model):
    request_id: str = ""
    user: str = ""
    documents: list[str]
    payment_plan: str
    application_status: str


class RepairRequest(Model):
    request_id: str = ""
    user: str = ""
    building_id: str
    issue: str
    photo_url: str | None = None


class RepairResult(Model):
    request_id: str = ""
    user: str = ""
    ticket_id: str
    slot: str | None
    channel: str  # "call" | "email"
    note: str


class PolicyRequest(Model):
    request_id: str = ""
    user: str = ""
    question: str
    building_id: str | None = None


class PolicyResult(Model):
    request_id: str = ""
    user: str = ""
    answer: str


class PicturesRequest(Model):
    request_id: str = ""
    user: str = ""
    building_ids: list[str] = []
    url: str | None = None
    scan_prices: bool = False
    beds: int | None = None


class PicturesResult(Model):
    request_id: str = ""
    user: str = ""
    images: list[str] = []
    prices: list[dict] = []  # [{building_id, price, special, beds}] read live from each building's website


class MemoryRequest(Model):
    request_id: str = ""
    user: str = ""
    question: str = ""
    remember: str = ""


class MemoryResult(Model):
    request_id: str = ""
    user: str = ""
    answer: str = ""
    facts: list[str] = []


class ScheduleRequest(Model):
    request_id: str = ""
    user: str = ""
    text: str = ""              # what they asked, in their words
    kind: str = ""              # get_offers | call_building | remind | follow_up_repair | check_prices (Jev decides if empty)
    when_iso: str = ""          # exact time if the caller already knows it
    payload: dict = {}          # what the task needs to run (sender, request, buildings, target)


class ScheduleResult(Model):
    request_id: str = ""
    user: str = ""
    task_id: str = ""
    kind: str = ""
    when_iso: str = ""
    when_human: str = ""
    note: str = ""


class TaskDue(Model):
    request_id: str = ""
    user: str = ""
    task_id: str = ""
    kind: str = ""
    text: str = ""
    payload: dict = {}


class ScoutRequest(Model):
    request_id: str = ""
    user: str = ""
    building_id: str = ""
    url: str = ""
    beds: int | None = None


class ScoutResult(Model):
    request_id: str = ""
    user: str = ""
    facts: dict = {}
    shots: list[str] = []
    pages: int = 0
