#!/usr/bin/env python3
"""Count processing-difficulty features in manuscript blocks to nominate passages
for the editor's wording_check, and compare a proposed wording with its original.

Subcommands:
  nominate  measure every block of an index.json (from review_packets.py index) or a
            Markdown file, rank the blocks by a within-document z-score sum, and print
            the top N with the feature that drove each one.
  compare   count the same features in an original quote and a proposed wording,
            side by side, and flag any feature the proposed wording has more of.
            It also reports, without a parse, the share of the original's words the
            proposal replaces (edit ratio; above 0.5 the edit is "heavy"), the
            type-token ratio and the mean word length of both texts, with advisory
            flags when the proposal is less varied or uses longer words.
            --lexical-only computes just these and needs no spaCy.

nominate also counts, per block, the words from assets/excess-vocabulary.json
(Kobak et al. 2025, Science Advances: words in excess in 2024 biomedical abstracts).
That count is a separate column, not part of the ranking: the list is a lower
bound, it ages as authors avoid the words, and it cannot tell an author's style
from a model's. It nominates a passage for inspection; it never convicts one.

Both commands write JSON and print a short table. The JSON carries a fixed "scope"
note: these are candidate-nomination heuristics from model-side evidence, not
findings and not measurements of human reading difficulty.

Feature list
------------
The eight features follow Section 3.1 of Clark, Martin and Mahowald (2026), "Why is
AI-generated language so hard to understand?", arXiv:2610.29162, which measures
them from spaCy dependency parses and links them to human processing difficulty:

  1. object relative clauses with a relativizer ("the method that we used")
  2. object relative clauses without a relativizer ("the method we used")
  3. denominal verbs (a noun used as a verb: "to leverage", "to pipeline")
  4. inanimate subjects of animate-only verbs ("the model believes")
  5. dependency length, standardized by the sentence-length distribution of a
     reference text so that long sentences alone do not drive the measure
  6. stacked nouns (three or more nouns in one modifier chain)
  7. bigrams unattested in a reference corpus
  8. trigram surprisal (the paper uses n-gram perplexity against a reference corpus)

The operational definitions below are this script's approximations, not the paper's
code. Measures 1, 2, 5 and 6 are read from the parse; 3 and 4 use built-in word lists
and are marked "heuristic" in the output; 5, 7 and 8 need a reference text and are
null without one. Section 3.1 of the paper describes the measures; the numbers here
are comparable only within one run.

spaCy is optional and imported lazily. Without it the script exits with code 2 and
prints the install commands:

  uv venv tools/nlp-env
  uv pip install --python tools/nlp-env/bin/python spacy
  VIRTUAL_ENV="$PWD/tools/nlp-env" tools/nlp-env/bin/python -m spacy download en_core_web_sm

The model is chosen with --model: a model name, or "auto" (default) to use
en_core_web_trf when it is installed and en_core_web_sm otherwise.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_packets as rp  # noqa: E402

VERSION = "0.13.0"
PAPER = "Clark, Martin and Mahowald (2026), arXiv:2610.29162, Section 3.1"
EXCESS_VOCABULARY = Path(__file__).resolve().parent.parent / "assets" / "excess-vocabulary.json"
WORD_TOKEN = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
# Edit weight by the share of the original's words that the proposal replaces (Jourdan et al.'s
# light/medium/heavy classes); above HEAVY_EDIT_RATIO the repair needs a stated reason or a split.
HEAVY_EDIT_RATIO = 0.5
LIGHT_EDIT_RATIO = 0.2
SCOPE_NOTE = (
    "Candidate-nomination heuristics computed from a dependency parse (model-side "
    "evidence). Counts are not findings: they nominate passages for the editor's "
    "wording_check and do not measure human reading difficulty. The ranking is "
    "standardized within one document, so it cannot compare two versions or two "
    "manuscripts; compare raw counts and rates with their word counts instead, and "
    "do not treat a lower count as a better text."
)
INSTALL_HELP = """spaCy is not installed in this Python. It is an optional dependency; install it in a
separate environment under the working directory (not in the skill directory):

  uv venv tools/nlp-env
  uv pip install --python tools/nlp-env/bin/python spacy
  VIRTUAL_ENV="$PWD/tools/nlp-env" tools/nlp-env/bin/python -m spacy download en_core_web_sm

