"""A model running on this machine, treated as one more provider.

The router never cared where an endpoint lives. It sends messages to a URL and
reads text back, so Ollama on `localhost:11434` and Groq in a datacentre are
the same shape of thing — the only difference is the base URL and who pays for
the tokens. That is why this is ~150 lines and not a subsystem: everything
downstream (`selector`, `client`, cooldowns, model health) works unchanged as
soon as the entry appears in the registry.

**OpenAI-compatible only.** Ollama, LM Studio, llama.cpp's server, vLLM and
text-generation-webui all expose `/v1/chat/completions`, so one shape covers
every realistic way to run a model locally. A raw PyTorch `model.forward()` is
*not* something this can call — wrap it in a small FastAPI app that speaks the
same route and it becomes callable, which is the honest boundary.

**Local mode only.** These routes make the backend fetch a URL the user typed.
On a laptop that is the entire point. On a public server it is a request
forgery primitive pointed at the hosting provider's internal network, so
`entry()` returns nothing and the API refuses in `public_mode`. The feature is
a desktop feature and says so.

**Why the hardware warning is not advice but a number.** A 7B model in fp16
needs ~14 GB for weights alone before the KV cache; quantized to 4 bits it
needs ~4 GB and runs at a few tokens a second on a CPU. Telling someone "this
might be slow" teaches them nothing. Telling them "you have 15.7 GB and no
dedicated GPU, so expect ~3B–8B at Q4 and a handful of tokens per second" lets
them decide.
"""

import ctypes
import os
import sys
from dataclasses import dataclass

import httpx

from app import settings_store
from app.config import settings
from app.providers.registry import ProviderModel

LOCAL_ID = "local"

BASE_URL = "local.base_url"
MODEL = "local.model"
LABEL = "local.label"

# Long, because the first request to a cold local model includes loading
# several gigabytes off disk into RAM. A cloud timeout would be a bug here.
PROBE_TIMEOUT = 8.0

_GIB = 1024**3


class LocalError(RuntimeError):
    """Something the user can fix, phrased as what to do about it."""



@dataclass(frozen=True)
class LocalConfig:
    base_url: str
    model: str
    label: str

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.model)


