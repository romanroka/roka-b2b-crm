"""Offline checks for the three-email follow-up workflow; no API calls or sends."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import gspread

import followups
import letters
import senders
import sheets
from test_senders import EVA, RESPONSE, SenderAppHarness

FIRST = {"subject": "Notre proposition", "body": "Premier email personnalisé, avec une proposition réelle."}
SEQUENCE = [{"subject": f"Sujet {i}", "body": f"Bonjour, texte différent numéro {i}."} for i in range(1, 4)]
FU_RESPONSE = SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(SEQUENCE))])


class FollowupLogicTests(unittest.TestCase):
    def test_parse_requires_exactly_three_complete_emails(self):
        for text in (json.dumps(SEQUENCE), "```json\n" + json.dumps(SEQUENCE) + "\n```"):
            self.assertEqual(followups.parse(text), SEQUENCE)
        for invalid in ("```", "not JSON", "[]", json.dumps(SEQUENCE[:2]), json.dumps(SEQUENCE + SEQUENCE[:1]), '{"drafts": []}', '[{}, {}, {}]', json.dumps([{**SEQUENCE[0], "body": " "}, *SEQUENCE[1:]]), json.dumps([{**SEQUENCE[0], "subject": 123}, *SEQUENCE[1:]])):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                followups.parse(invalid)

    def test_context_identity_changes_with_source_sender_client_and_brand(self):
        original = followups.context_key(senders.draft_key(EVA, 1, "Brand"), FIRST["body"])
        for profile, client, brand, body in ((EVA, 2, "Brand", FIRST["body"]), ({**EVA, "id": "other"}, 1, "Brand", FIRST["body"]), ({**EVA, "name": "Alex"}, 1, "Brand", FIRST["body"]), (EVA, 1, "Other brand", FIRST["body"]), (EVA, 1, "Brand", "New first email")):
            self.assertNotEqual(original, followups.context_key(senders.draft_key(profile, client, brand), body))

    def test_generation_uses_selected_sender_first_email_and_real_history(self):
        api = MagicMock()
        api.messages.create.return_value = FU_RESPONSE
        with patch.object(letters, "_get_anthropic_client", return_value=api):
            result = letters.generate_followups({"company": "Hôtel Test", "status": "Contacté"}, FIRST, sender=EVA, brand_context="Marque commune", history=[{"date": "2026-09-12", "type": "Premier email", "texte": "Historique réel"}])
        self.assertEqual(len(result), 3)
        api.messages.create.assert_called_once()
        request = api.messages.create.call_args.kwargs
        self.assertIn("Marque commune", request["system"])
        self.assertIn("Nom : Eva", request["system"])
        self.assertIn(EVA["context"], request["system"])
        self.assertIn(FIRST["body"], request["messages"][0]["content"])
        self.assertIn("Historique réel", request["messages"][0]["content"])
        self.assertIn("Contacté", request["messages"][0]["content"])
        for draft in result:
            self.assertTrue(draft["body"].endswith(senders.signature(EVA)))
            self.assertNotIn("Roman", draft["body"])

    def test_missing_first_email_does_not_call_ai(self):
        with patch.object(letters, "_get_anthropic_client") as api:
            for invalid in ({}, {"body": " "}, {"body": None}):
                with self.assertRaises(ValueError):
                    letters.generate_followups({}, invalid, sender=EVA)
            api.assert_not_called()

    def test_missing_sheet_is_read_only(self):
        spreadsheet = MagicMock()
        spreadsheet.worksheet.side_effect = gspread.WorksheetNotFound("missing")
        with patch.object(sheets, "_get_spreadsheet", return_value=spreadsheet):
            self.assertEqual(sheets.load_followup_drafts("eva:1"), {})
        spreadsheet.add_worksheet.assert_not_called()

    def test_storage_updates_only_own_row_and_round_trips_unicode(self):
        spreadsheet = MagicMock()
        ws = spreadsheet.worksheet.return_value
        ws.row_values.return_value = sheets.FOLLOWUPS_COLUMNS
        ws.col_values.return_value = ["context_key", "other:1", "eva:1", "other:2"]
        with patch.object(sheets, "_get_spreadsheet", return_value=spreadsheet):
            sheets.save_followup_drafts("eva:1", 1, "eva", FIRST, SEQUENCE)
            call = ws.update.call_args.kwargs
            self.assertEqual(call["range_name"], "A3:F3")
            self.assertEqual(call["value_input_option"], "RAW")
            row = call["values"][0]
            self.assertEqual(row[:3], ["eva:1", 1, "eva"])
            ws.get_all_records.return_value = [dict(zip(sheets.FOLLOWUPS_COLUMNS, row))]
            self.assertEqual(sheets.load_followup_drafts("eva:1"), {"first_letter": FIRST, "drafts": SEQUENCE})
            self.assertEqual(sheets.load_followup_drafts("other:1"), {})
        ws.clear.assert_not_called()
        ws.append_row.assert_not_called()
        self.assertTrue(all(call.args[0] == "FollowUps" for call in spreadsheet.worksheet.call_args_list))

    def test_first_save_creates_only_followups_sheet(self):
        spreadsheet = MagicMock()
        spreadsheet.worksheet.side_effect = gspread.WorksheetNotFound("missing")
        ws = spreadsheet.add_worksheet.return_value
        ws.row_values.return_value = sheets.FOLLOWUPS_COLUMNS
        ws.col_values.return_value = ["context_key"]
        with patch.object(sheets, "_get_spreadsheet", return_value=spreadsheet):
            sheets.save_followup_drafts("eva:1", 1, "eva", FIRST, SEQUENCE)
        spreadsheet.add_worksheet.assert_called_once_with(title="FollowUps", rows=1000, cols=6)
        self.assertEqual(ws.append_row.call_count, 2)
        self.assertEqual(ws.append_row.call_args.kwargs["value_input_option"], "RAW")

    def test_invalid_or_oversized_drafts_are_rejected_before_any_storage_call(self):
        with patch.object(sheets, "_get_spreadsheet") as storage:
            for sequence in (SEQUENCE[:2], [{**SEQUENCE[0], "body": "x" * 46000}, *SEQUENCE[1:]]):
                with self.assertRaises(ValueError):
                    sheets.save_followup_drafts("eva:1", 1, "eva", FIRST, sequence)
            with self.assertRaises(ValueError):
                sheets.save_followup_drafts("eva:1", 1, "eva", {}, SEQUENCE)
            storage.assert_not_called()


class FollowupInterfaceTests(SenderAppHarness):
    def generate_three(self, app):
        self.api.messages.create.return_value = FU_RESPONSE
        self.item(app.tabs[3].button, "✍️ Générer les 3 follow-ups").click().run()
        self.healthy(app)
        return [item for item in app.tabs[3].text_area if item.label.startswith("Texte du follow-up")]

    def test_button_disabled_until_first_email_exists(self):
        app = self.app()
        self.assertTrue(self.item(app.tabs[3].button, "✍️ Générer les 3 follow-ups").disabled)
        self.api.messages.create.assert_not_called()
        self.save_followups.assert_not_called()

    def test_generate_edit_mailto_save_and_restore_in_new_session(self):
        app = self.app()
        app.selectbox(key="active_sender_id").set_value("eva").run()
        self.generate(app).set_value("Premier email modifié pour Eva").run()
        bodies = self.generate_three(app)
        self.assertEqual(len(bodies), 3)
        self.assertTrue(all(body.value.endswith(senders.signature(EVA)) for body in bodies))
        self.assertIn("Premier email modifié pour Eva", self.api.messages.create.call_args.kwargs["messages"][0]["content"])
        bodies[0].set_value("Relance modifiée\n\nEva").run()
        self.item(app.tabs[3].text_input, "Objet du follow-up 1").set_value("Un échange ?").run()
        link = self.item(app.get("link_button"), "📧 Ouvrir le follow-up 1 dans mon client mail")
        parsed = urlparse(link.proto.url)
        self.assertEqual(parsed.path, "hotel1@example.com")
        self.assertEqual(parse_qs(parsed.query), {"subject": ["Un échange ?"], "body": ["Relance modifiée\n\nEva"]})
        self.item(app.tabs[3].button, "💾 Sauvegarder les 3 follow-ups").click().run()
        self.healthy(app)
        saved = self.save_followups.call_args.args
        self.assertEqual(saved[1:3], (1, "eva"))
        self.assertEqual(saved[3]["body"], "Premier email modifié pour Eva")
        self.assertEqual(saved[4][0], {"subject": "Un échange ?", "body": "Relance modifiée\n\nEva"})
        self.update_client.assert_not_called()
        self.log_message.assert_not_called()
        second = self.app()
        self.assertEqual(len(second.tabs[3].text_area), 0)
        second.selectbox(key="active_sender_id").set_value("eva").run()
        self.healthy(second)
        self.assertEqual(self.item(second.tabs[3].text_area, "Texte du follow-up 1").value, "Relance modifiée\n\nEva")
        self.assertTrue(any(text.value == "Premier email modifié pour Eva" for text in second.tabs[3].text))

    def test_switching_user_client_or_first_letter_cannot_mix_sequences(self):
        app = self.app()
        original = self.generate(app).value
        self.generate_three(app)[0].set_value("Retouche de Roman").run()
        app.selectbox(key="active_sender_id").set_value("eva").run()
        self.assertEqual(len(app.tabs[3].text_area), 0)
        self.api.messages.create.return_value = RESPONSE
        self.generate(app)
        self.generate_three(app)
        self.item(app.tabs[3].selectbox, "Client").set_value(2).run()
        self.assertEqual(len(app.tabs[3].text_area), 0)
        self.item(app.tabs[3].selectbox, "Client").set_value(1).run()
        self.assertTrue(self.item(app.tabs[3].text_area, "Texte du follow-up 1").value.endswith(senders.signature(EVA)))
        app.selectbox(key="active_sender_id").set_value("default").run()
        self.assertEqual(self.item(app.tabs[3].text_area, "Texte du follow-up 1").value, "Retouche de Roman")
        self.item(app.tabs[3].text_area, "Texte de la lettre (modifiable)").set_value("Une nouvelle proposition").run()
        self.assertEqual(len(app.tabs[3].text_area), 1)
        self.item(app.tabs[3].text_area, "Texte de la lettre (modifiable)").set_value(original).run()
        self.assertEqual(self.item(app.tabs[3].text_area, "Texte du follow-up 1").value, "Retouche de Roman")

    def test_already_contacted_client_uses_real_first_email_and_history(self):
        self.clients.loc[0, "status"] = "Contacté"
        self.clients.loc[0, "letter_text"] = "Ancien brouillon"
        self.messages.loc[0] = ["2026-09-12", 1, "Hôtel 1", "Premier email — Roman", "Email réellement envoyé"]
        self.messages.loc[1] = ["2026-09-13", 2, "Hôtel 2", "Premier email", "Autre client, ne pas inclure"]
        app = self.app()
        self.assertFalse(self.item(app.tabs[3].button, "✍️ Générer les 3 follow-ups").disabled)
        self.generate_three(app)
        prompt = self.api.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Email réellement envoyé", prompt)
        self.assertNotIn("Ancien brouillon", prompt)
        self.assertNotIn("Autre client, ne pas inclure", prompt)
        self.update_client.assert_not_called()
        self.log_message.assert_not_called()

    def test_failed_regeneration_keeps_edited_sequence(self):
        app = self.app()
        self.generate(app)
        self.generate_three(app)[1].set_value("Conserver cette retouche").run()
        for response in (RuntimeError("API indisponible"), SimpleNamespace(content=[SimpleNamespace(type="text", text='[{"subject": "Incomplete"}]')])):
            self.api.messages.create.side_effect = response if isinstance(response, Exception) else None
            if not isinstance(response, Exception):
                self.api.messages.create.return_value = response
            self.item(app.tabs[3].button, "✍️ Générer les 3 follow-ups").click().run()
            self.healthy(app)
            self.assertTrue(any("Erreur lors de la génération des follow-ups" in error.value for error in app.error))
            self.assertEqual(self.item(app.tabs[3].text_area, "Texte du follow-up 2").value, "Conserver cette retouche")
        self.save_followups.assert_not_called()

    def test_storage_errors_are_visible_without_marking_anything_sent(self):
        self.load_followups.side_effect = RuntimeError("lecture indisponible")
        app = self.app()
        self.assertTrue(any("Impossible de charger les follow-ups" in warning.value for warning in app.warning))
        self.generate(app)
        self.generate_three(app)
        self.save_followups.side_effect = RuntimeError("sauvegarde indisponible")
        self.item(app.tabs[3].button, "💾 Sauvegarder les 3 follow-ups").click().run()
        self.healthy(app)
        self.assertTrue(any("Impossible de sauvegarder les follow-ups" in error.value for error in app.error))
        self.update_client.assert_not_called()
        self.log_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