Then run this script with tools/nlp-env/bin/python. en_core_web_trf is used
automatically when installed (uv pip install --python tools/nlp-env/bin/python
'spacy[transformers]' and the same download command with en_core_web_trf)."""

COUNT_FEATURES = (
    "orc_with_relativizer",
    "orc_without_relativizer",
    "denominal_verbs",
    "inanimate_subject_animate_verb",
    "stacked_nouns",
    "unattested_bigrams",
)
CONTINUOUS_FEATURES = ("dependency_length", "trigram_surprisal_bits")
RANKED_FEATURES = COUNT_FEATURES + CONTINUOUS_FEATURES

FEATURE_NOTES: dict[str, dict[str, Any]] = {
    "orc_with_relativizer": {
        "status": "parse-based",
        "needs_reference": False,
        "definition": "A relcl verb whose nominal subject is not the relative word, with a relative word (that, which, who, whom, whose) attached to the clause.",
    },
    "orc_without_relativizer": {
        "status": "parse-based",
        "needs_reference": False,
        "definition": "A relcl verb whose nominal subject is not the relative word, with no relative word attached (a reduced object relative).",
    },
    "denominal_verbs": {
        "status": "heuristic",
        "needs_reference": False,
        "definition": "A verb whose lemma also occurs as a noun in the same text, or is on a short built-in list of nouns used as verbs; light verbs are excluded.",
    },
    "inanimate_subject_animate_verb": {
        "status": "heuristic",
        "needs_reference": False,
        "definition": "A nominal subject that is an inanimate pronoun or a common noun outside a built-in animate list, governing a verb on a built-in list of verbs that normally take a sentient subject.",
    },
    "dependency_length": {
        "status": "parse-based",
        "needs_reference": True,
        "definition": "Sum over non-punctuation tokens of the distance to the head, per sentence. With a reference text: z-score against reference sentences in the same length bin. Without: raw mean per sentence, reported with mean sentence length.",
    },
    "stacked_nouns": {
        "status": "parse-based",
        "needs_reference": False,
        "definition": "A run of three or more adjacent NOUN or PROPN tokens inside one sentence; each maximal run counts once.",
    },
    "unattested_bigrams": {
        "status": "parse-based",
        "needs_reference": True,
        "definition": "Lower-cased word bigrams inside a sentence that never occur in the reference text.",
    },
    "trigram_surprisal_bits": {
        "status": "parse-based",
        "needs_reference": True,
        "definition": "Mean surprisal in bits per token under an add-k trigram model trained on the reference text (the paper reports n-gram perplexity; 2 ** surprisal is a perplexity).",
    },
}

RELATIVE_WORDS = {"that", "which", "who", "whom", "whose"}
SUBJECT_DEPS = {"nsubj", "nsubjpass", "csubj", "csubjpass"}
NOUN_POS = {"NOUN", "PROPN"}

LIGHT_VERBS = {
    "be", "have", "do", "make", "get", "give", "take", "go", "come", "see", "say",
    "know", "use", "need", "want", "work", "show", "find", "call", "keep", "let",
    "put", "set", "turn", "help", "mean", "seem", "become", "include", "provide",
}
DENOMINAL_VERB_LIST = {
    "leverage", "impact", "interface", "benchmark", "pipeline", "harness", "surface",
    "spotlight", "showcase", "bridge", "anchor", "scaffold", "catalog", "chart",
    "frame", "mirror", "pilot", "position", "shape", "target", "trigger", "underscore",
    "foreground", "ground", "host", "index", "instrument", "task", "author", "action",
    "architect", "engineer", "gate", "signal", "silo", "sunset", "transition", "trend",
    "workshop", "dialogue", "incentivize", "operationalize", "contextualize",
}
ANIMATE_ONLY_VERBS = {
    "argue", "believe", "claim", "think", "hope", "decide", "want", "wish", "intend",
    "assume", "suppose", "recognize", "realize", "understand", "acknowledge", "worry",
    "fear", "expect", "notice", "remember", "forget", "learn", "attempt", "strive",
    "seek", "aim", "choose", "prefer", "refuse", "agree", "insist", "deny", "promise",
    "consider", "ignore", "appreciate", "know", "feel", "hesitate", "struggle",
    "endeavor", "endeavour", "envision", "imagine", "anticipate", "contend", "posit",
    "conjecture", "speculate", "advocate", "recommend", "caution", "warn", "admit",
    "concede", "doubt", "trust", "distrust", "strategize", "plan", "pursue",
}
ANIMATE_NOUNS = {
    "person", "people", "author", "authors", "researcher", "scientist", "engineer",
    "participant", "subject", "user", "reader", "reviewer", "editor", "student",
    "patient", "human", "man", "woman", "child", "team", "group", "community",
    "committee", "panel", "expert", "annotator", "rater", "clinician", "doctor",
    "physician", "nurse", "teacher", "worker", "practitioner", "developer",
    "programmer", "analyst", "individual", "respondent", "volunteer", "speaker",
    "writer", "agent", "stakeholder", "investigator", "colleague", "critic", "we",
    "everyone", "anyone", "someone", "nobody", "one",
}
ANIMATE_PRONOUNS = {"i", "we", "you", "he", "she", "they", "who", "whoever", "someone", "everyone", "anyone", "one"}
INANIMATE_PRONOUNS = {"it", "this", "that", "these", "those", "what", "which", "something", "everything", "nothing"}
NUMBER_TOKEN = "<num>"
UNKNOWN_TOKEN = "<unk>"
SENTENCE_START = "<s>"
SENTENCE_END = "</s>"


class DifficultyError(Exception):
    pass


# ---------------------------------------------------------------------------
# Feature detectors over a minimal token protocol
#
# A token needs: i, text, lower_, lemma_, pos_, tag_, dep_, head, children,
# is_punct, is_space, like_num. A sentence is a sequence of tokens. spaCy tokens
# satisfy this; the tests build small fake tokens with the same attributes.
# ---------------------------------------------------------------------------

def is_word(token: Any) -> bool:
    return not token.is_punct and not token.is_space


def word_form(token: Any) -> str:
    if token.like_num:
        return NUMBER_TOKEN
    return token.lower_


def sentence_words(sentence: Sequence[Any]) -> list[str]:
    return [word_form(t) for t in sentence if is_word(t)]


def is_relative_word(token: Any) -> bool:
    return token.tag_ in {"WDT", "WP", "WP$"} or token.lower_ in RELATIVE_WORDS


def _relative_word_attached(verb: Any) -> bool:
    for child in verb.children:
        if is_relative_word(child):
            return True
        if child.dep_ in {"prep", "agent"}:
            for grandchild in child.children:
                if is_relative_word(grandchild):
                    return True
    return False


def object_relative_clauses(sentence: Sequence[Any]) -> tuple[list[Any], list[Any]]:
    """Return (verbs of ORCs with a relativizer, verbs of ORCs without one)."""
    with_relativizer: list[Any] = []
    without_relativizer: list[Any] = []
    for token in sentence:
        if token.dep_ != "relcl":
            continue
        subjects = [c for c in token.children if c.dep_ in SUBJECT_DEPS]
        if not subjects or all(is_relative_word(s) for s in subjects):
            continue  # subject relative clause, or no explicit subject
        if _relative_word_attached(token):
            with_relativizer.append(token)
        else:
            without_relativizer.append(token)
    return with_relativizer, without_relativizer


def noun_lemmas(sentences: Iterable[Sequence[Any]]) -> set[str]:
    return {t.lemma_.lower() for sentence in sentences for t in sentence if t.pos_ == "NOUN"}


def denominal_verbs(sentence: Sequence[Any], nouns_in_text: set[str]) -> list[Any]:
    found: list[Any] = []
    for token in sentence:
        if token.pos_ != "VERB":
            continue
        lemma = token.lemma_.lower()
        if lemma in LIGHT_VERBS:
            continue
        if lemma in DENOMINAL_VERB_LIST or lemma in nouns_in_text:
            found.append(token)
    return found


def _subject_is_inanimate(subject: Any) -> bool | None:
    """True, False, or None when animacy cannot be judged (proper nouns, clauses)."""
    lemma = subject.lemma_.lower()
    lower = subject.lower_
    if subject.pos_ == "PRON":
        if lower in ANIMATE_PRONOUNS or lemma in ANIMATE_PRONOUNS:
            return False
        if lower in INANIMATE_PRONOUNS or lemma in INANIMATE_PRONOUNS:
            return True
        return None
    if subject.pos_ == "NOUN":
        return lemma not in ANIMATE_NOUNS and lower not in ANIMATE_NOUNS
    return None


def inanimate_subjects_of_animate_verbs(sentence: Sequence[Any]) -> list[tuple[Any, Any]]:
    found: list[tuple[Any, Any]] = []
    for token in sentence:
        if token.pos_ != "VERB" or token.lemma_.lower() not in ANIMATE_ONLY_VERBS:
            continue
        for child in token.children:
            if child.dep_ in {"nsubj", "nsubjpass"} and _subject_is_inanimate(child) is True:
                found.append((child, token))
    return found


def stacked_nouns(sentence: Sequence[Any], minimum: int = 3) -> list[list[Any]]:
    runs: list[list[Any]] = []
    current: list[Any] = []
    for token in sentence:
        if token.pos_ in NOUN_POS:
            current.append(token)
            continue
        if len(current) >= minimum:
            runs.append(current)
        current = []
    if len(current) >= minimum:
        runs.append(current)
    return runs


def dependency_length(sentence: Sequence[Any]) -> tuple[int, int]:
    """Return (sentence length in non-punctuation tokens, summed head distance)."""
    length = 0
    total = 0
    for token in sentence:
        if not is_word(token):
            continue
        length += 1
        if token.head is not token and token.head.i != token.i:
            total += abs(token.i - token.head.i)
    return length, total


# ---------------------------------------------------------------------------
# Reference-dependent statistics (pure stdlib)
# ---------------------------------------------------------------------------

def length_bin(length: int, width: int) -> int:
    return (length - 1) // width if length > 0 else 0


def standardize_dependency_lengths(
    sentences: Sequence[tuple[int, int]],
    reference: Sequence[tuple[int, int]],
    bin_width: int = 5,
) -> list[float | None]:
    """z-score each (length, total) pair against reference pairs in the same length bin.

    A bin needs at least two reference sentences and a non-zero spread; otherwise the
    sentence gets None. Sentences with no words get None.
    """
    bins: dict[int, list[int]] = defaultdict(list)
    for length, total in reference:
        if length > 0:
            bins[length_bin(length, bin_width)].append(total)
    stats: dict[int, tuple[float, float]] = {}
    for key, totals in bins.items():
        if len(totals) < 2:
            continue
        mean = sum(totals) / len(totals)
        variance = sum((t - mean) ** 2 for t in totals) / (len(totals) - 1)
        stats[key] = (mean, math.sqrt(variance))
    result: list[float | None] = []
    for length, total in sentences:
        if length <= 0:
            result.append(None)
            continue
        found = stats.get(length_bin(length, bin_width))
        if found is None or found[1] == 0:
            result.append(None)
        else:
            result.append((total - found[0]) / found[1])
    return result


class BigramTable:
    def __init__(self, bigrams: Iterable[tuple[str, str]] = ()) -> None:
        self.bigrams = set(bigrams)

    @classmethod
    def from_sentences(cls, sentences: Iterable[Sequence[str]]) -> "BigramTable":
        table = cls()
        for words in sentences:
            table.bigrams.update(zip(words, words[1:]))
        return table

    def unattested(self, words: Sequence[str]) -> list[tuple[str, str]]:
        return [pair for pair in zip(words, words[1:]) if pair not in self.bigrams]


class TrigramModel:
    """Add-k smoothed trigram model with <s> <s> ... </s> padding and an <unk> class."""

    def __init__(self, k: float = 0.01) -> None:
        if k <= 0:
            raise DifficultyError("Add-k constant must be positive.")
        self.k = k
        self.trigrams: Counter[tuple[str, str, str]] = Counter()
        self.bigrams: Counter[tuple[str, str]] = Counter()
        self.vocabulary: set[str] = {SENTENCE_END, UNKNOWN_TOKEN}

    def train(self, sentences: Iterable[Sequence[str]]) -> "TrigramModel":
        for words in sentences:
            self.vocabulary.update(words)
            padded = [SENTENCE_START, SENTENCE_START, *words, SENTENCE_END]
            for i in range(2, len(padded)):
                self.trigrams[(padded[i - 2], padded[i - 1], padded[i])] += 1
                self.bigrams[(padded[i - 2], padded[i - 1])] += 1
        return self

    def _map(self, word: str) -> str:
        if word == SENTENCE_START or word in self.vocabulary:
            return word
        return UNKNOWN_TOKEN

    def probability(self, w1: str, w2: str, w3: str) -> float:
        w1, w2, w3 = self._map(w1), self._map(w2), self._map(w3)
        numerator = self.trigrams[(w1, w2, w3)] + self.k
        denominator = self.bigrams[(w1, w2)] + self.k * len(self.vocabulary)
        return numerator / denominator

    def surprisal_bits(self, words: Sequence[str]) -> float | None:
        """Mean surprisal in bits per predicted token, the sentence end included."""
        if not words:
            return None
        padded = [SENTENCE_START, SENTENCE_START, *words, SENTENCE_END]
        total = 0.0
        for i in range(2, len(padded)):
            total -= math.log2(self.probability(padded[i - 2], padded[i - 1], padded[i]))
        return total / (len(padded) - 2)


# ---------------------------------------------------------------------------
# Ranking and comparison (pure stdlib)
# ---------------------------------------------------------------------------

def zscores(values: Sequence[float | None]) -> list[float | None]:
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [None if v is None else 0.0 for v in values]
    mean = sum(present) / len(present)
    variance = sum((v - mean) ** 2 for v in present) / (len(present) - 1)
    spread = math.sqrt(variance)
    if spread == 0:
        return [None if v is None else 0.0 for v in values]
    return [None if v is None else (v - mean) / spread for v in values]


def rank_blocks(rows: Sequence[dict[str, Any]], feature_names: Sequence[str], top: int) -> list[dict[str, Any]]:
    """rows: {"block_id", "features": {name: float|None}}. Returns the top rows by z-sum.

    Missing values (None) and zero-variance features contribute 0 to the sum. The
    driver is the feature with the largest positive z; None when no z is positive.
    Ties keep document order.
    """
    columns = {name: zscores([row["features"].get(name) for row in rows]) for name in feature_names}
    ranked: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        z = {name: columns[name][position] for name in feature_names}
        total = sum(v for v in z.values() if v is not None)
        positive = [(v, name) for name, v in z.items() if v is not None and v > 0]
        driver = max(positive, key=lambda item: item[0])[1] if positive else None
        ranked.append({"block_id": row["block_id"], "z_sum": round(total, 4), "z": {k: (None if v is None else round(v, 4)) for k, v in z.items()}, "driver": driver, "position": position})
    ranked.sort(key=lambda item: (-item["z_sum"], item["position"]))
    for rank, item in enumerate(ranked, 1):
        item["rank"] = rank
        del item["position"]
    return ranked[:top]


def word_tokens(text: str) -> list[str]:
    """Lower-cased word tokens; no parse needed."""
    return [match.group(0).lower() for match in WORD_TOKEN.finditer(text)]


def edit_ratio(original_words: Sequence[str], proposed_words: Sequence[str]) -> tuple[int, float | None]:
    """Changed words and their share of the original: a replaced run counts once by its longer side."""
    changed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=list(original_words), b=list(proposed_words), autojunk=False).get_opcodes():
        if tag == "replace":
            changed += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            changed += i2 - i1
        elif tag == "insert":
            changed += j2 - j1
    return changed, (round(changed / len(original_words), 4) if original_words else None)


def edit_weight(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio > HEAVY_EDIT_RATIO:
        return "heavy"
    return "light" if ratio <= LIGHT_EDIT_RATIO else "medium"


def lexical_measures(original_text: str, proposed_text: str) -> dict[str, Any]:
    """Edit ratio, type-token ratio and mean word length of the two texts, with advisory flags."""
    a, b = word_tokens(original_text), word_tokens(proposed_text)
    changed, ratio = edit_ratio(a, b)
    weight = edit_weight(ratio)
    ttr_a = round(len(set(a)) / len(a), 4) if a else None
    ttr_b = round(len(set(b)) / len(b), 4) if b else None
    len_a = round(sum(map(len, a)) / len(a), 3) if a else None
    len_b = round(sum(map(len, b)) / len(b), 3) if b else None
    flags = []
    if weight == "heavy":
        flags.append("heavy_edit")
    if ttr_a is not None and ttr_b is not None and ttr_b < ttr_a:
        flags.append("lower_type_token_ratio")
    if len_a is not None and len_b is not None and len_b > len_a:
        flags.append("longer_words")
    return {
        "original_words": len(a),
        "proposed_words": len(b),
        "changed_words": changed,
        "edit_ratio": ratio,
        "edit_weight": weight,
        "type_token_ratio": {"original": ttr_a, "proposed": ttr_b, "proposed_is_lower": "lower_type_token_ratio" in flags},
        "mean_word_length": {"original": len_a, "proposed": len_b, "proposed_is_longer": "longer_words" in flags},
        "advisory_flags": flags,
    }


def load_excess_vocabulary(path: str | Path | None) -> dict[str, Any] | None:
    """The excess-vocabulary asset as {word: part_of_speech} plus its source; None when disabled."""
    if path is None:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("groups"), dict) or not isinstance(data.get("source"), dict):
        raise DifficultyError("The excess-vocabulary file must be a JSON object with groups (part of speech to word list) and source.")
    words: dict[str, str] = {}
    for pos, items in data["groups"].items():
        if not isinstance(items, list) or any(not isinstance(w, str) or not w.strip() or w != w.lower() for w in items):
            raise DifficultyError(f"excess vocabulary group {pos!r} must list lower-cased words.")
        for word in items:
            words.setdefault(word, pos)
    return {"path": str(path), "source": data["source"], "words": words, "count": len(words)}


def count_excess_vocabulary(text: str, vocabulary: dict[str, Any]) -> dict[str, Any]:
    """Tokens of `text` whose lower-cased form is in the list; a nomination signal, not a finding."""
    tokens = word_tokens(text)
    matches = [t for t in tokens if t in vocabulary["words"]]
    return {"count": len(matches), "per_1k_words": round(1000 * len(matches) / len(tokens), 2) if tokens else None, "matches": sorted(set(matches))}


def compare_measures(original: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """Side-by-side counts with a flag for every feature the proposed text has more of."""
    features: dict[str, Any] = {}
    flagged: list[str] = []
    for name in COUNT_FEATURES:
        before = original["counts"].get(name)
        after = proposed["counts"].get(name)
        more = before is not None and after is not None and after > before
        features[name] = {"original": before, "proposed": after, "proposed_has_more": more}
        if more:
            flagged.append(name)
    for name, key in (("dependency_length", "mean_per_sentence"), ("dependency_length_standardized", "standardized_mean")):
        before = original["dependency_length"].get(key)
        after = proposed["dependency_length"].get(key)
        more = before is not None and after is not None and after > before
        features[name] = {"original": before, "proposed": after, "proposed_has_more": more}
        if more:
            flagged.append(name)
    before = original.get("trigram_surprisal_bits")
    after = proposed.get("trigram_surprisal_bits")
    more = before is not None and after is not None and after > before
    features["trigram_surprisal_bits"] = {"original": before, "proposed": after, "proposed_has_more": more}
    if more:
        flagged.append("trigram_surprisal_bits")
    return {
        "words": {"original": original["words"], "proposed": proposed["words"]},
        "features": features,
        "flagged": flagged,
        "proposed_has_more_difficulty_features": bool(flagged),
        "advisory": "Advisory only. A flag asks the editor to look again at the proposed wording; it does not reject the repair and does not replace the fidelity audit.",
    }


# ---------------------------------------------------------------------------
# spaCy-backed measurement
# ---------------------------------------------------------------------------

def load_nlp(model: str) -> tuple[Any, str]:
    try:
        import spacy  # type: ignore
    except ImportError:
        print(INSTALL_HELP, file=sys.stderr)
        sys.exit(2)
    names = ["en_core_web_trf", "en_core_web_sm"] if model == "auto" else [model]
    for name in names:
        try:
            return spacy.load(name), name
        except OSError:
            continue
        except ImportError:
            continue
    print(f"No spaCy model among {names} is installed in this Python.\n" + INSTALL_HELP, file=sys.stderr)
    sys.exit(2)


def parse_sentences(nlp: Any, text: str) -> list[list[Any]]:
    doc = nlp(text)
    return [list(sentence) for sentence in doc.sents]


class Reference:
    def __init__(self, sentences: Sequence[Sequence[Any]], bin_width: int = 5, k: float = 0.01) -> None:
        words = [sentence_words(s) for s in sentences]
        words = [w for w in words if w]
        self.sentence_count = len(words)
        self.word_count = sum(len(w) for w in words)
        self.bigrams = BigramTable.from_sentences(words)
        self.trigrams = TrigramModel(k).train(words)
        self.dependency_pairs = [dependency_length(s) for s in sentences]
        self.bin_width = bin_width


def _span(tokens: Sequence[Any]) -> str:
    return " ".join(t.text for t in tokens)


def _clause_text(verb: Any) -> str:
    tokens = sorted(verb.subtree, key=lambda t: t.i) if hasattr(verb, "subtree") else [verb]
    head = verb.head
    if head is not verb and head.i < tokens[0].i:
        tokens = [head, *tokens]
    return _span(tokens)


def measure_sentences(sentences: Sequence[Sequence[Any]], reference: Reference | None, examples_per_feature: int = 3) -> dict[str, Any]:
    nouns = noun_lemmas(sentences)
    counts = Counter({name: 0 for name in COUNT_FEATURES})
    examples: dict[str, list[str]] = {name: [] for name in COUNT_FEATURES}
    dep_pairs: list[tuple[int, int]] = []
    surprisals: list[float] = []
    unattested_count: int | None = 0 if reference else None
    words_total = 0

    def note(name: str, text: str) -> None:
        counts[name] += 1
        if len(examples[name]) < examples_per_feature:
            examples[name].append(text)

    for sentence in sentences:
        words = sentence_words(sentence)
        if not words:
            continue
        words_total += len(words)
        dep_pairs.append(dependency_length(sentence))
        with_rel, without_rel = object_relative_clauses(sentence)
        for verb in with_rel:
            note("orc_with_relativizer", _clause_text(verb))
        for verb in without_rel:
            note("orc_without_relativizer", _clause_text(verb))
        for verb in denominal_verbs(sentence, nouns):
            note("denominal_verbs", verb.text)
        for subject, verb in inanimate_subjects_of_animate_verbs(sentence):
            note("inanimate_subject_animate_verb", f"{subject.text} {verb.text}")
        for run in stacked_nouns(sentence):
            note("stacked_nouns", _span(run))
        if reference is not None:
            missing = reference.bigrams.unattested(words)
            unattested_count += len(missing)
            for pair in missing[: max(0, examples_per_feature - len(examples["unattested_bigrams"]))]:
                examples["unattested_bigrams"].append(" ".join(pair))
            bits = reference.trigrams.surprisal_bits(words)
            if bits is not None:
                surprisals.append(bits)
    counts["unattested_bigrams"] = unattested_count
    sentence_count = len(dep_pairs)
    per_1k = {
        name: (None if value is None or words_total == 0 else round(value * 1000.0 / words_total, 3))
        for name, value in counts.items()
    }
    mean_dep = round(sum(t for _, t in dep_pairs) / sentence_count, 3) if sentence_count else None
    mean_len = round(sum(n for n, _ in dep_pairs) / sentence_count, 3) if sentence_count else None
    standardized: float | None = None
    if reference is not None and sentence_count:
        z = [v for v in standardize_dependency_lengths(dep_pairs, reference.dependency_pairs, reference.bin_width) if v is not None]
        standardized = round(sum(z) / len(z), 4) if z else None
    return {
        "words": words_total,
        "sentences": sentence_count,
        "counts": dict(counts),
        "rates_per_1k_words": per_1k,
        "dependency_length": {"mean_per_sentence": mean_dep, "mean_sentence_length": mean_len, "standardized_mean": standardized},
        "trigram_surprisal_bits": round(sum(surprisals) / len(surprisals), 4) if surprisals else None,
        "examples": {name: items for name, items in examples.items() if items},
    }


def measure_text(nlp: Any, text: str, reference: Reference | None) -> dict[str, Any]:
    return measure_sentences(parse_sentences(nlp, text), reference)


def ranking_features(measure: dict[str, Any], has_reference: bool) -> dict[str, float | None]:
    rates = measure["rates_per_1k_words"]
    features: dict[str, float | None] = {name: rates.get(name) for name in COUNT_FEATURES}
    dep = measure["dependency_length"]
    if has_reference and dep.get("standardized_mean") is not None:
        features["dependency_length"] = dep["standardized_mean"]
    elif dep.get("mean_per_sentence") is not None and dep.get("mean_sentence_length"):
        features["dependency_length"] = dep["mean_per_sentence"] / dep["mean_sentence_length"]
    else:
        features["dependency_length"] = None
    features["trigram_surprisal_bits"] = measure.get("trigram_surprisal_bits")
    return features


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def load_blocks(path: str | Path) -> tuple[list[dict[str, Any]], str, str | None]:
    """Return (blocks, source kind, snapshot_id) from an index.json or a Markdown file."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        if not isinstance(data, dict) or "blocks" not in data or "text" not in data:
            raise DifficultyError("A JSON input must be an index.json from review_packets.py index (with text and blocks).")
        return list(data["blocks"]), "index", data.get("snapshot_id")
    text = rp.normalize_text(raw)
    return rp.build_blocks(text), "markdown", "sha256:" + rp.sha256_text(text)


