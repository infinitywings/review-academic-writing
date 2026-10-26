import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


df = load("difficulty_features")
rp = df.rp


class FakeToken:
    def __init__(self, i, text, lemma, pos, tag, dep):
        self.i = i
        self.text = text
        self.lower_ = text.lower()
        self.lemma_ = lemma
        self.pos_ = pos
        self.tag_ = tag
        self.dep_ = dep
        self.head = self
        self.children = []
        self.is_punct = pos == "PUNCT"
        self.is_space = pos == "SPACE"
        self.like_num = pos == "NUM"


def sentence(spec):
    """spec: list of (text, lemma, pos, tag, dep, head_index); head_index == own index for the root."""
    tokens = [FakeToken(i, *row[:5]) for i, row in enumerate(spec)]
    for token, row in zip(tokens, spec):
        token.head = tokens[row[5]]
        if token.head is not token:
            token.head.children.append(token)
    return tokens


def _spacy_model_available():
    try:
        import spacy  # noqa: F401
    except ImportError:
        return False
    try:
        import spacy
        spacy.load("en_core_web_sm")
    except OSError:
        return False
    return True


HAS_SPACY = _spacy_model_available()


class StackedNounTests(unittest.TestCase):
    def test_run_of_four_nouns_counts_once(self):
        s = sentence([
            ("The", "the", "DET", "DT", "det", 4),
            ("model", "model", "NOUN", "NN", "compound", 4),
            ("training", "training", "NOUN", "NN", "compound", 4),
            ("pipeline", "pipeline", "NOUN", "NN", "compound", 4),
            ("evaluation", "evaluation", "NOUN", "NN", "nsubj", 5),
            ("failed", "fail", "VERB", "VBD", "ROOT", 5),
            (".", ".", "PUNCT", ".", "punct", 5),
        ])
        runs = df.stacked_nouns(s)
        self.assertEqual([[t.text for t in run] for run in runs], [["model", "training", "pipeline", "evaluation"]])

    def test_two_nouns_do_not_count(self):
        s = sentence([
            ("The", "the", "DET", "DT", "det", 2),
            ("data", "data", "NOUN", "NN", "compound", 2),
            ("set", "set", "NOUN", "NN", "nsubj", 3),
            ("grew", "grow", "VERB", "VBD", "ROOT", 3),
        ])
        self.assertEqual(df.stacked_nouns(s), [])

    def test_runs_separated_by_a_verb_count_separately(self):
        s = sentence([
            ("Feature", "feature", "NOUN", "NN", "compound", 2),
            ("extraction", "extraction", "NOUN", "NN", "compound", 2),
            ("modules", "module", "NOUN", "NNS", "nsubj", 3),
            ("use", "use", "VERB", "VBP", "ROOT", 3),
            ("graph", "graph", "NOUN", "NN", "compound", 6),
            ("attention", "attention", "NOUN", "NN", "compound", 6),
            ("layers", "layer", "NOUN", "NNS", "dobj", 3),
        ])
        self.assertEqual(len(df.stacked_nouns(s)), 2)


