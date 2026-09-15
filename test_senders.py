"""Offline regression checks for sender identity, profile storage and drafts."""
import copy
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import gspread
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import config
import letters
import senders
import sheets

EVA = {"id": "eva", "name": "Eva", "role": "Responsable commerciale", "email": "eva@example.com", "context": "Je développe les partenariats avec les hôtels."}
RESPONSE = SimpleNamespace(content=[SimpleNamespace(type="text", text="SUJET: Proposition\n---\nBonjour, voici notre proposition.")])


class SenderLogicTests(unittest.TestCase):
    def test_legacy_signature_and_blank_fields(self):
        legacy = senders.legacy_sender({"sender_name": "Ancien auteur", "sender_role": "", "sender_email": ""})
        self.assertEqual(senders.signature(legacy), "Ancien auteur")
        self.assertEqual(senders.signature({"name": "Eva", "role": "", "email": ""}), "Eva")

    def test_validation(self):
        self.assertEqual(senders.validate_sender({**EVA, "name": " Eva "})["name"], "Eva")
        for invalid in ({**EVA, "name": " "}, {**EVA, "email": "bad"}, {**EVA, "email": "eva@example.com\nBcc: other@example.com"}):
            with self.assertRaises(ValueError):
                senders.validate_sender(invalid)

    def test_sender_and_context_reach_generation_without_global_mutation(self):
        original = (config.SENDER_NAME, config.SENDER_ROLE, config.SENDER_EMAIL)
        api = MagicMock()
        api.messages.create.return_value = RESPONSE
        with patch.object(letters, "_get_anthropic_client", return_value=api):
            for generate in (letters.generate_first_letter, letters.generate_relance):
                for profile in (EVA, {**EVA, "name": "Alex", "role": "", "email": "", "context": "Un autre rôle."}):
                    result = generate({"company": "Hôtel Test"}, sender=profile, brand_context="Marque commune")
                    request = api.messages.create.call_args.kwargs
                    self.assertIn("Nom : " + profile["name"], request["system"])
                    self.assertIn(profile["context"], request["system"])
                    self.assertIn("Marque commune", request["system"])
                    self.assertIn("Nom : " + profile["name"], request["messages"][0]["content"])
                    self.assertTrue(result["body"].endswith(senders.signature(profile)))
                    self.assertNotIn("Roman", result["body"])
        self.assertEqual((config.SENDER_NAME, config.SENDER_ROLE, config.SENDER_EMAIL), original)

    def test_drafts_differ_for_client_user_and_profile_changes(self):
        original = senders.draft_key(EVA, 1, "Brand")
        for profile, client_id, brand in ((EVA, 2, "Brand"), ({**EVA, "id": "other"}, 1, "Brand"), ({**EVA, "name": "New name"}, 1, "Brand"), ({**EVA, "context": "New role"}, 1, "Brand"), (EVA, 1, "New brand")):
            self.assertNotEqual(original, senders.draft_key(profile, client_id, brand))

    def test_missing_profile_sheet_is_read_only(self):
        spreadsheet = MagicMock()
        spreadsheet.worksheet.side_effect = gspread.WorksheetNotFound("missing")
        with patch.object(sheets, "_get_spreadsheet", return_value=spreadsheet):
            self.assertEqual(sheets.load_sender_profiles(), [])
        spreadsheet.add_worksheet.assert_not_called()

    def test_profile_update_targets_one_row_as_raw_values(self):
        spreadsheet = MagicMock()
        ws = spreadsheet.worksheet.return_value
        ws.row_values.return_value = sheets.SENDERS_COLUMNS
        ws.col_values.return_value = ["id", "default", "eva", "someone-else"]
        with patch.object(sheets, "_get_spreadsheet", return_value=spreadsheet):
            sheets.save_sender_profile(EVA)
        ws.update.assert_called_once_with(range_name="A3:E3", values=[[EVA[key] for key in sheets.SENDERS_COLUMNS]], value_input_option="RAW")
        ws.clear.assert_not_called()
        ws.append_row.assert_not_called()

    def test_first_save_creates_profile_sheet_only(self):
        spreadsheet = MagicMock()
        spreadsheet.worksheet.side_effect = gspread.WorksheetNotFound("missing")
        ws = spreadsheet.add_worksheet.return_value
        ws.row_values.return_value = sheets.SENDERS_COLUMNS
        ws.col_values.return_value = ["id"]
        with patch.object(sheets, "_get_spreadsheet", return_value=spreadsheet):
            sheets.save_sender_profile(EVA)
        spreadsheet.add_worksheet.assert_called_once_with(title="Utilisateurs", rows=100, cols=5)
        self.assertEqual(ws.append_row.call_count, 2)
        ws.append_row.assert_called_with([EVA[key] for key in sheets.SENDERS_COLUMNS], value_input_option="RAW")


