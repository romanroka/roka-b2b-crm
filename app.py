# -*- coding: utf-8 -*-
"""
ROKA B2B CRM — MVP Streamlit.

Запуск: streamlit run app.py
Инструкция по настройке — в README.md.
"""

import base64
import io
from datetime import datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

import pandas as pd
import streamlit as st
from PIL import Image

import config
import letters
import prospecting
import senders
import sheets

# Taille max d'une image encodée en base64 stockée dans une cellule Google
# Sheets (limite réelle ~50 000 caractères par cellule) — on vise large en
# dessous pour garder de la marge.
IMAGE_MAX_BASE64_CHARS = 42000

# ---------------------------------------------------------------------------
# Cache — Streamlit ré-exécute TOUT le script à chaque clic, où que ce soit
# dans l'appli (y compris le contenu des onglets non visibles à l'écran).
# Sans cache, ça veut dire un appel Google Sheets par fonction ci-dessous À
# CHAQUE clic, ce qui épuise vite le quota (429 "Quota exceeded"). Chaque
# lecture est donc mise en cache 30s ; refresh() les vide toutes d'un coup
# après une écriture, pour que l'affichage reparte à jour immédiatement.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner=False)
def load_brand_settings_cached() -> dict:
    return sheets.load_brand_settings()


@st.cache_data(ttl=30, show_spinner=False)
def load_sender_profiles_cached() -> list:
    return sheets.load_sender_profiles()


@st.cache_data(ttl=30, show_spinner=False)
def load_images_df_cached() -> pd.DataFrame:
    return sheets.load_images_df()


@st.cache_data(ttl=30, show_spinner=False)
def load_email_threads_df_cached() -> pd.DataFrame:
    return sheets.load_email_threads_df()


@st.cache_data(ttl=30, show_spinner=False)
def load_search_log_df_cached() -> pd.DataFrame:
    return sheets.load_search_log_df()


@st.cache_data(ttl=30, show_spinner=False)
def load_messages_df_cached() -> pd.DataFrame:
    return sheets.load_messages_df()


@st.cache_data(ttl=30, show_spinner=False)
def load_all_email_messages_df_cached() -> pd.DataFrame:
    return sheets.load_all_email_messages_df()


def messages_for_thread_cached(thread_id) -> pd.DataFrame:
    """Filtre la feuille EmailMessages (chargée une seule fois, en cache) au
    lieu de la relire entièrement à chaque thread affiché — évite d'épuiser
    le quota Google Sheets quand un client a plusieurs conversations."""
    all_msgs = load_all_email_messages_df_cached()
    if all_msgs.empty:
        return all_msgs
    return all_msgs[all_msgs["thread_id"] == str(thread_id)].sort_values("date")


# ---------------------------------------------------------------------------
# Infos entreprise/marque — remplies dans l'onglet "⚙️ Paramètres" et stockées
# dans l'onglet "Config" de la Google Sheet (pas besoin de toucher au code ni
# aux Secrets). Tant que rien n'est encore configuré, on garde les valeurs
# par défaut de config.py (ROKA).
# ---------------------------------------------------------------------------
try:
    brand_settings = load_brand_settings_cached()
except Exception:
    brand_settings = {}


def _apply_setting(attr: str, key: str, cast=str) -> None:
    val = brand_settings.get(key)
    if val not in (None, ""):
        try:
            setattr(config, attr, cast(val))
        except (TypeError, ValueError):
            pass


_apply_setting("APP_TITLE", "app_title")
_apply_setting("APP_ICON", "app_icon")
_apply_setting("RELANCE_DELAY_DAYS", "relance_delay_days", int)
_apply_setting("SAMPLE_RELANCE_DELAY_DAYS", "sample_relance_delay_days", int)

if brand_settings.get("activity_description") or brand_settings.get("product_description"):
    _ctx_parts = []
    if brand_settings.get("company_name"):
        _line = brand_settings["company_name"]
        if brand_settings.get("tagline"):
            _line += f" — {brand_settings['tagline']}"
        _ctx_parts.append(_line)
    if brand_settings.get("activity_description"):
        _ctx_parts.append(brand_settings["activity_description"])
    if brand_settings.get("product_description"):
        _ctx_parts.append(f"Produit / service vendu : {brand_settings['product_description']}")
    if brand_settings.get("target_audience"):
        _ctx_parts.append(f"Client cible : {brand_settings['target_audience']}")
    if brand_settings.get("tone_preferences"):
        _ctx_parts.append(f"Ton souhaité pour les emails : {brand_settings['tone_preferences']}")
    config.BRAND_CONTEXT = "\n".join(_ctx_parts)

# Keep the generation context local to this script run, like the selected sender.
brand_context = config.BRAND_CONTEXT