class RelativeClauseTests(unittest.TestCase):
    def test_object_relative_with_that(self):
        s = sentence([
            ("The", "the", "DET", "DT", "det", 1),
            ("book", "book", "NOUN", "NN", "nsubj", 5),
            ("that", "that", "PRON", "WDT", "dobj", 4),
            ("we", "we", "PRON", "PRP", "nsubj", 4),
            ("read", "read", "VERB", "VBD", "relcl", 1),
            ("was", "be", "AUX", "VBD", "ROOT", 5),
            ("long", "long", "ADJ", "JJ", "acomp", 5),
        ])
        with_rel, without_rel = df.object_relative_clauses(s)
        self.assertEqual([t.text for t in with_rel], ["read"])
        self.assertEqual(without_rel, [])

    def test_reduced_object_relative(self):
        s = sentence([
            ("The", "the", "DET", "DT", "det", 1),
            ("paper", "paper", "NOUN", "NN", "nsubj", 4),
            ("we", "we", "PRON", "PRP", "nsubj", 3),
            ("cite", "cite", "VERB", "VBP", "relcl", 1),
            ("argues", "argue", "VERB", "VBZ", "ROOT", 4),
            ("this", "this", "PRON", "DT", "dobj", 4),
        ])
        with_rel, without_rel = df.object_relative_clauses(s)
        self.assertEqual(with_rel, [])
        self.assertEqual([t.text for t in without_rel], ["cite"])

    def test_subject_relative_is_not_counted(self):
        s = sentence([
            ("The", "the", "DET", "DT", "det", 1),
            ("book", "book", "NOUN", "NN", "nsubj", 5),
            ("that", "that", "PRON", "WDT", "nsubjpass", 4),
            ("was", "be", "AUX", "VBD", "auxpass", 4),
            ("read", "read", "VERB", "VBN", "relcl", 1),
            ("helped", "help", "VERB", "VBD", "ROOT", 5),
        ])
        self.assertEqual(df.object_relative_clauses(s), ([], []))

    def test_relativizer_inside_preposition(self):
        s = sentence([
            ("the", "the", "DET", "DT", "det", 1),
            ("tool", "tool", "NOUN", "NN", "ROOT", 1),
            ("with", "with", "ADP", "IN", "prep", 5),
            ("which", "which", "PRON", "WDT", "pobj", 2),
            ("we", "we", "PRON", "PRP", "nsubj", 5),
            ("measured", "measure", "VERB", "VBD", "relcl", 1),
        ])
        with_rel, without_rel = df.object_relative_clauses(s)
        self.assertEqual([t.text for t in with_rel], ["measured"])
        self.assertEqual(without_rel, [])


class DenominalAndAnimacyTests(unittest.TestCase):
    def test_noun_attested_in_text_marks_verb(self):
        s1 = sentence([
            ("The", "the", "DET", "DT", "det", 1),
            ("pipeline", "pipeline", "NOUN", "NN", "nsubj", 2),
            ("runs", "run", "VERB", "VBZ", "ROOT", 2),
        ])
        s2 = sentence([
            ("We", "we", "PRON", "PRP", "nsubj", 1),
            ("pipeline", "pipeline", "VERB", "VBP", "ROOT", 1),
            ("the", "the", "DET", "DT", "det", 3),
            ("steps", "step", "NOUN", "NNS", "dobj", 1),
        ])
        nouns = df.noun_lemmas([s1, s2])
        self.assertEqual(nouns, {"pipeline", "step"})
        self.assertEqual([t.text for t in df.denominal_verbs(s2, nouns)], ["pipeline"])
        self.assertEqual(df.denominal_verbs(s1, nouns), [])

    def test_light_verb_excluded_and_builtin_list_included(self):
        s = sentence([
            ("We", "we", "PRON", "PRP", "nsubj", 1),
            ("use", "use", "VERB", "VBP", "ROOT", 1),
            ("and", "and", "CCONJ", "CC", "cc", 1),
            ("leverage", "leverage", "VERB", "VBP", "conj", 1),
            ("a", "a", "DET", "DT", "det", 5),
            ("use", "use", "NOUN", "NN", "dobj", 3),
        ])
        self.assertEqual([t.text for t in df.denominal_verbs(s, df.noun_lemmas([s]))], ["leverage"])

    def test_inanimate_subject_of_animate_verb(self):
        s = sentence([
            ("The", "the", "DET", "DT", "det", 1),
            ("model", "model", "NOUN", "NN", "nsubj", 2),
            ("believes", "believe", "VERB", "VBZ", "ROOT", 2),
            ("this", "this", "PRON", "DT", "dobj", 2),
        ])
        pairs = df.inanimate_subjects_of_animate_verbs(s)
        self.assertEqual([(a.text, b.text) for a, b in pairs], [("model", "believes")])

    def test_animate_subjects_are_not_flagged(self):
        for subject, lemma, pos, tag in (("We", "we", "PRON", "PRP"), ("Authors", "author", "NOUN", "NNS"), ("Smith", "Smith", "PROPN", "NNP")):
            s = sentence([
                (subject, lemma, pos, tag, "nsubj", 1),
                ("argue", "argue", "VERB", "VBP", "ROOT", 1),
            ])
            self.assertEqual(df.inanimate_subjects_of_animate_verbs(s), [])

    def test_non_animate_verb_is_ignored(self):
        s = sentence([
            ("The", "the", "DET", "DT", "det", 1),
            ("table", "table", "NOUN", "NN", "nsubj", 2),
            ("shows", "show", "VERB", "VBZ", "ROOT", 2),
            ("results", "result", "NOUN", "NNS", "dobj", 2),
        ])
        self.assertEqual(df.inanimate_subjects_of_animate_verbs(s), [])


