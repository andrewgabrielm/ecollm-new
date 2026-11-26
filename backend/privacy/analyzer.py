"""Injectable span detection and explicit offline-only Presidio/spaCy preload."""

import importlib
import importlib.util
import logging
import math
import re
from dataclasses import dataclass
from threading import Lock
from typing import Protocol, cast


class PrivacyError(RuntimeError):
    """Safe privacy failure; never attach source text or third-party diagnostics."""


class AnalyzerUnavailableError(PrivacyError):
    """Required local analyzer assets have not been explicitly installed/preloaded."""


class _BlockContentLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Presidio DEBUG context messages include nearby input tokens. Dependency
        # diagnostics must never bypass the application's content-free errors.
        return False


def _suppress_dependency_content_logs() -> None:
    for name in ("presidio-analyzer", "decision_process"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, _BlockContentLogs) for item in logger.filters):
            logger.addFilter(_BlockContentLogs())


# Every entity here is matched by a regex or checksum. Presidio's predefined set
# also carries spaCy NER categories (PERSON, LOCATION, ORGANIZATION, NRP,
# DATE_TIME, URL), and with en_core_web_sm those guess badly enough to break the
# product rather than protect it: LOCATION turned "capital of France" into
# "capital of [[PII_..._LOCATION_1]]" and the model refused, while PERSON tagged
# the leading verb in "Generate an essay on world war 2". A false positive is not
# merely noise — it hides words the model needs, and any placeholder at all arms
# the output inspector, which then withholds answers for naming public figures.
#
# The cost is real and deliberate: a name typed into a prompt is NOT masked, so
# it reaches an authorized cloud provider as written. Identifiers that carry
# actual account or identity risk are all deterministic, and those stay. Owners
# wanting NER coverage add it here with a larger spaCy model; this tuple is
# intentionally not reachable from the browser API.
DETECTED_ENTITIES: tuple[str, ...] = (
    "CREDIT_CARD",
    "CRYPTO",
    "EMAIL_ADDRESS",
    "IBAN_CODE",
    "IP_ADDRESS",
    "MEDICAL_LICENSE",
    "PHONE_NUMBER",
    "US_BANK_NUMBER",
    "US_DRIVER_LICENSE",
    "US_ITIN",
    "US_PASSPORT",
    "US_SSN",
    "SYNTHETIC_EMPLOYEE_ID",
    "SYNTHETIC_STUDENT_ID",
)


@dataclass(frozen=True, slots=True)
class DetectionSpan:
    entity_type: str
    start: int
    end: int
    score: float = 1.0

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[A-Z][A-Z_]{0,63}", self.entity_type)
            or isinstance(self.start, bool)
            or not isinstance(self.start, int)
            or isinstance(self.end, bool)
            or not isinstance(self.end, int)
            or self.start < 0
            or self.end <= self.start
            or isinstance(self.score, bool)
            or not isinstance(self.score, (float, int))
            or not math.isfinite(self.score)
            or not 0 <= self.score <= 1
        ):
            raise PrivacyError("Invalid analyzer span")


class Analyzer(Protocol):
    @property
    def version(self) -> str: ...

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]: ...


class _PresidioSpan(Protocol):
    entity_type: str
    start: int
    end: int
    score: float


class _PresidioEngine(Protocol):
    def analyze(
        self, *, text: str, language: str, entities: list[str], return_decision_process: bool
    ) -> list[_PresidioSpan]: ...