def _normalize(base_url: str) -> str:
    """Trims to the OpenAI-compatible root, so either spelling of the URL works.

    People paste `http://localhost:11434`, `.../v1` and `.../v1/chat/completions`
    interchangeably, because all three appear in the same tools' documentation.
    litellm wants the `/v1` root, and guessing wrong produces a 404 that reads
    like the model is missing rather than like the URL is.
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return ""
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def config() -> LocalConfig:
    return LocalConfig(
        base_url=_normalize(settings_store.get_text(BASE_URL)),
        model=settings_store.get_text(MODEL).strip(),
        label=settings_store.get_text(LABEL).strip(),
    )


def save(base_url: str, model: str, label: str = "") -> LocalConfig:
    settings_store.set_text(BASE_URL, _normalize(base_url), limit=300)
    settings_store.set_text(MODEL, (model or "").strip(), limit=200)
    settings_store.set_text(LABEL, (label or "").strip(), limit=80)
    return config()


def clear() -> None:
    for key in (BASE_URL, MODEL, LABEL):
        settings_store.set_text(key, "")


def available() -> bool:
    """Whether the feature may be used at all. False on a public server."""
    return not settings.public_mode


def entry() -> ProviderModel | None:
    """The registry entry, or None when nothing is configured.

    `strength=1` and a late `planner_rank` are deliberate: a model small enough
    to run here is not the one that should be picked for a hard question or be
    asked to plan, and the selector reads exactly those two numbers to decide.
    It stays fully selectable, so pinning it by hand always works — which is
    the case someone who set this up actually wants.
    """
    if not available():
        return None
    current = config()
    if not current.ready:
        return None

    return ProviderModel(
        id=LOCAL_ID,
        display_name=current.label or f"Lokálně: {current.model}",
        # The `openai/` prefix is what tells litellm "talk the OpenAI protocol
        # to `api_base`" rather than trying to recognise the slug as a hosted
        # model — without it a name like `llama3.2:3b` resolves to nothing.
        models=(f"openai/{current.model}",),
        capabilities=("general", "code", "creative"),
        signup_url="",
        notes="Běží na tomhle počítači. Nic neodchází ven a nic nestojí kredity.",
        strength=1,
        planner_rank=95,
        keyless=True,
        api_base=current.base_url,
        local=True,
    )


# ------------------------------------------------------------------ hardware


def total_ram_bytes() -> int:
    """Physical RAM, or 0 when it cannot be read.

    Windows has no `sysconf`, so this goes through `GlobalMemoryStatusEx`. The
    zero return is meaningful: the caller degrades to "unknown" rather than
    inventing a verdict from a number it does not have.
    """
    if sys.platform == "win32":
        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except (AttributeError, OSError):
            return 0
        return 0

    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return 0


def hardware() -> dict:
    """What this machine can realistically run, stated as sizes and a caveat.

    The verdict is deliberately conservative and deliberately about *quantized*
    models: nobody should be running fp16 weights on a laptop, and a
    recommendation that ignores quantization would be wrong by a factor of four.
    """
    ram = total_ram_bytes()
    gib = ram / _GIB if ram else 0.0
    cores = os.cpu_count() or 0

    if not ram:
        level = "unknown"
        verdict = "Velikost paměti se nepodařilo zjistit — postupuj podle toho, co ti nástroj sám hlásí."
        ceiling = ""
    elif gib < 8:
        level = "no"
        verdict = "Na lokální model je tu málo paměti. Zůstaň u cloudových providerů."
        ceiling = "do ~1B"
    elif gib < 16:
        level = "tight"
        verdict = (
            "Model se vejde, ale na procesoru poběží pomalu — počítej "
            "s jednotkami tokenů za sekundu, ne s rychlostí cloudu."
        )
        ceiling = "do ~7B v Q4"
    elif gib < 32:
        level = "ok"
        verdict = "Paměti je dost. Rychlost pořád závisí na tom, jestli běží model na procesoru, nebo na grafice."
        ceiling = "do ~13B v Q4"
    else:
        level = "good"
        verdict = "Na lokální model je tu paměti dost i na velké modely."
        ceiling = "30B i větší v Q4"

    return {
        "ram_gb": round(gib, 1) if ram else None,
        "cpu_cores": cores,
        "level": level,
        "verdict": verdict,
        "ceiling": ceiling,
        # Not detected, stated. Reading GPU VRAM portably means shelling out to
        # vendor tools, and a wrong "you have a GPU" is worse than a caveat.
        "gpu_note": (
            "Odhad počítá s během na procesoru. Pokud máš dedikovanou grafiku "
            "a nástroj ji používá, bude to výrazně rychlejší."
        ),
    }


# --------------------------------------------------------------------- probe


async def probe(base_url: str) -> list[str]:
    """Model ids the endpoint advertises at `GET /models`.

    Raises `LocalError` with something a person can act on. "Connection
    refused" means the tool is not running; a 404 means the URL is not the
    OpenAI-compatible root, and those two need different fixes.
    """
    url = _normalize(base_url)
    if not url:
        raise LocalError("Nejdřív zadej adresu endpointu.")
    if not url.startswith(("http://", "https://")):
        raise LocalError("Adresa musí začínat http:// nebo https://.")

    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as http:
            response = await http.get(f"{url}/models")
    except httpx.ConnectError as exc:
        raise LocalError(
            "Na té adrese nikdo neodpovídá. Běží Ollama / LM Studio / llama.cpp?"
        ) from exc
    except httpx.HTTPError as exc:
        raise LocalError(f"Spojení selhalo: {exc.__class__.__name__}") from exc

    if response.status_code == 404:
        raise LocalError(
            "Adresa odpovídá, ale nemá /models — zkus ji zakončit na /v1 "
            "(např. http://localhost:11434/v1)."
        )
    if response.status_code >= 400:
        raise LocalError(f"Endpoint vrátil {response.status_code}.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise LocalError("Endpoint nevrátil JSON — nevypadá jako OpenAI API.") from exc

    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise LocalError("Odpověď nemá pole `data` — nevypadá jako OpenAI API.")

    return [str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")]