class SenderInterfaceTests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.profiles = [copy.deepcopy(EVA)]
        self.brand = {"sender_name": "Roman", "sender_role": "Fondateur", "sender_email": "roman@example.com"}
        clients = []
        for identifier in (1, 2):
            row = dict.fromkeys(config.CLIENT_COLUMNS, "")
            row.update(id=identifier, company=f"Hôtel {identifier}", email=f"hotel{identifier}@example.com", status="Nouveau", relance_count=0)
            clients.append(row)
        values = {
            "load_brand_settings": self.brand,
            "load_clients_df": pd.DataFrame(clients, columns=config.CLIENT_COLUMNS),
            "load_images_df": pd.DataFrame(columns=sheets.IMAGES_COLUMNS),
            "load_search_log_df": pd.DataFrame(columns=sheets.SEARCH_LOG_COLUMNS),
            "load_messages_df": pd.DataFrame(columns=sheets.MESSAGES_COLUMNS),
            "load_email_threads_df": pd.DataFrame(columns=sheets.EMAIL_THREADS_COLUMNS),
            "load_all_email_messages_df": pd.DataFrame(columns=sheets.EMAIL_MESSAGES_COLUMNS),
        }
        for name, value in values.items():
            self.stack.enter_context(patch.object(sheets, name, return_value=value))
        self.load_profiles = self.stack.enter_context(patch.object(sheets, "load_sender_profiles", side_effect=lambda: copy.deepcopy(self.profiles)))
        self.save_profile = self.stack.enter_context(patch.object(sheets, "save_sender_profile", side_effect=self.store_profile))
        self.save_brand = self.stack.enter_context(patch.object(sheets, "save_brand_settings"))
        self.update_client = self.stack.enter_context(patch.object(sheets, "update_client"))
        self.log_message = self.stack.enter_context(patch.object(sheets, "log_message"))
        self.storage = self.stack.enter_context(patch.object(sheets, "_get_spreadsheet", side_effect=AssertionError("No live storage calls")))
        self.api = MagicMock()
        self.api.messages.create.return_value = RESPONSE
        self.stack.enter_context(patch.object(letters, "_get_anthropic_client", return_value=self.api))

    def tearDown(self):
        self.storage.assert_not_called()

    def store_profile(self, profile):
        self.profiles[:] = [row for row in self.profiles if row["id"] != profile["id"]] + [copy.deepcopy(profile)]

    def app(self):
        return self.healthy(AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=15).run())

    def healthy(self, app):
        self.assertFalse(app.exception, [exc.message for exc in app.exception])
        return app

    def item(self, elements, label):
        return next(item for item in elements if item.label == label)

    def generate(self, app):
        self.item(app.tabs[3].button, "✍️ Générer la lettre (objet + texte)").click().run()
        self.healthy(app)
        return self.item(app.tabs[3].text_area, "Texte de la lettre (modifiable)")

    def test_switching_users_and_clients_preserves_separate_edited_drafts(self):
        app = self.app()
        roman = self.generate(app)
        self.assertTrue(roman.value.endswith("Roman\nFondateur\nroman@example.com"))
        roman.set_value("Texte modifié par Roman").run()
        app.selectbox(key="active_sender_id").set_value("eva").run()
        self.healthy(app)
        self.assertEqual(len(app.tabs[3].text_area), 0)
        eva = self.generate(app)
        self.assertTrue(eva.value.endswith(senders.signature(EVA)))
        self.assertIn(EVA["context"], self.api.messages.create.call_args.kwargs["system"])
        self.item(app.tabs[3].selectbox, "Client").set_value(2).run()
        self.assertEqual(len(app.tabs[3].text_area), 0)
        self.item(app.tabs[3].selectbox, "Client").set_value(1).run()
        self.assertTrue(self.item(app.tabs[3].text_area, "Texte de la lettre (modifiable)").value.endswith(senders.signature(EVA)))
        app.selectbox(key="active_sender_id").set_value("default").run()
        self.assertEqual(self.item(app.tabs[3].text_area, "Texte de la lettre (modifiable)").value, "Texte modifié par Roman")
        second = self.app()
        self.assertEqual(second.selectbox(key="active_sender_id").value, "default")
        self.assertEqual(len(second.tabs[3].text_area), 0)

    def test_add_edit_profile_and_send_with_selected_identity(self):
        app = self.app()
        app.selectbox(key="edit_sender_id").set_value("__new__").run()
        self.item(app.text_input, "Nom de l'utilisateur *").set_value("Camille")
        self.item(app.text_input, "Fonction / rôle").set_value("Partenariats")
        self.item(app.text_input, "Email de signature").set_value("camille@example.com")
        self.item(app.text_area, "Contexte personnel pour les lettres").set_value("Je représente la marque auprès des hôtels.")
        self.item(app.button, "💾 Enregistrer l'utilisateur").click().run()
        self.healthy(app)
        identifier = self.save_profile.call_args.args[0]["id"]
        self.assertEqual(app.selectbox(key="active_sender_id").value, identifier)
        self.assertEqual(app.selectbox(key="edit_sender_id").value, identifier)
        self.assertTrue(self.generate(app).value.endswith("Camille\nPartenariats\ncamille@example.com"))
        self.item(app.text_input, "Nom de l'utilisateur *").set_value("Camille Martin")
        self.item(app.text_input, "Fonction / rôle").set_value("")
        self.item(app.text_input, "Email de signature").set_value("")
        self.item(app.button, "💾 Enregistrer l'utilisateur").click().run()
        self.healthy(app)
        self.assertEqual(self.save_profile.call_args.args[0]["id"], identifier)
        self.assertEqual(len(app.tabs[3].text_area), 0)
        self.assertTrue(self.generate(app).value.endswith("\n\nCamille Martin"))
        self.item(app.tabs[3].button, "💾 Sauvegarder le brouillon").click().run()
        self.assertTrue(self.update_client.call_args.args[1]["letter_text"].endswith("Camille Martin"))
        self.item(app.tabs[3].button, "✅ Marquer comme envoyé (déjà envoyé moi-même)").click().run()
        self.healthy(app)
        self.assertEqual(self.log_message.call_args.args[2], "Premier email — Camille Martin")
        self.assertEqual(self.update_client.call_args.args[1]["status"], "Contacté")

    def test_profile_save_validation_and_brand_settings_preservation(self):
        app = self.app()
        app.selectbox(key="edit_sender_id").set_value("__new__").run()
        self.item(app.button, "💾 Enregistrer l'utilisateur").click().run()
        self.healthy(app)
        self.save_profile.assert_not_called()
        self.item(app.button, "💾 Enregistrer").click().run()
        self.healthy(app)
        self.assertEqual(self.save_brand.call_args.args[0]["sender_email"], "roman@example.com")

    def test_loading_error_does_not_silently_fall_back_to_another_user(self):
        self.load_profiles.side_effect = RuntimeError("temporary failure")
        app = self.app()
        self.assertTrue(any("Impossible de charger les utilisateurs" in error.value for error in app.error))
        self.assertEqual(len(app.tabs), 0)
        self.api.messages.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