def load_reference(nlp: Any, path: str | None, bin_width: int, k: float) -> tuple[Reference | None, dict[str, Any] | None]:
    if path is None:
        return None, None
    text = rp.normalize_text(Path(path).read_text(encoding="utf-8"))
    reference = Reference(parse_sentences(nlp, text), bin_width=bin_width, k=k)
    return reference, {"path": str(path), "sentences": reference.sentence_count, "words": reference.word_count, "bin_width": bin_width, "add_k": k}


def read_text_argument(value: str) -> str:
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


def _anchor_text(original: Any) -> str:
    if isinstance(original, str):
        return original
    if isinstance(original, list):
        parts = []
        for item in original:
            if not isinstance(item, dict) or not isinstance(item.get("quote"), str):
                raise DifficultyError("original anchors must be objects with a quote string.")
            parts.append(item["quote"])
        return " ".join(parts)
    raise DifficultyError("original must be a string or a list of {block_id, quote} anchors.")


def compare_pairs_from_args(args: argparse.Namespace) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    if args.original is not None or args.proposed is not None:
        if args.original is None or args.proposed is None:
            raise DifficultyError("--original and --proposed must be given together.")
        pairs.append({"candidate_id": args.candidate_id, "original": read_text_argument(args.original), "proposed_wording": read_text_argument(args.proposed)})
    if args.input is not None:
        request = rp.load_json(args.input)
        if not isinstance(request, dict) or "original" not in request or "proposed_wording" not in request:
            raise DifficultyError("--input must be the audit input JSON with candidate_id, original and proposed_wording.")
        pairs.append({"candidate_id": request.get("candidate_id"), "original": _anchor_text(request["original"]), "proposed_wording": str(request["proposed_wording"])})
    if args.repairs is not None:
        repairs = rp.load_json(args.repairs)
        for repair in repairs.get("repairs", []):
            wording = repair.get("proposed_wording")
            if not isinstance(wording, str) or not wording.strip():
                continue
            if not isinstance(repair.get("audit_path"), str):
                pairs.append({"candidate_id": repair.get("candidate_id"), "skipped": "no audit_path in the repair; audit the wording first"})
                continue
            audit = rp.load_json(repair["audit_path"])
            if not isinstance(audit, dict) or "original" not in audit:
                raise DifficultyError(f"audit_path {repair['audit_path']} for {repair.get('candidate_id')} is not a fidelity audit with an original.")
            pairs.append({"candidate_id": repair.get("candidate_id"), "original": _anchor_text(audit["original"]), "proposed_wording": wording})
    if not pairs:
        raise DifficultyError("Give --original with --proposed, or --input, or --repairs.")
    return pairs


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _header(model_name: str | None, reference_info: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "tool": "difficulty_features.py",
        "version": VERSION,
        "scope": SCOPE_NOTE,
        "source": PAPER,
        "model": model_name,
        "reference": reference_info,
        "features": FEATURE_NOTES,
        "heuristic_features": [name for name, note in FEATURE_NOTES.items() if note["status"] == "heuristic"],
        "reference_dependent_features": [name for name, note in FEATURE_NOTES.items() if note["needs_reference"]],
    }