class DependencyLengthTests(unittest.TestCase):
    def test_length_and_total_distance(self):
        s = sentence([
            ("The", "the", "DET", "DT", "det", 1),
            ("book", "book", "NOUN", "NN", "nsubj", 2),
            ("was", "be", "AUX", "VBD", "ROOT", 2),
            ("long", "long", "ADJ", "JJ", "acomp", 2),
            (".", ".", "PUNCT", ".", "punct", 2),
        ])
        self.assertEqual(df.dependency_length(s), (4, 3))

    def test_standardization_against_reference_bins(self):
        reference = [(5, 4), (5, 6), (5, 8), (12, 20)]
        result = df.standardize_dependency_lengths([(4, 8), (3, 6), (12, 30), (0, 0)], reference, bin_width=5)
        self.assertEqual(result, [1.0, 0.0, None, None])

    def test_zero_spread_bin_gives_none(self):
        self.assertEqual(df.standardize_dependency_lengths([(5, 9)], [(5, 7), (5, 7)], bin_width=5), [None])

    def test_length_bin(self):
        self.assertEqual([df.length_bin(n, 5) for n in (1, 5, 6, 10, 11)], [0, 0, 1, 1, 2])


class NgramTests(unittest.TestCase):
    def test_unattested_bigrams(self):
        table = df.BigramTable.from_sentences([["we", "read", "the", "paper"], ["the", "paper", "was", "long"]])
        self.assertEqual(table.unattested(["we", "read", "the", "book"]), [("the", "book")])
        self.assertEqual(table.unattested(["the", "paper"]), [])

    def test_trigram_probabilities_with_add_one(self):
        model = df.TrigramModel(k=1.0).train([["a", "b"]])
        self.assertEqual(sorted(model.vocabulary), ["</s>", "<unk>", "a", "b"])
        self.assertAlmostEqual(model.probability("<s>", "<s>", "a"), 0.4, places=9)
        self.assertAlmostEqual(model.probability("a", "b", "zzz"), 0.2, places=9)
        self.assertAlmostEqual(model.probability("q", "q", "q"), 0.25, places=9)

    def test_trigram_surprisal_mean_bits(self):
        model = df.TrigramModel(k=1.0).train([["a", "b"]])
        self.assertAlmostEqual(model.surprisal_bits(["a", "b"]), 1.3219, places=4)
        self.assertIsNone(model.surprisal_bits([]))

    def test_add_k_must_be_positive(self):
        with self.assertRaises(df.DifficultyError):
            df.TrigramModel(k=0)


