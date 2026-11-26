"""Deterministic, enumerative generators for the Step 11 benchmark.

Every task is a Cartesian product of fixed template parts and fixed data rows.
No random state is used; regenerating the fixtures from the same source code
must yield byte-identical output. Families group paraphrases of the same
reference answer so family-aware splitting keeps paraphrases together.
"""

from collections.abc import Callable, Iterator

from backend.contracts import TaskCategory
from backend.learning.benchmarks.types import BenchmarkTask, make_task_id
from backend.versions import JSONValue

GENERATOR_VERSION = "benchmarks-generators-v1"

Generator = Callable[[], Iterator[BenchmarkTask]]


def _task(
    family_id: str,
    variant_index: int,
    category: TaskCategory,
    prompt: str,
    reference: dict[str, JSONValue],
) -> BenchmarkTask:
    return BenchmarkTask(
        task_id=make_task_id(family_id, variant_index),
        family_id=family_id,
        category=category,
        variant_index=variant_index,
        prompt=prompt.strip(),
        reference=reference,
    )


# ---------------------------------------------------------------------------
# qa — factoid lookups with multiple acceptable surface forms.
# ---------------------------------------------------------------------------

_QA_FACTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("capital-france", "capital of", "France", ("Paris",)),
    ("capital-japan", "capital of", "Japan", ("Tokyo",)),
    ("capital-brazil", "capital of", "Brazil", ("Brasilia", "Brasília")),
    ("capital-australia", "capital of", "Australia", ("Canberra",)),
    ("capital-canada", "capital of", "Canada", ("Ottawa",)),
    ("element-symbol-na", "chemical symbol of", "sodium", ("Na",)),
    ("element-symbol-fe", "chemical symbol of", "iron", ("Fe",)),
    ("element-symbol-au", "chemical symbol of", "gold", ("Au",)),
    ("planet-count", "number of planets in", "the Solar System", ("8", "eight")),
    ("water-freeze-c", "freezing point in Celsius of", "water at sea level", ("0", "zero")),
    ("speed-light-km-s", "approximate speed in km/s of", "light in a vacuum", ("299792",)),
    ("math-sqrt-144", "value of", "the square root of 144", ("12", "twelve")),
    ("math-pi-two-dp", "value of", "pi rounded to two decimal places", ("3.14",)),
    ("continent-count", "number of", "continents on Earth", ("7", "seven")),
    ("week-days", "number of", "days in a week", ("7", "seven")),
)

_QA_PARAPHRASES: tuple[str, ...] = (
    "Answer briefly: what is the {attribute} {subject}?",
    "In one word or number, give the {attribute} {subject}.",
    "Respond with only the {attribute} {subject}.",
)


def _qa_tasks() -> Iterator[BenchmarkTask]:
    for family_slug, attribute, subject, answers in _QA_FACTS:
        family_id = f"qa-{family_slug}"
        reference: dict[str, JSONValue] = {"answers": list(answers)}
        for variant, template in enumerate(_QA_PARAPHRASES):
            prompt = template.format(attribute=attribute, subject=subject)
            yield _task(family_id, variant, TaskCategory.QA, prompt, reference)


# ---------------------------------------------------------------------------
# reason — short chain-of-reasoning problems with a single scalar answer.
# ---------------------------------------------------------------------------

