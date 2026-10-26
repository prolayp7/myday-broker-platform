"""Splitting a driver's name into the parts Meta matches on.

EVERY CASE ASSERTS ALL THREE FIELDS. Checking only first and last would let a
wrong middle_name pass every test in this file, and keeping the middle OUT of
first_name is the entire reason that column exists.

The expectations below are not invented: they are the answers Claude returned
during validation, checked against the real leads. One of them corrected me —
SANTHOSH PAUL N is first SANTHOSH / last PAUL, and the lead's own email is
paulsantho@yahoo.com.
"""

import claude_client
import meta_capi


def _no_transport(monkeypatch):
    """Make any call to Claude fail the test.

    Used to prove a shape is resolved LOCALLY. A test that merely asserts the
    right answer would still pass if the answer came from the network, which
    is the thing being ruled out.
    """

    def _boom(*_a, **_k):
        raise AssertionError("Claude was called for a name a rule settles")

    monkeypatch.setattr(claude_client, "_split_with_claude", _boom)


class TestTheCommaSettlesIt:
    """DASH prints the surname first, and a comma says so outright. No
    judgement needed, so no network call."""

    def test_plain_comma(self, monkeypatch):
        _no_transport(monkeypatch)
        assert claude_client.split_name("CHEEMA,SAJIDA") == ("SAJIDA", "", "CHEEMA")

    def test_space_after_the_comma(self, monkeypatch):
        """Some reports print one, some do not — 'JAYAN, ROONA' is real."""
        _no_transport(monkeypatch)
        assert claude_client.split_name("JAYAN, ROONA") == ("ROONA", "", "JAYAN")

    def test_middle_initial_after_the_given_name(self, monkeypatch):
        _no_transport(monkeypatch)
        assert claude_client.split_name("SAMPLE,TESTCASE A") == (
            "TESTCASE",
            "A",
            "SAMPLE",
        )

    def test_a_full_middle_name_stays_out_of_first(self, monkeypatch):
        """THE CASE middle_name EXISTS FOR. Fused, this hashes to
        'johnmichael' and matches nobody; split, 'john' matches."""
        _no_transport(monkeypatch)
        assert claude_client.split_name("SMITH,JOHN MICHAEL") == (
            "JOHN",
            "MICHAEL",
            "SMITH",
        )

    def test_mixed_case_comma(self, monkeypatch):
        """The same report prints both 'SAMPLE,TESTCASE A' and
        'Otherperson,Second' — the comma rule is case-blind."""
        _no_transport(monkeypatch)
        assert claude_client.split_name("Otherperson,Second") == (
            "Second",
            "",
            "Otherperson",
        )


class TestShapesWithNothingToDecide:
    def test_one_word_is_a_given_name(self, monkeypatch):
        """No surname is invented. Meta scores a present-but-wrong field worse
        than an absent one, and this leaves the lead incomplete until the
        broker supplies the rest — which is the intended outcome."""
        _no_transport(monkeypatch)
        assert claude_client.split_name("Syed") == ("Syed", "", "")

    def test_redacted_and_non_names(self, monkeypatch):
        _no_transport(monkeypatch)
        for value in ("******", "OTHER", "N/A", "unknown"):
            assert claude_client.split_name(value) == ("", "", ""), value

    def test_empty_and_whitespace(self, monkeypatch):
        _no_transport(monkeypatch)
        for value in (None, "", "   "):
            assert claude_client.split_name(value) == ("", "", ""), repr(value)


class TestClaudeIsAskedOnlyWhenItMatters:
    def test_an_ambiguous_name_reaches_claude(self, monkeypatch):
        """Two ALL-CAPS words, no comma. Nothing in the string says which is
        the surname — this is the case the whole module exists for."""
        seen = []
        monkeypatch.setattr(
            claude_client,
            "_split_with_claude",
            lambda name: seen.append(name) or ("KARAN", "", "SHARMA"),
        )
        assert claude_client.split_name("SHARMA KARAN") == ("KARAN", "", "SHARMA")
        assert seen == ["SHARMA KARAN"]

    def test_asked_once_per_name(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            claude_client,
            "_split_with_claude",
            lambda name: calls.append(name) or ("A", "", "B"),
        )
        claude_client.split_name("SOMEONE ELSE")
        assert len(calls) == 1