class PresidioAnalyzer:
    """Preload once during controlled startup, never from a request or auto-download.

    The supplied NLP engine already contains its local spaCy pipeline, so Presidio
    never calls its download-capable default ``SpacyNlpEngine.load`` path.
    """

    __slots__ = ("_engine", "_lock", "_version")

    def __init__(self, engine: _PresidioEngine, version: str) -> None:
        _suppress_dependency_content_logs()
        self._engine = engine
        self._version = version
        self._lock = Lock()

    @property
    def version(self) -> str:
        return self._version

    @classmethod
    def preload(cls, model_name: str = "en_core_web_sm") -> "PresidioAnalyzer":
        if model_name != "en_core_web_sm":
            raise AnalyzerUnavailableError("Unsupported local analyzer model")
        try:
            if importlib.util.find_spec(model_name) is None:
                raise AnalyzerUnavailableError(
                    "Local spaCy model is missing; explicit setup required"
                )
            spacy = importlib.import_module("spacy")
            _suppress_dependency_content_logs()
            presidio = importlib.import_module("presidio_analyzer")
            nlp_module = importlib.import_module("presidio_analyzer.nlp_engine")
            recognisers = importlib.import_module("backend.privacy.recognisers")
            pipeline = spacy.load(model_name)
            nlp_engine = nlp_module.SpacyNlpEngine(
                models=[{"lang_code": "en", "model_name": model_name}]
            )
            nlp_engine.nlp = {"en": pipeline}
            if not nlp_engine.is_loaded():
                raise AnalyzerUnavailableError("Local NLP preload failed")
            registry = presidio.RecognizerRegistry()
            registry.load_predefined_recognizers(nlp_engine=nlp_engine, languages=["en"])
            # The default EmailRecognizer uses tldextract.extract, which fetches
            # suffix data on first use. Replace it with an offline snapshot-only
            # validator before the engine can analyze a request.
            registry.remove_recognizer("EmailRecognizer", language="en")
            registry.add_recognizer(recognisers.offline_email_recognizer())
            for recognizer in recognisers.presidio_recognizers():
                registry.add_recognizer(recognizer)
            engine = presidio.AnalyzerEngine(
                registry=registry,
                nlp_engine=nlp_engine,
                supported_languages=["en"],
                log_decision_process=False,
            )
            from importlib.metadata import version

            version_id = (
                f"presidio-{version('presidio-analyzer')}/"
                f"spacy-{version('spacy')}/{model_name}-{version(model_name)}"
            )
            return cls(cast(_PresidioEngine, engine), version_id)
        except AnalyzerUnavailableError:
            raise
        except Exception:
            raise AnalyzerUnavailableError("Local analyzer preload failed") from None

    def analyze(self, text: str) -> tuple[DetectionSpan, ...]:
        try:
            with self._lock:
                results = self._engine.analyze(
                    text=text,
                    language="en",
                    entities=list(DETECTED_ENTITIES),
                    return_decision_process=False,
                )
            return tuple(
                DetectionSpan(item.entity_type, item.start, item.end, item.score)
                for item in results
            )
        except Exception:
            raise PrivacyError("Privacy analysis failed") from None


def resolve_overlaps(text: str, spans: tuple[DetectionSpan, ...]) -> tuple[DetectionSpan, ...]:
    """Mask each overlapping union fully; choose its label by stable confidence order.

    Dropping overlapping candidates can expose their unmatched tails. Merging the
    union preserves coverage, while ties use length, entity type and offsets.
    """
    if any(not isinstance(span, DetectionSpan) or span.end > len(text) for span in spans):
        raise PrivacyError("Analyzer returned out-of-bounds spans")
    ordered = sorted(spans, key=lambda span: (span.start, span.end, span.entity_type, -span.score))
    merged: list[DetectionSpan] = []
    group: list[DetectionSpan] = []
    end = -1

    def emit() -> None:
        winner = min(
            group,
            key=lambda item: (-item.score, -(item.end - item.start), item.entity_type, item.start),
        )
        merged.append(
            DetectionSpan(
                winner.entity_type, group[0].start, max(item.end for item in group), winner.score
            )
        )

    for span in ordered:
        if group and span.start >= end:
            emit()
            group = []
        group.append(span)
        end = max(end, span.end)
    if group:
        emit()
    return tuple(merged)