_REASON_FACTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "trains-meet",
        "Two trains leave stations 300 km apart at the same time and travel toward each other at 60 km/h and 90 km/h. How many hours until they meet?",
        ("2",),
    ),
    (
        "apples-sharing",
        "Alice has 12 apples and gives one third of them to Bob. How many apples does Bob receive?",
        ("4", "four"),
    ),
    (
        "age-ratio",
        "Carol is twice as old as Dan. In five years their combined age will be 40. How old is Carol now?",
        ("20",),
    ),
    (
        "rectangle-area",
        "A rectangle measures 7 by 5. What is its area?",
        ("35",),
    ),
    (
        "coin-flip",
        "What is the probability of getting exactly two heads in three fair coin flips, as a simplified fraction?",
        ("3/8",),
    ),
    (
        "modular-arithmetic",
        "What is 17 mod 5?",
        ("2", "two"),
    ),
    (
        "sequence-next",
        "What number comes next in the sequence 2, 4, 8, 16, ...?",
        ("32",),
    ),
    (
        "price-discount",
        "A shirt costs 40 dollars and is on sale for 25 percent off. What is the sale price in dollars?",
        ("30",),
    ),
    (
        "speed-average",
        "You drive 120 km in 2 hours then 100 km in 2 hours. What is your average speed in km/h?",
        ("55",),
    ),
    (
        "digits-sum",
        "What is the sum of the digits of 2026?",
        ("10", "ten"),
    ),
    (
        "primes-under-10",
        "How many prime numbers are strictly less than 10?",
        ("4", "four"),
    ),
    (
        "clock-angle",
        "At 3:00, what is the smaller angle between the hour and minute hands in degrees?",
        ("90",),
    ),
    (
        "boxes-weight",
        "Five boxes weigh 3 kg each and two boxes weigh 4 kg each. What is the total weight in kilograms?",
        ("23",),
    ),
    (
        "ratio-split",
        "Split 60 dollars between two people in a 2:3 ratio. How many dollars does the smaller share receive?",
        ("24",),
    ),
)

_REASON_PARAPHRASES: tuple[str, ...] = (
    "Reason step by step but answer with the final value only. {problem}",
    "Think through the problem silently and reply with just the final answer. {problem}",
    "Solve the following and respond with only the numeric answer. {problem}",
)


def _reason_tasks() -> Iterator[BenchmarkTask]:
    for slug, problem, answers in _REASON_FACTS:
        family_id = f"reason-{slug}"
        reference: dict[str, JSONValue] = {"answers": list(answers)}
        for variant, template in enumerate(_REASON_PARAPHRASES):
            prompt = template.format(problem=problem)
            yield _task(family_id, variant, TaskCategory.REASON, prompt, reference)


# ---------------------------------------------------------------------------
# summarise — short public-domain passages with a reference summary.
# ---------------------------------------------------------------------------

_SUMMARY_PASSAGES: tuple[tuple[str, str, str], ...] = (
    (
        "orbit",
        "A planet orbits a star because the star's gravity pulls on it. The planet's forward motion keeps it from falling straight in, and the balance between pull and motion traces an orbit.",
        "A planet's orbit is the balance between the star's gravity and the planet's forward motion.",
    ),
    (
        "photosynthesis",
        "Plants use sunlight to turn carbon dioxide and water into sugar and oxygen. The sugar feeds the plant and the oxygen is released into the air.",
        "Plants convert sunlight, carbon dioxide and water into sugar and oxygen.",
    ),
    (
        "rain",
        "Water evaporates from oceans and lakes under the sun. The vapour rises, cools, and condenses into droplets that eventually fall back as rain.",
        "Water evaporates, condenses in clouds and falls back as rain.",
    ),
    (
        "election",
        "In a general election voters choose representatives who form a legislature. The group with the most seats usually forms the government.",
        "Voters elect representatives, and the largest group forms the government.",
    ),
    (
        "immune",
        "The immune system recognises invading pathogens and produces antibodies that bind to them. Those antibodies help cells destroy the invaders.",
        "The immune system produces antibodies that help cells destroy invading pathogens.",
    ),
    (
        "bridge-arch",
        "An arch bridge transfers the load of the deck outward along the curve of the arch to supports at each end. The supports push back, keeping the arch in place.",
        "An arch bridge carries loads along a curve to supports that push back.",
    ),
    (
        "compiler",
        "A compiler reads source code written by a programmer, checks that it is well formed, and translates it into machine instructions a computer can run directly.",
        "A compiler translates checked source code into machine instructions.",
    ),
    (
        "vaccine",
        "A vaccine exposes the immune system to a harmless piece of a pathogen. The immune system learns to recognise it and responds faster if the real pathogen appears later.",
        "A vaccine trains the immune system with a harmless signal so it can respond quickly to the real pathogen.",
    ),
    (
        "tides",
        "The moon's gravity pulls water on the near side of the Earth toward it, and inertia creates a matching bulge on the far side. As the Earth rotates these bulges pass coastlines as tides.",
        "The moon's gravity and the Earth's rotation produce tidal bulges that reach coastlines as tides.",
    ),
    (
        "cache",
        "A cache keeps a small fast copy of data so repeat requests do not need to recompute or refetch. When the cache is missing the data, the slow path is used and the cache can be updated.",
        "A cache stores repeated data in fast memory to avoid recomputing or refetching.",
    ),
    (
        "lightning",
        "Static charges build up between clouds and the ground until the air briefly becomes a conductor. A sudden current flows, heating the air so fast it explodes outward as thunder.",
        "Lightning is a sudden current that equalises charge between clouds and the ground, heating the air into thunder.",
    ),
    (
        "sound",
        "Sound is a pressure wave that travels through air by pushing molecules into each other. The ear converts the pattern of pressure into nerve signals the brain interprets as sound.",
        "Sound is a pressure wave in air that the ear converts into signals for the brain.",
    ),
    (
        "sorting",
        "A sorting algorithm arranges items into a chosen order by comparing them. Different algorithms trade off how many comparisons they make and how much extra memory they need.",
        "Sorting algorithms order items by comparison with different time and memory trade-offs.",
    ),
    (
        "map-scale",
        "A map scale tells you how distances on the paper correspond to distances in the real world. A one-to-one hundred thousand scale means one centimetre on the map is one kilometre on the ground.",
        "A map scale states how paper distances map to real-world distances.",
    ),
)