class TestWhenClaudeCannotAnswer:
    """THE CRITICAL BEHAVIOUR. An unresolved name is left BLANK, never guessed.

    The old last-space splitter gets two of the three real ambiguous names
    backwards:

        SHARMA KARAN    -> 'SHARMA' / 'KARAN'     (truth: KARAN / SHARMA)
        RAJI MOL CHACKO -> 'RAJI MOL' / 'CHACKO'  (truth: CHACKO / RAJI MOL)

    and stores the result confidently. A filled box gives the broker no reason
    to look; a blank one does. Because a Lead needs first and last before it
    fires, an unresolved name holds the conversion rather than sending Meta a
    wrong identity — which is the failure this whole change exists to prevent.
    """

    def test_no_key_leaves_an_ambiguous_name_blank(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert claude_client.split_name("SHARMA KARAN") == ("", "", "")
        assert claude_client.split_name("RAJI MOL CHACKO") == ("", "", "")

    def test_no_key_still_resolves_the_shapes_a_rule_settles(self, monkeypatch):
        """An outage costs the broker one manual entry on the ambiguous names,
        not every name."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert claude_client.split_name("CHEEMA,SAJIDA") == ("SAJIDA", "", "CHEEMA")
        assert claude_client.split_name("Syed") == ("Syed", "", "")
        assert claude_client.split_name("******") == ("", "", "")

    def test_a_network_failure_leaves_it_blank(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        class _Boom:
            class messages:
                @staticmethod
                def create(**_):
                    raise TimeoutError("slow")

        monkeypatch.setattr(claude_client, "_client", lambda: _Boom())
        assert claude_client.split_name("SHARMA KARAN") == ("", "", "")

    def test_no_text_block_leaves_it_blank(self, monkeypatch):
        """Observed for real: thinking is on by default and shares max_tokens,
        so a tight budget returns thinking and NO answer — stop_reason
        max_tokens, not an exception. A silent empty reply must not become a
        silent empty name."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        class _Block:
            type = "thinking"

        class _Response:
            content = [_Block()]
            stop_reason = "max_tokens"

        class _Client:
            class messages:
                @staticmethod
                def create(**_):
                    return _Response()

        monkeypatch.setattr(claude_client, "_client", lambda: _Client())
        assert claude_client.split_name("SHARMA KARAN") == ("", "", "")

    def test_a_malformed_reply_leaves_it_blank(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        class _Block:
            type = "text"
            text = "sorry, I cannot do that"

        class _Response:
            content = [_Block()]
            stop_reason = "end_turn"

        class _Client:
            class messages:
                @staticmethod
                def create(**_):
                    return _Response()

        monkeypatch.setattr(claude_client, "_client", lambda: _Client())
        assert claude_client.split_name("SHARMA KARAN") == ("", "", "")

    def test_the_key_never_reaches_a_log_line(self, monkeypatch, caplog):
        """Anthropic errors can quote request detail back. Only the exception
        TYPE is logged — the same rule meta_capi._redact_token exists for."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")

        class _Client:
            class messages:
                @staticmethod
                def create(**_):
                    raise RuntimeError("failed with key sk-ant-secret-value")

        monkeypatch.setattr(claude_client, "_client", lambda: _Client())
        with caplog.at_level("WARNING"):
            claude_client.split_name("SHARMA KARAN")
        assert "sk-ant-secret-value" not in caplog.text


class TestTheSenderPrefersTheConfirmedColumns:
    LEAD = {"id": "abc", "email": "a@b.com", "name": "Jane Doe"}

    def test_columns_win_over_the_combined_name(self):
        """The point of the whole exercise. driver.name is DASH's printed
        form, which split_name mangles; the columns are the confirmed split."""
        ud = meta_capi.build_user_data(
            lead=self.LEAD,
            driver={
                "name": "CHEEMA,SAJIDA",
                "first_name": "SAJIDA",
                "last_name": "CHEEMA",
            },
        )
        assert ud["fn"] == meta_capi.hash_name_part("SAJIDA")
        assert ud["ln"] == meta_capi.hash_name_part("CHEEMA")

    def test_falls_back_to_split_name_for_rows_without_the_columns(self):
        """Rows that predate 0094. Not a good split — it is why the columns
        exist — but better than sending no name at all."""
        ud = meta_capi.build_user_data(
            lead=self.LEAD, driver={"name": "Priya Raghunathan"}
        )
        assert ud["fn"] == meta_capi.hash_name_part("Priya")
        assert ud["ln"] == meta_capi.hash_name_part("Raghunathan")

    def test_middle_name_is_never_sent(self):
        """Meta has no parameter for it. The column's only job is keeping a
        middle name out of fn."""
        ud = meta_capi.build_user_data(
            lead=self.LEAD,
            driver={"first_name": "JOHN", "middle_name": "MICHAEL", "last_name": "SMITH"},
        )
        assert ud["fn"] == meta_capi.hash_name_part("JOHN")
        assert "middle_name" not in ud
        assert ud["fn"] != meta_capi.hash_name_part("JOHN MICHAEL")

    def test_a_first_name_with_no_surname_still_sends_fn(self):
        """'Syed' has no surname to send. fn alone is worth more than
        nothing, and absent beats present-but-wrong on ln."""
        ud = meta_capi.build_user_data(
            lead=self.LEAD, driver={"name": "Syed", "first_name": "Syed"}
        )
        assert ud["fn"] == meta_capi.hash_name_part("Syed")
        assert "ln" not in ud

    def test_blank_columns_do_not_suppress_the_fallback(self):
        """An unresolved name leaves the columns empty strings, not null. That
        must not read as 'confirmed to be blank' and block the fallback."""
        ud = meta_capi.build_user_data(
            lead=self.LEAD,
            driver={"name": "Priya Raghunathan", "first_name": "", "last_name": ""},
        )
        assert ud["fn"] == meta_capi.hash_name_part("Priya")


class TestTheApplicantIsSeededSplitAtCreation:
    """ONE RULE EVERYWHERE: a name is split wherever one is set.

    The applicant row is created the first time a broker opens a lead, seeded
    from the lead form. Before this, only the DASH path split a name — so a
    lead with no report yet had blank name parts, could never satisfy the
    applicant-complete check, and could never fire a Lead. The lead's source
    decides whether the result is SENT to Meta; it does not decide whether the
    name is split.
    """

    def _wire(self, monkeypatch, lead_name, split):
        import services.driver as driver_service

        captured = {}

        class _R:
            def __init__(self, data):
                self.data = data

        monkeypatch.setattr(driver_service.repo, "get_principal", lambda _l: _R([]))
        monkeypatch.setattr(
            driver_service.lead_repo, "get_by_id",
            lambda _l: _R([{"id": "l1", "name": lead_name,
                            "phone": "+15550000000", "email": "a@b.com"}]),
        )
        monkeypatch.setattr(
            driver_service.claude_client, "split_name", lambda _n: split
        )
        monkeypatch.setattr(
            driver_service.repo, "insert_principal",
            lambda lead_id, **kw: captured.update(kw) or _R([{"id": "d1", **kw}]),
        )
        return driver_service, captured

    def test_the_seeded_name_is_split(self, monkeypatch):
        svc, captured = self._wire(monkeypatch, "Eldho Kurian", ("Eldho", "", "Kurian"))
        svc.get_or_create_principal("l1")
        assert captured["first_name"] == "Eldho"
        assert captured["last_name"] == "Kurian"
        assert captured["name"] == "Eldho Kurian", "the printed form is kept too"

    def test_an_unresolved_name_seeds_nulls_not_blanks(self, monkeypatch):
        """Blank strings would read as "confirmed to have no surname" and
        satisfy a `not null` check. Null is the honest "not split yet"."""
        svc, captured = self._wire(monkeypatch, "SHARMA KARAN", ("", "", ""))
        svc.get_or_create_principal("l1")
        assert captured["first_name"] is None
        assert captured["last_name"] is None

    def test_a_single_word_name_seeds_no_surname(self, monkeypatch):
        """'Garima' gets a first name and nothing else. The lead stays
        incomplete until the broker adds a surname — intended, not a bug."""
        svc, captured = self._wire(monkeypatch, "Garima", ("Garima", "", ""))
        svc.get_or_create_principal("l1")
        assert captured["first_name"] == "Garima"
        assert captured["last_name"] is None