def run_nominate(args: argparse.Namespace) -> dict[str, Any]:
    vocabulary = load_excess_vocabulary(None if args.no_excess_vocabulary else args.excess_vocabulary)
    nlp, model_name = load_nlp(args.model)
    blocks, source_kind, snapshot_id = load_blocks(args.input)
    reference, reference_info = load_reference(nlp, args.reference, args.bin_width, args.add_k)
    measured: list[dict[str, Any]] = []
    for block in blocks:
        measure = measure_text(nlp, block["text"], reference)
        excess = count_excess_vocabulary(block["text"], vocabulary) if vocabulary else None
        measured.append({"block_id": block["block_id"], "kind": block.get("kind", "text"), **measure, "excess_vocabulary": excess})
    eligible = [m for m in measured if m["kind"] == "text" and m["words"] >= args.min_words]
    rows = [{"block_id": m["block_id"], "features": ranking_features(m, reference is not None)} for m in eligible]
    ranked = rank_blocks(rows, RANKED_FEATURES, args.top)
    by_id = {m["block_id"]: m for m in measured}
    nominations = []
    for item in ranked:
        measure = by_id[item["block_id"]]
        nominations.append({**item, "words": measure["words"], "excess_vocabulary": measure["excess_vocabulary"], "examples": measure["examples"].get(item["driver"], []) if item["driver"] else [], "note": "Candidate for wording_check; inspect in context before recording anything."})
    result = {
        **_header(model_name, reference_info),
        "command": "nominate",
        "input": {"path": str(args.input), "kind": source_kind, "snapshot_id": snapshot_id, "blocks": len(blocks), "ranked_blocks": len(eligible), "min_words": args.min_words},
        "excess_vocabulary": None if vocabulary is None else {"path": vocabulary["path"], "words": vocabulary["count"], "source": vocabulary["source"],
                                                               "note": "A separate count, not part of z_sum. The list is a lower bound that ages and cannot separate an author's style from a model's; a count nominates, it never convicts."},
        "nominations": nominations,
        "blocks": measured,
    }
    rp.write_json_atomic(args.out, result)
    print(f"difficulty_features nominate: {len(eligible)} of {len(blocks)} blocks ranked; model {model_name}; reference {'yes' if reference else 'no'}; excess vocabulary {'yes' if vocabulary else 'no'}")
    print(f"{'rank':>4}  {'block':<7} {'z_sum':>7}  {'words':>5}  {'excess':>6}  driver")
    for item in nominations:
        example = item["examples"][0] if item["examples"] else ""
        excess = "-" if item["excess_vocabulary"] is None else item["excess_vocabulary"]["count"]
        print(f"{item['rank']:>4}  {item['block_id']:<7} {item['z_sum']:>7.2f}  {item['words']:>5}  {str(excess):>6}  {item['driver'] or '-'}  {example[:60]}")
    print("Scope: " + SCOPE_NOTE)
    print(f"Wrote {args.out}")
    return result