_SUMMARY_PARAPHRASES: tuple[str, ...] = (
    "Summarise the passage below in one sentence:\n\n{passage}",
    "Produce a single-sentence summary of this passage:\n\n{passage}",
    "Read the passage and write one concise sentence that captures the main idea:\n\n{passage}",
)


def _summary_tasks() -> Iterator[BenchmarkTask]:
    for slug, passage, reference_summary in _SUMMARY_PASSAGES:
        family_id = f"summarise-{slug}"
        reference: dict[str, JSONValue] = {
            "reference": reference_summary,
            "source": passage,
        }
        for variant, template in enumerate(_SUMMARY_PARAPHRASES):
            yield _task(
                family_id,
                variant,
                TaskCategory.SUMMARISE,
                template.format(passage=passage),
                reference,
            )


# ---------------------------------------------------------------------------
# extract — structured field extraction with reference values.
# ---------------------------------------------------------------------------

_EXTRACT_SOURCES: tuple[tuple[str, str, dict[str, str]], ...] = (
    (
        "invoice-small",
        "Invoice 1042 for customer Acme Corp dated 2026-02-15 totals 240.00 USD.",
        {
            "invoice_number": "1042",
            "customer": "Acme Corp",
            "date": "2026-02-15",
            "total": "240.00",
            "currency": "USD",
        },
    ),
    (
        "invoice-mid",
        "Invoice 2087 for customer Globex dated 2026-05-01 totals 1290.50 EUR.",
        {
            "invoice_number": "2087",
            "customer": "Globex",
            "date": "2026-05-01",
            "total": "1290.50",
            "currency": "EUR",
        },
    ),
    (
        "invoice-large",
        "Invoice 3301 for customer Initech dated 2026-08-20 totals 87654.00 GBP.",
        {
            "invoice_number": "3301",
            "customer": "Initech",
            "date": "2026-08-20",
            "total": "87654.00",
            "currency": "GBP",
        },
    ),
    (
        "flight-one",
        "Flight AB123 departs London on 2026-03-10 at 08:45 and arrives in Paris at 11:05.",
        {
            "flight": "AB123",
            "origin": "London",
            "destination": "Paris",
            "date": "2026-03-10",
            "departure": "08:45",
            "arrival": "11:05",
        },
    ),
    (
        "flight-two",
        "Flight CD456 departs Tokyo on 2026-04-22 at 21:10 and arrives in Seoul at 23:40.",
        {
            "flight": "CD456",
            "origin": "Tokyo",
            "destination": "Seoul",
            "date": "2026-04-22",
            "departure": "21:10",
            "arrival": "23:40",
        },
    ),
    (
        "shipment-small",
        "Shipment SH-7781 holds 3 pallets weighing 450 kg routed from Rotterdam to Oslo.",
        {
            "shipment_id": "SH-7781",
            "pallets": "3",
            "weight_kg": "450",
            "origin": "Rotterdam",
            "destination": "Oslo",
        },
    ),
    (
        "shipment-heavy",
        "Shipment SH-9120 holds 12 pallets weighing 2100 kg routed from Hamburg to Stockholm.",
        {
            "shipment_id": "SH-9120",
            "pallets": "12",
            "weight_kg": "2100",
            "origin": "Hamburg",
            "destination": "Stockholm",
        },
    ),
    (
        "meeting-planner",
        "Meeting with Sam on 2026-06-11 at 14:30 in Room 402 about the ingest rewrite.",
        {
            "attendee": "Sam",
            "date": "2026-06-11",
            "time": "14:30",
            "location": "Room 402",
            "topic": "ingest rewrite",
        },
    ),
    (
        "meeting-review",
        "Meeting with Priya on 2026-07-02 at 09:00 in Room 118 about the release checklist.",
        {
            "attendee": "Priya",
            "date": "2026-07-02",
            "time": "09:00",
            "location": "Room 118",
            "topic": "release checklist",
        },
    ),
    (
        "receipt-coffee",
        "Receipt from Bean & Steam on 2026-11-02: 2 lattes and 1 croissant for 11.75 USD.",
        {
            "merchant": "Bean & Steam",
            "date": "2026-11-02",
            "items": "2 lattes and 1 croissant",
            "total": "11.75",
            "currency": "USD",
        },
    ),
    (
        "receipt-books",
        "Receipt from Northfield Books on 2026-10-14: 3 paperbacks for 42.30 USD.",
        {
            "merchant": "Northfield Books",
            "date": "2026-10-14",
            "items": "3 paperbacks",
            "total": "42.30",
            "currency": "USD",
        },
    ),
    (
        "order-hardware",
        "Order PO-5544 placed by Ingrid at ToolWorks on 2026-09-18 contains 4 drills priced 99 USD each.",
        {
            "order_id": "PO-5544",
            "buyer": "Ingrid",
            "vendor": "ToolWorks",
            "date": "2026-09-18",
            "quantity": "4",
            "unit_price": "99",
        },
    ),
    (
        "order-furniture",
        "Order PO-6677 placed by Marco at ChairCo on 2026-10-01 contains 8 chairs priced 65 USD each.",
        {
            "order_id": "PO-6677",
            "buyer": "Marco",
            "vendor": "ChairCo",
            "date": "2026-10-01",
            "quantity": "8",
            "unit_price": "65",
        },
    ),
    (
        "ticket-concert",
        "Ticket for Nina on 2026-12-05 at Hall B seat 42 priced 85 USD.",
        {
            "attendee": "Nina",
            "date": "2026-12-05",
            "venue": "Hall B",
            "seat": "42",
            "price": "85",
            "currency": "USD",
        },
    ),
    (
        "ticket-match",
        "Ticket for Omar on 2026-09-28 at Stadium 3 seat 118 priced 60 USD.",
        {
            "attendee": "Omar",
            "date": "2026-09-28",
            "venue": "Stadium 3",
            "seat": "118",
            "price": "60",
            "currency": "USD",
        },
    ),
)