st.set_page_config(page_title=config.APP_TITLE, page_icon=config.APP_ICON, layout="wide")

# ---------------------------------------------------------------------------
# Design minimaliste — optionnel, activable dans l'onglet "⚙️ Paramètres".
# Ne touche à aucune donnée ni logique : juste un peu de CSS pour un rendu
# plus épuré (espacements, boutons, typographie). Désactivé par défaut pour
# ne rien changer sans que ce soit demandé.
# ---------------------------------------------------------------------------
MINIMAL_DESIGN_ENABLED = str(brand_settings.get("minimal_design", "")).strip().lower() in (
    "1", "true", "oui", "yes",
)

MINIMAL_DESIGN_CSS = """
<style>
html, body, [class*="css"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
#MainMenu { visibility: hidden; }
footer { visibility: hidden; }
.block-container {
    padding-top: 2.5rem;
    padding-bottom: 3rem;
    max-width: 1100px;
}
h1, h2, h3 {
    font-weight: 600 !important;
    letter-spacing: -0.01em;
}
div.stButton > button, .stDownloadButton > button, .stLinkButton > a {
    border-radius: 6px !important;
    font-weight: 500 !important;
    box-shadow: none !important;
}
div[data-testid="stForm"] {
    border: 1px solid rgba(128, 128, 128, 0.2);
    border-radius: 10px;
    padding: 1.2rem 1.2rem 0.4rem 1.2rem;
}
[data-testid="stMetricValue"] {
    font-weight: 600 !important;
}
.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
}
.stTabs [data-baseweb="tab"] {
    padding: 8px 18px;
}
hr {
    margin: 1.6rem 0;
    opacity: 0.15;
}
[data-testid="stExpander"] {
    border: 1px solid rgba(128, 128, 128, 0.15);
    border-radius: 8px;
}
</style>
"""

if MINIMAL_DESIGN_ENABLED:
    st.markdown(MINIMAL_DESIGN_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Данные — с кэшем, чтобы не дёргать Google Sheets на каждый клик
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner="Chargement depuis Google Sheets…")
def load_data() -> pd.DataFrame:
    return sheets.load_clients_df()