class RankingTests(unittest.TestCase):
    def test_zscores(self):
        self.assertEqual(df.zscores([1.0, 2.0, 3.0]), [-1.0, 0.0, 1.0])
        self.assertEqual(df.zscores([2.0, 2.0, 2.0]), [0.0, 0.0, 0.0])
        self.assertEqual(df.zscores([1.0, None, 3.0]), [-0.7071067811865475, None, 0.7071067811865475])
        self.assertEqual(df.zscores([5.0]), [0.0])

    def test_rank_blocks_orders_by_z_sum_and_names_driver(self):
        rows = [
            {"block_id": "B0001", "features": {"a": 1.0, "b": 0.0}},
            {"block_id": "B0002", "features": {"a": 2.0, "b": 0.0}},
            {"block_id": "B0003", "features": {"a": 3.0, "b": None}},
        ]
        ranked = df.rank_blocks(rows, ["a", "b"], top=2)
        self.assertEqual([r["block_id"] for r in ranked], ["B0003", "B0002"])
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[0]["z_sum"], 1.0)
        self.assertEqual(ranked[0]["driver"], "a")
        self.assertEqual(ranked[0]["z"], {"a": 1.0, "b": None})
        self.assertEqual(ranked[1]["z"], {"a": 0.0, "b": 0.0})
        self.assertIsNone(ranked[1]["driver"])

    def test_rank_blocks_ties_keep_document_order(self):
        rows = [{"block_id": "B0001", "features": {"a": 1.0}}, {"block_id": "B0002", "features": {"a": 1.0}}]
        self.assertEqual([r["block_id"] for r in df.rank_blocks(rows, ["a"], top=5)], ["B0001", "B0002"])


def _measure(counts, dep_mean=10.0, dep_std=None, surprisal=None, words=10):
    base = {name: 0 for name in df.COUNT_FEATURES}
    base.update(counts)
    return {
        "words": words,
        "counts": base,
        "dependency_length": {"mean_per_sentence": dep_mean, "mean_sentence_length": 8.0, "standardized_mean": dep_std},
        "trigram_surprisal_bits": surprisal,
    }


class CompareTests(unittest.TestCase):
    def test_flag_when_proposed_has_more(self):
        result = df.compare_measures(_measure({"stacked_nouns": 1}), _measure({"stacked_nouns": 2, "denominal_verbs": 1}, dep_mean=12.0))
        self.assertEqual(result["flagged"], ["denominal_verbs", "stacked_nouns", "dependency_length"])
        self.assertTrue(result["proposed_has_more_difficulty_features"])
        self.assertEqual(result["features"]["stacked_nouns"], {"original": 1, "proposed": 2, "proposed_has_more": True})
        self.assertEqual(result["features"]["orc_with_relativizer"], {"original": 0, "proposed": 0, "proposed_has_more": False})
        self.assertEqual(result["words"], {"original": 10, "proposed": 10})

    def test_no_flag_when_proposed_has_fewer_or_equal(self):
        result = df.compare_measures(_measure({"stacked_nouns": 2}, dep_mean=12.0, surprisal=9.0), _measure({"stacked_nouns": 2}, dep_mean=9.0, surprisal=8.5))
        self.assertEqual(result["flagged"], [])
        self.assertFalse(result["proposed_has_more_difficulty_features"])
        self.assertEqual(result["features"]["trigram_surprisal_bits"], {"original": 9.0, "proposed": 8.5, "proposed_has_more": False})

    def test_missing_reference_values_never_flag(self):
        result = df.compare_measures(_measure({"unattested_bigrams": None}), _measure({"unattested_bigrams": None}))
        self.assertEqual(result["features"]["unattested_bigrams"], {"original": None, "proposed": None, "proposed_has_more": False})
        self.assertEqual(result["features"]["dependency_length_standardized"], {"original": None, "proposed": None, "proposed_has_more": False})