_EXTRACT_PARAPHRASES: tuple[str, ...] = (
    'Extract the following fields as a JSON object with keys {fields}: "{text}"',
    'From the sentence below return a JSON object with keys {fields}. Sentence: "{text}"',
    'Read: "{text}". Reply with a JSON object containing the keys {fields}.',
)


def _extract_tasks() -> Iterator[BenchmarkTask]:
    for slug, text, fields in _EXTRACT_SOURCES:
        family_id = f"extract-{slug}"
        key_list = ", ".join(sorted(fields))
        reference: dict[str, JSONValue] = {"fields": dict(fields)}
        for variant, template in enumerate(_EXTRACT_PARAPHRASES):
            prompt = template.format(fields=key_list, text=text)
            yield _task(family_id, variant, TaskCategory.EXTRACT, prompt, reference)


# ---------------------------------------------------------------------------
# classify — sentiment / topic classification with a closed label set.
# ---------------------------------------------------------------------------

_CLASSIFY_FACTS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "sentiment-pos-1",
        ("positive", "negative", "neutral"),
        "The service was excellent and the staff very friendly.",
        "positive",
    ),
    (
        "sentiment-pos-2",
        ("positive", "negative", "neutral"),
        "Loved the ending and would recommend it.",
        "positive",
    ),
    (
        "sentiment-pos-3",
        ("positive", "negative", "neutral"),
        "Shipping was fast and the item is better than described.",
        "positive",
    ),
    (
        "sentiment-neg-1",
        ("positive", "negative", "neutral"),
        "The order arrived broken and support never replied.",
        "negative",
    ),
    (
        "sentiment-neg-2",
        ("positive", "negative", "neutral"),
        "Waited an hour for food that was cold.",
        "negative",
    ),
    (
        "sentiment-neg-3",
        ("positive", "negative", "neutral"),
        "The seat was uncomfortable and the movie was dull.",
        "negative",
    ),
    (
        "sentiment-neu-1",
        ("positive", "negative", "neutral"),
        "The package arrived on the stated day.",
        "neutral",
    ),
    (
        "sentiment-neu-2",
        ("positive", "negative", "neutral"),
        "The meeting lasted forty minutes.",
        "neutral",
    ),
    (
        "topic-sport-1",
        ("sport", "science", "business", "politics"),
        "The team scored three goals in the final minutes to secure the title.",
        "sport",
    ),
    (
        "topic-science-1",
        ("sport", "science", "business", "politics"),
        "Researchers reported a new compound that inhibits a bacterial enzyme in lab tests.",
        "science",
    ),
    (
        "topic-business-1",
        ("sport", "science", "business", "politics"),
        "Shareholders approved the merger during an annual general meeting.",
        "business",
    ),
    (
        "topic-politics-1",
        ("sport", "science", "business", "politics"),
        "The chamber voted to extend the parliamentary session by two weeks.",
        "politics",
    ),
    (
        "topic-sport-2",
        ("sport", "science", "business", "politics"),
        "A new stadium will host next season's championship matches.",
        "sport",
    ),
    (
        "topic-science-2",
        ("sport", "science", "business", "politics"),
        "Telescope observations suggest the dwarf galaxy is older than previously thought.",
        "science",
    ),
    (
        "topic-business-2",
        ("sport", "science", "business", "politics"),
        "The firm posted stronger quarterly earnings on export sales.",
        "business",
    ),
)

