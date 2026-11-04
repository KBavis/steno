"""How a search's words become a full-text query (no database): sentences, stems, typos, and
the organization's abbreviations."""

from steno.graph.query import (
    abbreviations,
    clause,
    is_abbreviation,
    meaningful,
    rank_tiers,
    stem,
    words_of,
)


def test_a_sentence_keeps_only_the_words_that_name_something():
    assert meaningful(words_of("How can I delete a conversation?")) == ["delete", "conversation"]
    assert meaningful(words_of("how do we predict categories")) == ["predict", "categories"]
    # Only stop words: keep them rather than search for nothing
    assert meaningful(words_of("what is this")) == ["what", "is", "this"]


def test_stems_match_other_forms_of_the_word():
    assert stem("categorize") == stem("categories") == "categor"
    assert stem("predictions") == "predict"
    assert stem("sync") == "sync"  # too short to cut
    assert "categor*" in clause("categorize")


def test_long_words_tolerate_a_typo():
    assert "deleet~1" in clause("deleet")
    assert "conversaton~2" in clause("conversaton")
    assert "subsciption~2" in clause("subsciption")
    assert "~" not in clause("sync")


def test_the_organizations_abbreviations_are_found_in_its_names():
    names = ["ent", "clt", "prd", "sub", "pub", "client", "cnt", "orders"]
    assert is_abbreviation("clt", "client") and is_abbreviation("prd", "prod")
    assert is_abbreviation("sub", "subscription")
    # Letters in order are all it takes: `clt` fits cluster too. It ranks below the word
    # itself, and the agent reads `matched`
    assert is_abbreviation("clt", "cluster")
    assert not is_abbreviation("pub", "subscription")  # another first letter
    assert not is_abbreviation("client", "client")  # the word itself isn't an abbreviation
    assert abbreviations("client", names) == ["clt", "cnt"]
    q = clause("client", names)
    assert "clt^2" in q and "client*" in q


def test_when_nothing_matches_every_word_one_is_dropped_at_a_time():
    tiers = list(rank_tiers(["link", "bank", "account", "plaid"]))
    assert tiers[0] == (["link", "bank", "account", "plaid"], [])
    assert (["link", "account", "plaid"], ["bank"]) in tiers
    # Never below half the words
    assert min(len(kept) for kept, _ in tiers) == 2


def test_identifiers_are_split_into_words_for_the_index():
    from steno.graph.query import split_identifiers

    assert split_identifiers("git_commit (insert)") == "git commit"
    assert split_identifiers("deleteConversation, DiffTaskRunner.execute") == (
        "delete conversation diff task runner"
    )
    assert split_identifiers("plain words only") == ""