class InputTests(unittest.TestCase):
    def test_load_blocks_from_markdown_and_index(self):
        text = "# Title\n\nFirst paragraph.\n\nSecond paragraph.\n"
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "m.md"
            md.write_text(text, encoding="utf-8")
            index = Path(tmp) / "index.json"
            index.write_text(json.dumps(rp.build_index(text)), encoding="utf-8")
            blocks, kind, snapshot = df.load_blocks(md)
            self.assertEqual(kind, "markdown")
            self.assertEqual([b["block_id"] for b in blocks], ["B0001", "B0002", "B0003"])
            self.assertEqual(blocks[0]["kind"], "heading")
            blocks2, kind2, snapshot2 = df.load_blocks(index)
            self.assertEqual(kind2, "index")
            self.assertEqual(snapshot, snapshot2)
            self.assertEqual([b["text"] for b in blocks2], ["# Title", "First paragraph.", "Second paragraph."])

    def test_load_blocks_rejects_other_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{\"x\": 1}", encoding="utf-8")
            with self.assertRaises(df.DifficultyError):
                df.load_blocks(bad)

    def test_anchor_text_joins_quotes(self):
        self.assertEqual(df._anchor_text([{"block_id": "B0001", "quote": "one"}, {"block_id": "B0002", "quote": "two"}]), "one two")
        self.assertEqual(df._anchor_text("plain"), "plain")
        with self.assertRaises(df.DifficultyError):
            df._anchor_text([{"block_id": "B0001"}])

    def test_compare_pairs_from_repairs_and_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repairs = Path(tmp) / "repairs.json"
            checks = Path(tmp) / "checks.json"
            repairs.write_text(json.dumps({"repairs": [
                {"candidate_id": "C01", "proposed_wording": "We use the parser."},
                {"candidate_id": "C02", "proposed_wording": None},
                {"candidate_id": "C03", "proposed_wording": "Orphan wording."},
            ]}), encoding="utf-8")
            checks.write_text(json.dumps({"candidates": [{"id": "C01", "anchor": {"block_id": "B0002", "quote": "The parser is utilized."}}]}), encoding="utf-8")
            args = argparse.Namespace(original=None, proposed=None, candidate_id=None, input=None, repairs=str(repairs), checks=str(checks))
            pairs = df.compare_pairs_from_args(args)
            self.assertEqual(pairs, [
                {"candidate_id": "C01", "original": "The parser is utilized.", "proposed_wording": "We use the parser."},
                {"candidate_id": "C03", "skipped": "no candidate anchor in --checks"},
            ])

    def test_compare_pairs_from_audit_input_and_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_input = Path(tmp) / "C04.input.json"
            audit_input.write_text(json.dumps({"candidate_id": "C04", "original": [{"block_id": "B0001", "quote": "Old."}], "proposed_wording": "New.", "basis": []}), encoding="utf-8")
            args = argparse.Namespace(original="A.", proposed="B.", candidate_id="X", input=str(audit_input), repairs=None, checks=None)
            self.assertEqual(df.compare_pairs_from_args(args), [
                {"candidate_id": "X", "original": "A.", "proposed_wording": "B."},
                {"candidate_id": "C04", "original": "Old.", "proposed_wording": "New."},
            ])

    def test_compare_pairs_requires_an_input(self):
        args = argparse.Namespace(original="A.", proposed=None, candidate_id=None, input=None, repairs=None, checks=None)
        with self.assertRaises(df.DifficultyError):
            df.compare_pairs_from_args(args)
        args = argparse.Namespace(original=None, proposed=None, candidate_id=None, input=None, repairs=None, checks=None)
        with self.assertRaises(df.DifficultyError):
            df.compare_pairs_from_args(args)


class MissingSpacyTests(unittest.TestCase):
    def test_load_nlp_exits_2_with_install_instructions(self):
        saved = sys.modules.get("spacy", "absent")
        sys.modules["spacy"] = None
        try:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                df.load_nlp("auto")
        finally:
            if saved == "absent":
                del sys.modules["spacy"]
            else:
                sys.modules["spacy"] = saved
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("uv venv tools/nlp-env", stderr.getvalue())
        self.assertIn("python -m spacy download en_core_web_sm", stderr.getvalue())

    def test_help_runs_without_spacy(self):
        completed = subprocess.run([sys.executable, "-I", str(SCRIPTS / "difficulty_features.py"), "--help"], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0)
        self.assertIn("nominate", completed.stdout)
        self.assertIn("compare", completed.stdout)

    def test_feature_notes_mark_heuristics(self):
        self.assertEqual([n for n, v in df.FEATURE_NOTES.items() if v["status"] == "heuristic"], ["denominal_verbs", "inanimate_subject_animate_verb"])
        self.assertEqual([n for n, v in df.FEATURE_NOTES.items() if v["needs_reference"]], ["dependency_length", "unattested_bigrams", "trigram_surprisal_bits"])
        self.assertIn("not findings", df.SCOPE_NOTE)