_CLASSIFY_PARAPHRASES: tuple[str, ...] = (
    "Classify the text into one of {labels}. Reply with only the label.\n\nText: {text}",
    "Pick the correct label from {labels} for the text and reply with only the label.\n\nText: {text}",
    "Which of {labels} best fits the following? Respond with the single label.\n\nText: {text}",
)


def _classify_tasks() -> Iterator[BenchmarkTask]:
    for slug, labels, text, answer in _CLASSIFY_FACTS:
        family_id = f"classify-{slug}"
        label_str = ", ".join(labels)
        reference: dict[str, JSONValue] = {"label": answer, "labels": list(labels)}
        for variant, template in enumerate(_CLASSIFY_PARAPHRASES):
            prompt = template.format(labels=label_str, text=text)
            yield _task(family_id, variant, TaskCategory.CLASSIFY, prompt, reference)


# ---------------------------------------------------------------------------
# format — strict JSON output under a given schema.
# ---------------------------------------------------------------------------

_FORMAT_FAMILIES: tuple[tuple[str, dict[str, str], str], ...] = (
    (
        "greeting",
        {"greeting": "string"},
        'the greeting "hello world"',
    ),
    (
        "point",
        {"x": "integer", "y": "integer"},
        "the point with x=2 and y=3",
    ),
    (
        "box",
        {"width": "integer", "height": "integer", "label": "string"},
        'a box of width 10, height 5, label="crate"',
    ),
    (
        "color",
        {"name": "string", "hex": "string"},
        'the color named "azure" with hex "#f0ffff"',
    ),
    (
        "range",
        {"start": "integer", "end": "integer"},
        "the inclusive range from 4 to 9",
    ),
    (
        "flag",
        {"name": "string", "enabled": "boolean"},
        'feature "beta_ui" enabled',
    ),
    (
        "user",
        {"id": "integer", "name": "string"},
        'user id 7 named "Robin"',
    ),
    (
        "temperature",
        {"celsius": "integer", "status": "string"},
        'a temperature of 22 with status "ok"',
    ),
    (
        "coord",
        {"lat": "number", "lon": "number"},
        "coordinates 48.86 N, 2.35 E",
    ),
    (
        "counter",
        {"name": "string", "value": "integer"},
        'a counter named "requests" with value 42',
    ),
    (
        "flagged-user",
        {"id": "integer", "verified": "boolean"},
        "user id 101 verified true",
    ),
    (
        "timer",
        {"label": "string", "seconds": "integer"},
        'a timer labelled "brew" set to 180 seconds',
    ),
    (
        "price",
        {"amount": "number", "currency": "string"},
        'a price of 9.50 in "USD"',
    ),
    (
        "note",
        {"id": "integer", "text": "string"},
        'note id 3 with text "remember milk"',
    ),
)