def refresh():
    load_data.clear()
    load_brand_settings_cached.clear()
    load_sender_profiles_cached.clear()
    load_images_df_cached.clear()
    load_email_threads_df_cached.clear()
    load_search_log_df_cached.clear()
    load_messages_df_cached.clear()
    load_all_email_messages_df_cached.clear()


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def add_days(date_str: str, days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def remember_draft_edit(draft_id: str, field: str, widget_key: str) -> None:
    st.session_state["letter_drafts"][draft_id][field] = st.session_state[widget_key]


st.title(f"{config.APP_ICON} {config.APP_TITLE}")

try:
    df = load_data()
except Exception as e:
    st.error(
        f"Impossible de charger Google Sheets : {e}\n\n"
        "Vérifie : le fichier credentials.json (en local) ou le secret "
        "gcp_service_account (sur Streamlit Cloud), le nom exact de "
        "GOOGLE_SHEET_NAME, et que la Google Sheet est bien partagée avec "
        "l'email du service account (voir README / DEPLOY.md)."
    )
    st.stop()

try:
    sender_profiles = load_sender_profiles_cached()
except Exception as e:
    st.error(f"Impossible de charger les utilisateurs : {e}. Réessaie en actualisant la page.")
    st.stop()

if not any(profile["id"] == "default" for profile in sender_profiles):
    sender_profiles = [senders.legacy_sender(brand_settings), *sender_profiles]
sender_by_id = {profile["id"]: profile for profile in sender_profiles}
pending_sender_id = st.session_state.pop("pending_sender_id", None)
if pending_sender_id in sender_by_id:
    st.session_state["active_sender_id"] = pending_sender_id
    st.session_state["edit_sender_id"] = pending_sender_id
if st.session_state.get("active_sender_id") not in sender_by_id:
    st.session_state["active_sender_id"] = sender_profiles[0]["id"]

active_sender_id = st.selectbox(
    "👤 Utilisateur actif",
    list(sender_by_id),
    format_func=lambda identifier: " — ".join(
        value for value in (sender_by_id[identifier]["name"], sender_by_id[identifier]["email"]) if value
    ),
    key="active_sender_id",
    help="Les lettres utilisent le nom, la fonction et le contexte de cet utilisateur. Gère les profils dans Paramètres.",
)
active_sender = sender_by_id[active_sender_id]

tab_settings, tab_clients, tab_prospecting, tab_letters = st.tabs(
    ["⚙️ Paramètres", "📋 Clients", "🔎 Prospection", "✉️ Lettres"]
)

# ---------------------------------------------------------------------------
# TAB: Paramètres (infos entreprise/marque — utilisées pour personnaliser
# les emails générés par l'IA, sans jamais toucher au code)
# ---------------------------------------------------------------------------
with tab_settings:
    st.subheader("👤 Utilisateurs et signatures")
    st.caption("Ajoute les personnes qui écrivent aux clients, puis sélectionne l'utilisateur actif en haut de la page.")
    if st.session_state.get("edit_sender_id") not in [*sender_by_id, "__new__"]:
        st.session_state["edit_sender_id"] = active_sender_id
    edit_sender_id = st.selectbox(
        "Profil à modifier",
        [*sender_by_id, "__new__"],
        format_func=lambda identifier: "➕ Ajouter un utilisateur" if identifier == "__new__" else sender_by_id[identifier]["name"],
        key="edit_sender_id",
    )
    profile_to_edit = sender_by_id.get(edit_sender_id, {})
    with st.form(f"sender_profile_form_{edit_sender_id}"):
        c1, c2, c3 = st.columns(3)
        with c1:
            profile_name = st.text_input("Nom de l'utilisateur *", value=profile_to_edit.get("name", ""))
        with c2:
            profile_role = st.text_input("Fonction / rôle", value=profile_to_edit.get("role", ""))
        with c3:
            profile_email = st.text_input("Email de signature", value=profile_to_edit.get("email", ""))
        profile_context = st.text_area(
            "Contexte personnel pour les lettres",
            value=profile_to_edit.get("context", ""),
            placeholder="Présentation, expérience, responsabilités, manière de s'adresser aux clients…",
            help="Utilisé par l'IA pour écrire au nom de cette personne, en complément du contexte de la marque.",
        )
        if st.form_submit_button("💾 Enregistrer l'utilisateur"):
            try:
                profile = senders.validate_sender({
                    "id": uuid4().hex if edit_sender_id == "__new__" else edit_sender_id,
                    "name": profile_name,
                    "role": profile_role,
                    "email": profile_email,
                    "context": profile_context,
                })
                sheets.save_sender_profile(profile)
            except ValueError as e:
                st.warning(str(e))
            except Exception as e:
                st.error(f"Impossible d'enregistrer l'utilisateur : {e}")
            else:
                load_sender_profiles_cached.clear()
                st.session_state["pending_sender_id"] = profile["id"]
                st.session_state["sender_saved"] = True
                st.rerun()
    if st.session_state.pop("sender_saved", False):
        st.success("Utilisateur enregistré et sélectionné pour les prochains emails.")

    st.divider()
    st.subheader("Informations sur l'entreprise / la marque")
    st.caption(
        "Ces infos servent à personnaliser TOUT ce que l'IA génère (les emails "
        "de prospection) — plus c'est précis, moins les messages "
        "sont génériques. Elles servent aussi pour le titre de l'appli."
    )

    if not (brand_settings.get("activity_description") or brand_settings.get("product_description")):
        st.info(
            "Pas encore configuré : pour l'instant, l'IA utilise la description "
            "par défaut (ROKA, café spécialité) codée dans le projet. Remplis "
            "et enregistre ce formulaire pour que les emails parlent de TON "
            "entreprise à toi."
        )

    with st.form("brand_settings_form"):
        st.markdown("**Marque / entreprise**")
        f_company_name = st.text_input(
            "Nom de l'entreprise / marque", value=brand_settings.get("company_name", "")
        )
        f_tagline = st.text_input("Slogan (optionnel)", value=brand_settings.get("tagline", ""))
        f_activity = st.text_area(
            "Que fait l'entreprise ? (activité, positionnement — 2-3 phrases)",
            value=brand_settings.get("activity_description", ""), height=80,
        )
        f_product = st.text_area(
            "Quel produit / service vend-elle exactement ?",
            value=brand_settings.get("product_description", ""), height=80,
        )
        f_audience = st.text_area(
            "À qui elle vend (client cible typique)",
            value=brand_settings.get("target_audience", ""), height=60,
        )
        f_tone = st.text_area(
            "Ton souhaité pour les emails, et ce qu'il faut éviter",
            value=brand_settings.get("tone_preferences", ""), height=60,
        )

        st.markdown("**Application**")
        c1, c2 = st.columns(2)
        with c1:
            f_app_title = st.text_input(
                "Titre de l'application", value=brand_settings.get("app_title") or config.APP_TITLE
            )
        with c2:
            f_app_icon = st.text_input(
                "Icône (emoji)", value=brand_settings.get("app_icon") or config.APP_ICON
            )

        st.markdown("**Délais de relance (en jours)**")
        c1, c2 = st.columns(2)
        with c1:
            f_relance_days = st.number_input(
                "Après un email sans réponse", min_value=1, max_value=30,
                value=int(brand_settings.get("relance_delay_days") or config.RELANCE_DELAY_DAYS),
            )
        with c2:
            f_sample_days = st.number_input(
                "Après l'envoi d'échantillons", min_value=1, max_value=30,
                value=int(brand_settings.get("sample_relance_delay_days") or config.SAMPLE_RELANCE_DELAY_DAYS),
            )

        st.markdown("**Apparence**")
        f_minimal_design = st.checkbox(
            "🎨 Design minimaliste (optionnel)",
            value=str(brand_settings.get("minimal_design", "")).strip().lower() in ("1", "true", "oui", "yes"),
            help="Rendu plus épuré : espacements plus généreux, boutons plus discrets, "
                 "menu Streamlit masqué. N'affecte aucune donnée, juste l'apparence.",
        )

        submitted = st.form_submit_button("💾 Enregistrer")
        if submitted:
            sheets.save_brand_settings(
                {
                    **brand_settings,
                    "company_name": f_company_name,
                    "tagline": f_tagline,
                    "activity_description": f_activity,
                    "product_description": f_product,
                    "target_audience": f_audience,
                    "tone_preferences": f_tone,
                    "app_title": f_app_title,
                    "app_icon": f_app_icon,
                    "relance_delay_days": f_relance_days,
                    "sample_relance_delay_days": f_sample_days,
                    "minimal_design": "oui" if f_minimal_design else "non",
                }
            )
            load_brand_settings_cached.clear()
            st.success("Enregistré ! L'appli se recharge avec ces informations…")
            st.rerun()

    st.divider()
    st.subheader("🖼️ Bibliothèque d'images")
    st.caption(
        "Ajoute ici une fois pour toutes les images que tu veux pouvoir insérer "
        "dans tes emails (photo produit, logo...) — ensuite, dans l'onglet "
        "✉️ Lettres, télécharge celle à joindre dans ton client mail. "
        "Les images sont automatiquement redimensionnées/compressées pour "
        "rester légères (elles sont stockées dans la Google Sheet)."
    )

    with st.form("add_image_form", clear_on_submit=True):
        img_name = st.text_input("Nom de l'image (pour la retrouver dans la liste)")
        uploaded_img = st.file_uploader("Fichier (JPEG ou PNG)", type=["png", "jpg", "jpeg"])
        add_img_submitted = st.form_submit_button("➕ Ajouter à la bibliothèque")

    if add_img_submitted:
        if not img_name.strip():
            st.warning("Donne un nom à l'image.")
        elif not uploaded_img:
            st.warning("Choisis un fichier image.")
        else:
            try:
                img = Image.open(uploaded_img).convert("RGB")
                img.thumbnail((800, 800))
                quality = 85
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                # on baisse la qualité jusqu'à tenir dans une cellule Google Sheets
                while len(buf.getvalue()) * 4 / 3 > IMAGE_MAX_BASE64_CHARS and quality > 25:
                    quality -= 10
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=quality, optimize=True)
                data = buf.getvalue()
                b64 = base64.b64encode(data).decode("ascii")
                if len(b64) > IMAGE_MAX_BASE64_CHARS:
                    st.error(
                        "Image trop grande même après compression — essaie une image "
                        "plus simple, ou recadrée sur l'essentiel."
                    )
                else:
                    sheets.save_image(img_name.strip(), "image/jpeg", b64)
                    load_images_df_cached.clear()
                    st.success(f"Image « {img_name.strip()} » ajoutée à la bibliothèque.")
                    st.rerun()
            except Exception as e:
                st.error(f"Erreur lors du traitement de l'image : {e}")

    try:
        images_df = load_images_df_cached()
    except Exception as e:
        images_df = pd.DataFrame()
        st.caption(f"Bibliothèque indisponible pour l'instant : {e}")

    if images_df.empty:
        st.caption("Aucune image dans la bibliothèque pour l'instant.")
    else:
        cols = st.columns(4)
        for i, row in images_df.reset_index(drop=True).iterrows():
            with cols[i % 4]:
                try:
                    st.image(base64.b64decode(row["data_base64"]), caption=row["name"], use_container_width=True)
                except Exception:
                    st.caption(f"{row['name']} (aperçu indisponible)")
                if st.button("🗑️ Supprimer", key=f"del_img_{row['name']}"):
                    sheets.delete_image(row["name"])
                    load_images_df_cached.clear()
                    st.rerun()