SAMPLE = """# A sample manuscript

We study how readers understand dense prose. The method that we used relies on a parser. The paper we cite argues that long sentences are hard.

The framework leverages a gradient boosting model training pipeline evaluation strategy. The model believes that the data set size matters.

Plain sentences help readers. We measured reading time with an eye tracker. Participants read each text once.
"""
REFERENCE = "Readers understand short sentences. We measured reading time with an eye tracker. Participants read each text once. The method relies on a parser. Long sentences are hard to read. We study how people read.\n"


@unittest.skipUnless(HAS_SPACY, "spaCy with en_core_web_sm is not installed in this Python")
class SpacyIntegrationTests(unittest.TestCase):
    def test_nominate_and_compare_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "sample.md"
            md.write_text(SAMPLE, encoding="utf-8")
            ref = Path(tmp) / "ref.txt"
            ref.write_text(REFERENCE, encoding="utf-8")
            out = Path(tmp) / "nominate.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = df.main(["nominate", "--input", str(md), "--reference", str(ref), "--model", "en_core_web_sm", "--min-words", "5", "--top", "2", "--out", str(out)])
            self.assertEqual(code, 0)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(result["scope"], df.SCOPE_NOTE)
            self.assertEqual(result["model"], "en_core_web_sm")
            self.assertEqual(result["input"]["blocks"], 4)
            self.assertEqual(result["input"]["ranked_blocks"], 3)
            blocks = {b["block_id"]: b for b in result["blocks"]}
            self.assertEqual(blocks["B0002"]["counts"]["orc_with_relativizer"], 1)
            self.assertEqual(blocks["B0002"]["counts"]["orc_without_relativizer"], 1)
            self.assertEqual(blocks["B0002"]["counts"]["inanimate_subject_animate_verb"], 1)
            self.assertEqual(blocks["B0002"]["examples"]["orc_with_relativizer"], ["method that we used"])
            self.assertEqual(blocks["B0003"]["counts"]["stacked_nouns"], 1)
            self.assertEqual(blocks["B0003"]["examples"]["inanimate_subject_animate_verb"], ["model believes"])
            self.assertEqual(blocks["B0003"]["examples"]["denominal_verbs"], ["leverages"])
            self.assertEqual(blocks["B0004"]["counts"]["unattested_bigrams"], 3)
            self.assertEqual(len(result["nominations"]), 2)
            self.assertEqual(result["nominations"][0]["rank"], 1)
            self.assertIn("rank", stdout.getvalue())

            out2 = Path(tmp) / "compare.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = df.main(["compare", "--candidate-id", "C01", "--original", "The framework uses gradient boosting to train the model.", "--proposed", "The framework leverages a gradient boosting model training pipeline.", "--model", "en_core_web_sm", "--out", str(out2)])
            self.assertEqual(code, 0)
            comparison = json.loads(out2.read_text(encoding="utf-8"))["comparisons"][0]
            self.assertEqual(comparison["candidate_id"], "C01")
            self.assertEqual(comparison["features"]["stacked_nouns"], {"original": 0, "proposed": 1, "proposed_has_more": True})
            self.assertEqual(comparison["features"]["denominal_verbs"], {"original": 0, "proposed": 1, "proposed_has_more": True})
            self.assertTrue(comparison["proposed_has_more_difficulty_features"])
            self.assertEqual(comparison["features"]["unattested_bigrams"], {"original": None, "proposed": None, "proposed_has_more": False})


if __name__ == "__main__":
    unittest.main()