_FORMAT_PARAPHRASES: tuple[str, ...] = (
    "Output a single JSON object describing {subject}. Keys and types: {schema}. Return JSON only.",
    "Return only a JSON object for {subject}. It must have exactly the keys and types: {schema}.",
    "Produce JSON (no prose) for {subject} with these keys and types: {schema}.",
)


def _format_tasks() -> Iterator[BenchmarkTask]:
    for slug, schema, subject in _FORMAT_FAMILIES:
        family_id = f"format-{slug}"
        schema_str = ", ".join(f"{k}: {v}" for k, v in sorted(schema.items()))
        schema_payload: dict[str, JSONValue] = dict(schema)
        reference: dict[str, JSONValue] = {"schema": schema_payload}
        for variant, template in enumerate(_FORMAT_PARAPHRASES):
            prompt = template.format(subject=subject, schema=schema_str)
            yield _task(family_id, variant, TaskCategory.FORMAT, prompt, reference)


# ---------------------------------------------------------------------------
# code — small deterministic function implementations with unit tests.
# ---------------------------------------------------------------------------

_CODE_FAMILIES: tuple[tuple[str, str, str, tuple[tuple[list[JSONValue], JSONValue], ...]], ...] = (
    (
        "add",
        "add",
        "def add(a: int, b: int) -> int:",
        (
            ([0, 0], 0),
            ([1, 2], 3),
            ([-3, 5], 2),
            ([10, 10], 20),
        ),
    ),
    (
        "multiply",
        "multiply",
        "def multiply(a: int, b: int) -> int:",
        (
            ([0, 5], 0),
            ([3, 4], 12),
            ([-2, 6], -12),
            ([7, 0], 0),
        ),
    ),
    (
        "factorial",
        "factorial",
        "def factorial(n: int) -> int:",
        (
            ([0], 1),
            ([1], 1),
            ([4], 24),
            ([6], 720),
        ),
    ),
    (
        "reverse-string",
        "reverse",
        "def reverse(text: str) -> str:",
        (
            ([""], ""),
            (["a"], "a"),
            (["abc"], "cba"),
            (["level"], "level"),
        ),
    ),
    (
        "sum-list",
        "sum_list",
        "def sum_list(values: list[int]) -> int:",
        (
            ([[]], 0),
            ([[1]], 1),
            ([[1, 2, 3]], 6),
            ([[-1, 1, -2, 2]], 0),
        ),
    ),
    (
        "max-of-list",
        "max_of",
        "def max_of(values: list[int]) -> int:",
        (
            ([[1]], 1),
            ([[1, 2, 3]], 3),
            ([[-5, -2, -9]], -2),
            ([[4, 4, 4]], 4),
        ),
    ),
    (
        "count-vowels",
        "count_vowels",
        "def count_vowels(text: str) -> int:",
        (
            ([""], 0),
            (["abc"], 1),
            (["Education"], 5),
            (["xyz"], 0),
        ),
    ),
    (
        "is-even",
        "is_even",
        "def is_even(n: int) -> bool:",
        (
            ([0], True),
            ([1], False),
            ([2], True),
            ([-3], False),
        ),
    ),
    (
        "clamp",
        "clamp",
        "def clamp(value: int, lo: int, hi: int) -> int:",
        (
            ([5, 0, 10], 5),
            ([-1, 0, 10], 0),
            ([11, 0, 10], 10),
            ([0, 0, 0], 0),
        ),
    ),
    (
        "gcd",
        "gcd",
        "def gcd(a: int, b: int) -> int:",
        (
            ([12, 8], 4),
            ([9, 3], 3),
            ([17, 5], 1),
            ([0, 7], 7),
        ),
    ),
    (
        "fib",
        "fib",
        "def fib(n: int) -> int:",
        (
            ([0], 0),
            ([1], 1),
            ([5], 5),
            ([10], 55),
        ),
    ),
    (
        "palindrome",
        "is_palindrome",
        "def is_palindrome(text: str) -> bool:",
        (
            ([""], True),
            (["a"], True),
            (["abba"], True),
            (["abc"], False),
        ),
    ),
    (
        "join-words",
        "join_words",
        "def join_words(words: list[str], sep: str) -> str:",
        (
            ([[], "-"], ""),
            ([["a"], "-"], "a"),
            ([["a", "b"], "-"], "a-b"),
            ([["x", "y", "z"], "+"], "x+y+z"),
        ),
    ),
)