# ---------------------------------------------------------------------------
# TAB: Clients
# ---------------------------------------------------------------------------
with tab_clients:
    col_a, col_b = st.columns([1, 5])
    with col_a:
        if st.button("🔄 Rafraîchir"):
            refresh()
            st.rerun()

    with st.expander("➕ Ajouter un client", expanded=df.empty):
        with st.form("add_client_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                company = st.text_input("Entreprise *")
                contact_name = st.text_input("Nom du contact")
                contact_role = st.text_input("Fonction du contact")
                email = st.text_input("Email")
                phone = st.text_input("Téléphone")
                city = st.text_input("Ville")
                website = st.text_input(
                    "Site web (optionnel)",
                    placeholder="https://...",
                    help="Aide Claude à trouver la bonne entreprise en cherchant sur le web pour personnaliser la lettre.",
                )
            with c2:
                sector = st.selectbox("Secteur", config.SECTORS)
                region = st.selectbox("Zone", list(config.REGION_SCORE.keys()))
                source = st.selectbox("Source du lead", config.SOURCES)
                volume_potential = st.selectbox("Potentiel de volume", config.VOLUME_POTENTIAL)
                price_sensitivity = st.selectbox(
                    "Sensibilité au prix (perçue)", config.PRICE_SENSITIVITY
                )
            notes = st.text_area("Notes")

            submitted = st.form_submit_button("Ajouter")
            if submitted:
                if not company:
                    st.warning("Le nom de l'entreprise est obligatoire.")
                else:
                    new_id = sheets.append_client(
                        {
                            "company": company,
                            "contact_name": contact_name,
                            "contact_role": contact_role,
                            "email": email,
                            "phone": phone,
                            "city": city,
                            "website": website,
                            "sector": sector,
                            "region": region,
                            "source": source,
                            "volume_potential": volume_potential,
                            "price_sensitivity": price_sensitivity,
                            "notes": notes,
                        }
                    )
                    st.success(f"Client #{new_id} ajouté.")
                    refresh()
                    st.rerun()

    st.subheader("Base clients")
    if df.empty:
        st.info("Pas encore de client. Ajoute le premier ci-dessus.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            status_filter = st.multiselect("Filtrer par statut", config.STATUSES)
        with col2:
            sector_filter = st.multiselect("Filtrer par secteur", config.SECTORS)

        shown = df.copy()
        if status_filter:
            shown = shown[shown["status"].isin(status_filter)]
        if sector_filter:
            shown = shown[shown["sector"].isin(sector_filter)]

        st.dataframe(
            shown[
                [
                    "id", "company", "contact_name", "sector", "city", "status",
                    "last_contact_date", "next_relance_date",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("**Modifier un client**")
        client_id = st.selectbox(
            "Choisir un client",
            shown["id"].tolist() if not shown.empty else [],
            format_func=lambda i: f"{i} — {shown[shown['id'] == i]['company'].values[0]}",
            key="edit_select",
        )
        if client_id:
            client = sheets.get_client_by_id(int(client_id), df=df)

            st.markdown("**📨 Historique des messages envoyés**")
            try:
                client_messages = sheets.get_messages_for_client(int(client_id), messages_df=load_messages_df_cached())
            except Exception as e:
                client_messages = pd.DataFrame()
                st.caption(f"Historique indisponible pour l'instant : {e}")

            if client_messages.empty:
                st.caption("Aucun message envoyé pour l'instant à ce client.")
            else:
                for _, msg in client_messages.iloc[::-1].iterrows():
                    with st.expander(f"{msg['date']} — {msg['type']}"):
                        st.text(msg["texte"] or "(pas de texte)")

            next_date = client.get("next_relance_date")
            if next_date and str(next_date).strip():
                motif = (
                    "avoir son avis sur les échantillons envoyés"
                    if client.get("status") == "RDV / Échantillons"
                    else "relancer suite à un email sans réponse"
                )
                if str(next_date) <= today_str():
                    st.warning(f"⏰ **À faire maintenant** ({next_date}) : {motif}.")
                else:
                    st.info(f"⏰ **Prochaine action prévue le {next_date}** : {motif}.")
            else:
                st.caption("⏰ Aucune action programmée pour l'instant.")

            try:
                all_threads = load_email_threads_df_cached()
                client_threads = (
                    all_threads[all_threads["client_id"] == int(client_id)]
                    if not all_threads.empty else all_threads
                )
            except Exception:
                client_threads = pd.DataFrame()

            if not client_threads.empty:
                st.markdown("**📧 Historique de correspondance importé**")
                for _, th in client_threads.iterrows():
                    stage = th.get("ai_stage") or "stage non déterminé"
                    with st.expander(f"{th.get('subject', '')} — {stage}"):
                        next_action = th.get("next_action") or "—"
                        next_date = th.get("next_follow_up_date") or "pas de date"
                        st.caption(f"Prochaine action (avis de l'IA) : {next_action} ({next_date})")
                        thread_msgs = messages_for_thread_cached(th["thread_id"])
                        for _, m in thread_msgs.iterrows():
                            who = "Nous" if m["direction"] == "out" else "Eux"
                            st.text(f"[{m['date']}] {who} : {m.get('subject', '')}\n{str(m.get('body_text', ''))[:500]}")

            st.divider()
            with st.form("edit_client_form"):
                st.markdown("**Coordonnées**")
                c1, c2 = st.columns(2)
                with c1:
                    e_company = st.text_input("Entreprise", value=client.get("company", ""))
                    e_contact_name = st.text_input("Nom du contact", value=client.get("contact_name", ""))
                    e_contact_role = st.text_input("Fonction du contact", value=client.get("contact_role", ""))
                    e_email = st.text_input("Email", value=client.get("email", ""))
                with c2:
                    e_phone = st.text_input("Téléphone", value=client.get("phone", ""))
                    e_city = st.text_input("Ville", value=client.get("city", ""))
                    e_source = st.selectbox(
                        "Source du lead", config.SOURCES,
                        index=config.SOURCES.index(client["source"]) if client.get("source") in config.SOURCES else 0,
                    )
                    e_website = st.text_input("Site web (optionnel)", value=client.get("website", ""))

                st.markdown("**Qualification**")
                c3, c4 = st.columns(2)
                with c3:
                    e_status = st.selectbox(
                        "Statut", config.STATUSES,
                        index=config.STATUSES.index(client["status"]) if client["status"] in config.STATUSES else 0,
                    )
                    e_sector = st.selectbox(
                        "Secteur", config.SECTORS,
                        index=config.SECTORS.index(client["sector"]) if client["sector"] in config.SECTORS else 0,
                    )
                    e_region = st.selectbox(
                        "Zone", list(config.REGION_SCORE.keys()),
                        index=list(config.REGION_SCORE.keys()).index(client["region"])
                        if client["region"] in config.REGION_SCORE else 0,
                    )
                with c4:
                    e_volume = st.selectbox(
                        "Potentiel de volume", config.VOLUME_POTENTIAL,
                        index=config.VOLUME_POTENTIAL.index(client["volume_potential"])
                        if client["volume_potential"] in config.VOLUME_POTENTIAL else 0,
                    )
                    e_price = st.selectbox(
                        "Sensibilité au prix", config.PRICE_SENSITIVITY,
                        index=config.PRICE_SENSITIVITY.index(client["price_sensitivity"])
                        if client["price_sensitivity"] in config.PRICE_SENSITIVITY else 0,
                    )
                    e_notes = st.text_area("Notes", value=client.get("notes", ""))

                if st.form_submit_button("Enregistrer"):
                    if not e_company.strip():
                        st.warning("Le nom de l'entreprise ne peut pas être vide.")
                    else:
                        sheets.update_client(
                            int(client_id),
                            {
                                "company": e_company,
                                "contact_name": e_contact_name,
                                "contact_role": e_contact_role,
                                "email": e_email,
                                "phone": e_phone,
                                "city": e_city,
                                "source": e_source,
                                "status": e_status,
                                "sector": e_sector,
                                "region": e_region,
                                "volume_potential": e_volume,
                                "price_sensitivity": e_price,
                                "notes": e_notes,
                                "website": e_website,
                            },
                        )
                        st.success("Client mis à jour.")
                        refresh()
                        st.rerun()

# ---------------------------------------------------------------------------
# TAB: Prospection (recherche automatique de prospects par ville)
# ---------------------------------------------------------------------------
with tab_prospecting:
    st.subheader("Chercher automatiquement des prospects par ville")
    st.caption(
        "Claude cherche sur le web de vraies entreprises dans la ville indiquée, "
        "parmi les secteurs cibles configurés. Rien n'est ajouté à la base tant "
        "que tu n'as pas coché puis validé les résultats ci-dessous."
    )

    with st.expander("🗂 Historique des recherches (pour ne pas repasser deux fois au même endroit)"):
        try:
            log_df = load_search_log_df_cached()
        except Exception as e:
            log_df = pd.DataFrame()
            st.caption(f"Historique indisponible pour l'instant : {e}")

        if log_df.empty:
            st.caption("Aucune recherche encore enregistrée — ton historique apparaîtra ici.")
        else:
            st.dataframe(
                log_df.iloc[::-1].rename(
                    columns={
                        "date": "Date", "city": "Ville", "sectors": "Secteurs",
                        "elargi_environs": "Environs inclus", "trouves": "Trouvés", "ajoutes": "Ajoutés",
                    }
                ),
                hide_index=True, use_container_width=True,
            )
            st.caption("**Villes déjà couvertes** : " + ", ".join(sorted(log_df["city"].astype(str).str.strip().unique())))

    c1, c2 = st.columns([3, 1])
    with c1:
        prospect_city = st.text_input("Ville à prospecter", placeholder="ex : Lyon")
    with c2:
        prospect_max = st.number_input("Nombre max", min_value=1, max_value=20, value=10)

    prospect_sectors = st.multiselect(
        "Secteurs à cibler",
        [s for s in config.SECTORS if s != "Autre"],
        default=[s for s in config.SECTORS if s != "Autre"],
        help="Décoche les secteurs que tu ne veux pas prospecter cette fois-ci.",
    )
    prospect_nearby = st.checkbox(
        "Élargir aussi aux environs / communes voisines (pas seulement le centre-ville)",
        value=True,
    )

    if st.button("🔍 Chercher des prospects"):
        if not prospect_city.strip():
            st.warning("Indique une ville.")
        elif not prospect_sectors:
            st.warning("Choisis au moins un secteur.")
        else:
            with st.spinner(f"Recherche en cours à {prospect_city}… (peut prendre 30 à 60 secondes)"):
                try:
                    results = prospecting.find_prospects(
                        prospect_city.strip(),
                        int(prospect_max),
                        sectors=prospect_sectors,
                        include_nearby=prospect_nearby,
                    )
                    st.session_state["prospect_results"] = results
                    st.session_state["prospect_city"] = prospect_city.strip()
                    # on garde une trace de cette recherche (ville, secteurs, date) pour
                    # pouvoir s'y retrouver plus tard — voir l'historique ci-dessus.
                    st.session_state["prospect_log_row"] = sheets.log_prospect_search(
                        prospect_city.strip(), prospect_sectors, prospect_nearby, len(results)
                    )
                    load_search_log_df_cached.clear()
                except Exception as e:
                    st.error(f"Erreur lors de la recherche : {e}")

    if "prospect_results" in st.session_state:
        results = st.session_state["prospect_results"]
        if not results:
            st.info(
                "Aucun prospect trouvé avec certitude pour cette ville. Essaie une "
                "ville plus grande, ou élargis la liste des secteurs dans la config."
            )
        else:
            existing_names = (
                set(df["company"].astype(str).str.strip().str.lower()) if not df.empty else set()
            )

            preview_rows = []
            for r in results:
                is_duplicate = r["company"].strip().lower() in existing_names
                notes = r["notes"]
                if is_duplicate:
                    notes = (notes + "  ⚠️ déjà présent dans la base").strip()
                preview_rows.append(
                    {
                        "Ajouter ?": not is_duplicate,
                        "Entreprise": r["company"],
                        "Secteur": r["sector"],
                        "Ville": r["city"],
                        "Site web": r["website"],
                        "Email": r["email"],
                        "Téléphone": r["phone"],
                        "Notes": notes,
                    }
                )
            preview_df = pd.DataFrame(preview_rows)

            st.warning(
                "⚠️ Les emails et téléphones trouvés par l'IA ne sont pas garantis "
                "exacts — vérifie-les avant d'envoyer un email ou d'appeler. Le nom "
                "de l'entreprise et le site web sont généralement plus fiables."
            )

            edited = st.data_editor(
                preview_df,
                hide_index=True,
                use_container_width=True,
                disabled=[
                    "Entreprise", "Secteur", "Ville", "Site web", "Email", "Téléphone", "Notes",
                ],
                column_config={"Ajouter ?": st.column_config.CheckboxColumn("Ajouter ?")},
                key="prospect_editor",
            )

            to_add = edited[edited["Ajouter ?"]]
            st.write(f"**{len(to_add)}** sélectionné(s) sur {len(edited)} trouvé(s).")

            if st.button("➕ Ajouter les prospects sélectionnés", disabled=to_add.empty):
                rows_to_add = [
                    {
                        "company": row["Entreprise"],
                        "sector": row["Secteur"] if row["Secteur"] in config.SECTORS else "Autre",
                        "city": row["Ville"],
                        "website": row["Site web"],
                        "email": row["Email"],
                        "phone": row["Téléphone"],
                        "source": "Prospection automatique (recherche IA)",
                        "notes": f"[Recherche auto à {st.session_state.get('prospect_city', '')}] {row['Notes']}",
                    }
                    for _, row in to_add.iterrows()
                ]
                # un seul appel à l'API pour tout le lot — évite d'épuiser le quota
                # Google Sheets quand on ajoute plusieurs prospects d'un coup
                new_ids = sheets.append_clients(rows_to_add)
                log_row = st.session_state.get("prospect_log_row")
                if log_row:
                    sheets.update_search_log_added(log_row, len(new_ids))
                st.success(f"{len(new_ids)} prospect(s) ajouté(s) à la base, statut « Nouveau ».")
                del st.session_state["prospect_results"]
                st.session_state.pop("prospect_log_row", None)
                refresh()
                st.rerun()

# ---------------------------------------------------------------------------
# TAB: Lettres
# ---------------------------------------------------------------------------
with tab_letters:
    st.subheader("Générer une lettre de prise de contact personnalisée")
    st.caption(f"Lettre rédigée au nom de {active_sender['name']}.")
    with st.expander("Signature de l'utilisateur actif"):
        st.text(senders.signature(active_sender))
    st.caption(
        "Objet ET corps du message générés par Claude à partir du profil du "
        "client — relis toujours avant d'envoyer depuis ton client mail."
    )

    eligible = df[df["status"].isin(["Nouveau", "À qualifier", "À contacter"])] if not df.empty else df
    if eligible.empty:
        st.info(
            "Ajoute un prospect dans Clients ou Prospection, ou passe un client "
            "au statut « À contacter » dans Clients pour préparer son premier email."
        )
    else:
        client_id = st.selectbox(
            "Client", eligible["id"].tolist(),
            format_func=lambda i: f"{i} — {eligible[eligible['id'] == i]['company'].values[0]}",
        )
        client = sheets.get_client_by_id(int(client_id), df=df)

        draft_id = senders.draft_key(active_sender, int(client_id), brand_context)
        drafts = st.session_state.setdefault("letter_drafts", {})
        subject_widget = f"letter_subject_{draft_id}"
        body_widget = f"letter_body_{draft_id}"

        try:
            images_df = load_images_df_cached()
        except Exception:
            images_df = pd.DataFrame()
        image_options = ["(Aucune image)"] + (images_df["name"].tolist() if not images_df.empty else [])
        selected_image_name = st.selectbox(
            "🖼️ Image à joindre à l'email (optionnel)",
            image_options,
            help="Gère la bibliothèque d'images dans l'onglet ⚙️ Paramètres.",
        )

        if selected_image_name != "(Aucune image)":
            selected_image = images_df[images_df["name"] == selected_image_name].iloc[0]
            image_mime = selected_image["content_type"]
            st.download_button(
                "📥 Télécharger l'image à joindre",
                data=base64.b64decode(selected_image["data_base64"]),
                file_name="image.png" if image_mime == "image/png" else "image.jpg",
                mime=image_mime,
            )
            st.caption("Ajoute cette image manuellement à ton email dans ton client mail.")

        if st.button("✍️ Générer la lettre (objet + texte)"):
            with st.spinner("Génération en cours…"):
                try:
                    result = letters.generate_first_letter(client, sender=active_sender, brand_context=brand_context)
                    drafts[draft_id] = result
                    st.session_state.pop(subject_widget, None)
                    st.session_state.pop(body_widget, None)
                except Exception as e:
                    st.error(f"Erreur lors de la génération : {e}")

        if draft_id in drafts:
            edited_subject = st.text_input(
                "Objet de l'email (modifiable, généré automatiquement)",
                value=drafts[draft_id]["subject"], key=subject_widget,
                on_change=remember_draft_edit, args=(draft_id, "subject", subject_widget),
            )
            edited = st.text_area(
                "Texte de la lettre (modifiable)", value=drafts[draft_id]["body"], height=300,
                key=body_widget, on_change=remember_draft_edit, args=(draft_id, "body", body_widget),
            )

            st.markdown("**Envoyer depuis ton client mail**")
            if active_sender.get("email"):
                st.caption(f"Dans ton client mail, sélectionne aussi le compte expéditeur {active_sender['email']}.")
            c1, c2 = st.columns(2)
            with c1:
                mailto_body = quote(edited)
                mailto_subject = quote(edited_subject)
                mail_link = f"mailto:{client.get('email', '')}?subject={mailto_subject}&body={mailto_body}"
                st.link_button("📧 Ouvrir dans mon client mail", mail_link)
            with c2:
                if st.button("💾 Sauvegarder le brouillon"):
                    sheets.update_client(
                        int(client_id),
                        {"letter_text": edited, "letter_generated_at": today_str()},
                    )
                    st.success("Brouillon sauvegardé (statut inchangé).")
                    refresh()

            if st.button("✅ Marquer comme envoyé (déjà envoyé moi-même)"):
                sheets.update_client(
                    int(client_id),
                    {
                        "letter_text": edited,
                        "letter_generated_at": today_str(),
                        "status": "Contacté",
                        "last_contact_date": today_str(),
                        "next_relance_date": add_days(today_str(), config.RELANCE_DELAY_DAYS),
                    },
                )
                sender_label = active_sender["name"]
                if active_sender.get("email"):
                    sender_label += f" <{active_sender['email']}>"
                sheets.log_message(int(client_id), client.get("company", ""), f"Premier email — {sender_label}", edited)
                st.success(f"Marqué comme envoyé. Relance programmée dans {config.RELANCE_DELAY_DAYS} jours.")
                drafts.pop(draft_id, None)
                refresh()
                st.rerun()