def lexical_comparison(candidate_id: Any, original: str, proposed_wording: str) -> dict[str, Any]:
    """One compare row without a parse: the texts, word counts and lexical measures; parse fields empty."""
    lexical = lexical_measures(original, proposed_wording)
    return {"candidate_id": candidate_id, "original": original, "proposed_wording": proposed_wording,
            "words": {"original": lexical["original_words"], "proposed": lexical["proposed_words"]},
            "features": {}, "flagged": [], "proposed_has_more_difficulty_features": False,
            "advisory": "Parse-based features not computed (lexical only). Advisory only; the fidelity audit remains the gate.",
            "lexical": lexical}


def compare_header(model_name: str | None, reference_info: dict[str, Any] | None, lexical_only: bool) -> dict[str, Any]:
    return {**_header(model_name, reference_info), "command": "compare", "lexical_only": lexical_only}


def run_compare(args: argparse.Namespace) -> dict[str, Any]:
    pairs = compare_pairs_from_args(args)
    if args.lexical_only:
        nlp, model_name, reference, reference_info = None, None, None, None
    else:
        nlp, model_name = load_nlp(args.model)
        reference, reference_info = load_reference(nlp, args.reference, args.bin_width, args.add_k)
    comparisons: list[dict[str, Any]] = []
    for pair in pairs:
        if "skipped" in pair:
            comparisons.append(pair)
            continue
        row = lexical_comparison(pair["candidate_id"], pair["original"], pair["proposed_wording"])
        if nlp is not None:
            original = measure_text(nlp, pair["original"], reference)
            proposed = measure_text(nlp, pair["proposed_wording"], reference)
            row.update(compare_measures(original, proposed), examples={"original": original["examples"], "proposed": proposed["examples"]})
        comparisons.append(row)
    result = {**compare_header(model_name, reference_info, bool(args.lexical_only)), "comparisons": comparisons}
    rp.write_json_atomic(args.out, result)
    print(f"difficulty_features compare: {len(comparisons)} pair(s); model {model_name or 'none (lexical only)'}; reference {'yes' if reference else 'no'}")
    for item in comparisons:
        label = item.get("candidate_id") or "-"
        if "skipped" in item:
            print(f"{label}: skipped ({item['skipped']})")
            continue
        lexical = item["lexical"]
        print(f"{label}: words {item['words']['original']} -> {item['words']['proposed']}; flagged: {', '.join(item['flagged']) or 'none'}; "
              f"edit ratio {lexical['edit_ratio']} ({lexical['edit_weight']}); advisory: {', '.join(lexical['advisory_flags']) or 'none'}")
        if item["features"]:
            print(f"  {'feature':<34} {'orig':>8} {'prop':>8}  more?")
            for name, cell in item["features"].items():
                before = "-" if cell["original"] is None else cell["original"]
                after = "-" if cell["proposed"] is None else cell["proposed"]
                print(f"  {name:<34} {str(before):>8} {str(after):>8}  {'yes' if cell['proposed_has_more'] else ''}")
        ttr, length = lexical["type_token_ratio"], lexical["mean_word_length"]
        print(f"  {'type_token_ratio':<34} {str(ttr['original']):>8} {str(ttr['proposed']):>8}  {'lower' if ttr['proposed_is_lower'] else ''}")
        print(f"  {'mean_word_length':<34} {str(length['original']):>8} {str(length['proposed']):>8}  {'longer' if length['proposed_is_longer'] else ''}")
    print("Advisory only; the fidelity audit remains the gate. A heavy edit (ratio above 0.5) needs heavy_edit_reason in the repair or a split. Scope: " + SCOPE_NOTE)
    print(f"Wrote {args.out}")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--model", default="auto", help="spaCy model name, or auto (en_core_web_trf if installed, else en_core_web_sm).")
        command.add_argument("--reference", help="Reference corpus text (plain text or Markdown) for bigrams, trigram surprisal and dependency-length bins.")
        command.add_argument("--bin-width", type=int, default=5, help="Sentence-length bin width in tokens for dependency-length standardization (default 5).")
        command.add_argument("--add-k", type=float, default=0.01, help="Add-k constant of the trigram model (default 0.01).")
        command.add_argument("--out", required=True, help="Output JSON path (must not exist).")

    nominate = sub.add_parser("nominate", help="Rank blocks by a within-document z-score sum.")
    nominate.add_argument("--input", required=True, help="index.json from review_packets.py index, or a Markdown file.")
    nominate.add_argument("--top", type=int, default=10, help="How many blocks to print (default 10).")
    nominate.add_argument("--min-words", type=int, default=20, help="Rank only text blocks with at least this many words (default 20).")
    nominate.add_argument("--excess-vocabulary", default=str(EXCESS_VOCABULARY), help=f"Word list counted per block as a separate column (default {EXCESS_VOCABULARY}).")
    nominate.add_argument("--no-excess-vocabulary", action="store_true", help="Skip the excess-vocabulary count.")
    common(nominate)

    compare = sub.add_parser("compare", help="Compare an original quote with a proposed wording (advisory).")
    compare.add_argument("--original", help="Original text, or @path to read a file.")
    compare.add_argument("--proposed", help="Proposed wording, or @path to read a file.")
    compare.add_argument("--candidate-id", help="Label for the --original/--proposed pair.")
    compare.add_argument("--input", help="Audit input JSON (candidate_id, original anchors, proposed_wording).")
    compare.add_argument("--repairs", help="repairs.json; every repair with proposed_wording is compared with the original its fidelity audit (audit_path) quoted.")
    compare.add_argument("--lexical-only", action="store_true", help="Compute only the edit ratio, type-token ratio and mean word length (no spaCy needed).")
    common(compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "nominate":
            run_nominate(args)
        else:
            run_compare(args)
    except (DifficultyError, rp.ReviewDataError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