_CODE_PARAPHRASES: tuple[str, ...] = (
    (
        "Write a Python function with the signature:\n\n{signature}\n    ...\n\n"
        "Return only the complete function definition. No prose, no imports unless necessary."
    ),
    (
        "Implement the Python function below exactly as named. Return only the function source.\n\n"
        "{signature}\n    ..."
    ),
    (
        "Produce a single Python function matching this signature. Reply with only the source.\n\n"
        "{signature}"
    ),
)


def _code_tasks() -> Iterator[BenchmarkTask]:
    for slug, function_name, signature, tests in _CODE_FAMILIES:
        family_id = f"code-{slug}"
        reference_tests: list[JSONValue] = [
            {"input": list(inputs), "expected": expected} for inputs, expected in tests
        ]
        reference: dict[str, JSONValue] = {
            "function": function_name,
            "signature": signature,
            "tests": reference_tests,
        }
        for variant, template in enumerate(_CODE_PARAPHRASES):
            prompt = template.format(signature=signature)
            yield _task(family_id, variant, TaskCategory.CODE, prompt, reference)


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

_GENERATORS: tuple[Generator, ...] = (
    _qa_tasks,
    _reason_tasks,
    _summary_tasks,
    _extract_tasks,
    _classify_tasks,
    _format_tasks,
    _code_tasks,
)


def generate_all_tasks() -> tuple[BenchmarkTask, ...]:
    """Produce the frozen benchmark corpus in generator order."""
    tasks: list[BenchmarkTask] = []
    seen_task_ids: set[str] = set()
    for generator in _GENERATORS:
        for task in generator():
            if task.task_id in seen_task_ids:
                raise ValueError(f"duplicate task id: {task.task_id}")
            seen_task_ids.add(task.task_id)
            tasks.append(task)
    return tuple(tasks)
