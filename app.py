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

import pandas as pd
import streamlit as st
from PIL import Image

import config
import gmail_sync
import letters
import prospecting
import scoring
import sheets

# Taille max d'une image encodée en base64 stockée dans une cellule Google
# Sheets (limite réelle ~50 000 caractères par cellule) — on vise large en
# dessous pour garder de la marge.
IMAGE_MAX_BASE64_CHARS = 42000

# ---------------------------------------------------------------------------
# Infos entreprise/marque — remplies dans l'onglet "⚙️ Paramètres" et stockées
# dans l'onglet "Config" de la Google Sheet (pas besoin de toucher au code ni
# aux Secrets). Tant que rien n'est encore configuré, on garde les valeurs
# par défaut de config.py (ROKA).
# ---------------------------------------------------------------------------
try:
    brand_settings = sheets.load_brand_settings()
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
_apply_setting("SENDER_NAME", "sender_name")
_apply_setting("SENDER_ROLE", "sender_role")
_apply_setting("SENDER_EMAIL", "sender_email")
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
# Gmail — retour de l'écran d'autorisation Google (présence de ?code=... dans
# l'URL). On échange le code contre les tokens, on les sauvegarde, puis on
# nettoie l'URL pour ne pas retraiter le même code à chaque rechargement.
# ---------------------------------------------------------------------------
if "code" in st.query_params and config.GMAIL_CLIENT_ID:
    try:
        _tokens = gmail_sync.exchange_code_for_tokens(st.query_params["code"])
        sheets.save_gmail_auth(_tokens)
        st.query_params.clear()
        st.success("✅ Gmail connecté avec succès ! Va dans l'onglet 📧 Gmail pour lancer le premier import.")
    except Exception as e:
        st.query_params.clear()
        st.error(f"Erreur lors de la connexion à Gmail : {e}")


# ---------------------------------------------------------------------------
# Данные — с кэшем, чтобы не дёргать Google Sheets на каждый клик
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30, show_spinner="Chargement depuis Google Sheets…")
def load_data() -> pd.DataFrame:
    return sheets.load_clients_df()


def refresh():
    load_data.clear()


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def add_days(date_str: str, days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


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

# ---------------------------------------------------------------------------
# Gmail — synchronisation "automatique" à l'ouverture de l'appli. Le tout
# PREMIER import (potentiellement des centaines de messages + classification
# IA de chaque conversation) se lance à la main depuis l'onglet Gmail, pour
# ne pas bloquer l'appli plusieurs minutes à l'ouverture sans prévenir.
# Une fois ce premier import fait, les ouvertures suivantes resynchronisent
# tout seules (au plus une fois toutes les GMAIL_MIN_SYNC_INTERVAL_MINUTES
# minutes) — c'est ça, "l'automatique" possible sur un hébergement gratuit
# qui ne fait tourner aucun processus permanent en tâche de fond.
# ---------------------------------------------------------------------------
try:
    _gmail_auth = sheets.load_gmail_auth()
except Exception:
    _gmail_auth = {}

if (
    _gmail_auth.get("refresh_token")
    and _gmail_auth.get("last_synced_at")
    and gmail_sync.should_auto_sync(_gmail_auth)
):
    try:
        gmail_sync.sync()
        refresh()
    except Exception:
        pass  # on ne bloque jamais l'appli pour un souci de sync silencieux

tab_settings, tab_clients, tab_prospecting, tab_scoring, tab_letters, tab_relance, tab_dashboard, tab_gmail = st.tabs(
    ["⚙️ Paramètres", "📋 Clients", "🔎 Prospection", "🎯 Scoring", "✉️ Lettres", "🔁 Relances", "📊 Dashboard", "📧 Gmail"]
)

# ---------------------------------------------------------------------------
# TAB: Paramètres (infos entreprise/marque — utilisées pour personnaliser
# les emails générés par l'IA, sans jamais toucher au code)
# ---------------------------------------------------------------------------
with tab_settings:
    st.subheader("Informations sur l'entreprise / la marque")
    st.caption(
        "Ces infos servent à personnaliser TOUT ce que l'IA génère (les emails "
        "de prospection et de relance) — plus c'est précis, moins les messages "
        "sont génériques. Elles servent aussi pour le titre de l'appli et la "
        "signature des emails."
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

        st.markdown("**Signature des emails**")
        c1, c2, c3 = st.columns(3)
        with c1:
            f_sender_name = st.text_input(
                "Nom", value=brand_settings.get("sender_name") or config.SENDER_NAME
            )
        with c2:
            f_sender_role = st.text_input(
                "Fonction", value=brand_settings.get("sender_role") or config.SENDER_ROLE
            )
        with c3:
            f_sender_email = st.text_input(
                "Email", value=brand_settings.get("sender_email") or config.SENDER_EMAIL
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
                    "company_name": f_company_name,
                    "tagline": f_tagline,
                    "activity_description": f_activity,
                    "product_description": f_product,
                    "target_audience": f_audience,
                    "tone_preferences": f_tone,
                    "app_title": f_app_title,
                    "app_icon": f_app_icon,
                    "sender_name": f_sender_name,
                    "sender_role": f_sender_role,
                    "sender_email": f_sender_email,
                    "relance_delay_days": f_relance_days,
                    "sample_relance_delay_days": f_sample_days,
                    "minimal_design": "oui" if f_minimal_design else "non",
                }
            )
            st.success("Enregistré ! L'appli se recharge avec ces informations…")
            st.rerun()

    st.divider()
    st.subheader("🖼️ Bibliothèque d'images")
    st.caption(
        "Ajoute ici une fois pour toutes les images que tu veux pouvoir insérer "
        "dans tes emails (photo produit, logo...) — ensuite, dans l'onglet "
        "✉️ Lettres, tu choisis simplement laquelle utiliser dans une liste. "
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
                    st.success(f"Image « {img_name.strip()} » ajoutée à la bibliothèque.")
                    st.rerun()
            except Exception as e:
                st.error(f"Erreur lors du traitement de l'image : {e}")

    try:
        images_df = sheets.load_images_df()
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
                    "fit_score", "fit_label", "last_contact_date", "next_relance_date",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("**Modifier un client**")
        client_id = st.selectbox(
            "Choisir un client", shown["id"].tolist() if not shown.empty else [], key="edit_select"
        )
        if client_id:
            client = sheets.get_client_by_id(int(client_id), df=df)

            st.markdown("**📨 Historique des messages envoyés**")
            try:
                client_messages = sheets.get_messages_for_client(int(client_id))
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
                    st.warning(f"⏰ **À faire maintenant** ({next_date}) : {motif}. Voir l'onglet Relances.")
                else:
                    st.info(f"⏰ **Prochaine action prévue le {next_date}** : {motif}.")
            else:
                st.caption("⏰ Aucune action programmée pour l'instant.")

            try:
                all_threads = sheets.load_email_threads_df()
                client_threads = (
                    all_threads[all_threads["client_id"] == int(client_id)]
                    if not all_threads.empty else all_threads
                )
            except Exception:
                client_threads = pd.DataFrame()

            if not client_threads.empty:
                st.markdown("**📧 Correspondance Gmail liée**")
                for _, th in client_threads.iterrows():
                    stage = th.get("ai_stage") or "stage non déterminé"
                    with st.expander(f"{th.get('subject', '')} — {stage}"):
                        next_action = th.get("next_action") or "—"
                        next_date = th.get("next_follow_up_date") or "pas de date"
                        st.caption(f"Prochaine action (avis de l'IA) : {next_action} ({next_date})")
                        thread_msgs = sheets.load_email_messages_for_thread(th["thread_id"])
                        for _, m in thread_msgs.iterrows():
                            who = "Nous" if m["direction"] == "out" else "Eux"
                            st.text(f"[{m['date']}] {who} : {m.get('subject', '')}\n{str(m.get('body_text', ''))[:500]}")

            st.divider()
            with st.form("edit_client_form"):
                c1, c2 = st.columns(2)
                with c1:
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
                with c2:
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
                    e_website = st.text_input("Site web (optionnel)", value=client.get("website", ""))

                if st.form_submit_button("Enregistrer"):
                    sheets.update_client(
                        int(client_id),
                        {
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
            log_df = sheets.load_search_log_df()
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
# TAB: Scoring
# ---------------------------------------------------------------------------
with tab_scoring:
    st.subheader("Évaluer si un prospect correspond à ROKA (fit / pas fit)")
    st.caption(
        "Score basé sur des règles simples et transparentes (secteur, zone, volume, "
        "sensibilité au prix) — modifiables dans config.py. Pas d'IA ici, exprès : "
        "un score doit être reproductible et explicable."
    )

    if df.empty:
        st.info("Ajoute des clients dans l'onglet Clients d'abord.")
    else:
        to_score = df[df["status"].isin(["Nouveau", "À qualifier"])]
        st.write(f"**{len(to_score)}** client(s) en attente de scoring.")

        if not to_score.empty and st.button("⚡ Scorer tous les clients en attente"):
            results = []
            updates_by_id = {}
            for _, row in to_score.iterrows():
                res = scoring.score_client(row.to_dict())
                new_status = (
                    "À contacter" if res.label == "Fit ✅"
                    else "Non pertinent" if res.label == "Pas fit ❌"
                    else "À qualifier"
                )
                updates_by_id[int(row["id"])] = {
                    "fit_score": res.score,
                    "fit_label": res.label,
                    "fit_reasoning": res.reasoning,
                    "status": new_status,
                }
                results.append({"id": row["id"], "company": row["company"], "score": res.score, "label": res.label})
            # un seul appel à l'API pour tout le lot — évite d'épuiser le quota
            # Google Sheets quand il y a beaucoup de clients à scorer d'un coup
            sheets.update_clients(updates_by_id)
            st.success(f"{len(results)} client(s) scoré(s).")
            st.dataframe(pd.DataFrame(results), hide_index=True, use_container_width=True)
            refresh()

        st.divider()
        st.markdown("**Scorer un client précis**")
        client_id = st.selectbox("Client", df["id"].tolist(), key="score_select")
        if client_id:
            client = sheets.get_client_by_id(int(client_id), df=df)
            st.write(f"Secteur : {client['sector']} · Zone : {client['region']} · "
                      f"Volume : {client['volume_potential']} · Sensibilité prix : {client['price_sensitivity']}")
            if st.button("Calculer le score"):
                res = scoring.score_client(client)
                st.metric("Score", f"{res.score}/100", res.label)
                st.text(res.reasoning)
                st.session_state["last_score"] = res

            if "last_score" in st.session_state and st.button("💾 Enregistrer ce score"):
                res = st.session_state["last_score"]
                new_status = (
                    "À contacter" if res.label == "Fit ✅"
                    else "Non pertinent" if res.label == "Pas fit ❌"
                    else "À qualifier"
                )
                sheets.update_client(
                    int(client_id),
                    {
                        "fit_score": res.score,
                        "fit_label": res.label,
                        "fit_reasoning": res.reasoning,
                        "status": new_status,
                    },
                )
                st.success("Score enregistré.")
                del st.session_state["last_score"]
                refresh()
                st.rerun()

# ---------------------------------------------------------------------------
# TAB: Lettres
# ---------------------------------------------------------------------------
with tab_letters:
    st.subheader("Générer une lettre de prise de contact personnalisée")
    st.caption(
        "Objet ET corps du message générés par Claude à partir du profil du "
        "client — relis toujours avant d'envoyer. Rien ne part sans que tu "
        "cliques explicitement sur un bouton d'envoi."
    )

    eligible = df[df["status"] == "À contacter"] if not df.empty else df
    if eligible.empty:
        st.info("Aucun client au statut « À contacter ». Score des clients dans l'onglet Scoring d'abord.")
    else:
        client_id = st.selectbox(
            "Client", eligible["id"].tolist(),
            format_func=lambda i: f"{i} — {eligible[eligible['id'] == i]['company'].values[0]}",
        )
        client = sheets.get_client_by_id(int(client_id), df=df)

        try:
            images_df = sheets.load_images_df()
        except Exception:
            images_df = pd.DataFrame()
        image_options = ["(Aucune image)"] + (images_df["name"].tolist() if not images_df.empty else [])
        selected_image_name = st.selectbox(
            "🖼️ Image à insérer dans l'email (optionnel)",
            image_options,
            help="Gère la bibliothèque d'images dans l'onglet ⚙️ Paramètres.",
        )

        if st.button("✍️ Générer la lettre (objet + texte)"):
            with st.spinner("Génération en cours…"):
                try:
                    result = letters.generate_first_letter(client)
                    st.session_state["draft_subject"] = result["subject"]
                    st.session_state["draft_letter"] = result["body"]
                except Exception as e:
                    st.error(f"Erreur lors de la génération : {e}")

        if "draft_letter" in st.session_state:
            edited_subject = st.text_input(
                "Objet de l'email (modifiable, généré automatiquement)",
                value=st.session_state.get("draft_subject", ""),
            )
            edited = st.text_area("Texte de la lettre (modifiable)", value=st.session_state["draft_letter"], height=300)

            has_image = selected_image_name != "(Aucune image)"
            if has_image:
                st.caption(
                    "ℹ️ L'image ne s'affichera QUE si tu utilises « Envoyer via Gmail » ci-dessous "
                    "— un lien « ouvrir dans mon client mail » ne peut techniquement pas inclure d'image."
                )

            st.markdown("**Option 1 — tu envoies toi-même**")
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
                sheets.log_message(int(client_id), client.get("company", ""), "Premier email", edited)
                st.success(f"Marqué comme envoyé. Relance programmée dans {config.RELANCE_DELAY_DAYS} jours.")
                del st.session_state["draft_letter"]
                st.session_state.pop("draft_subject", None)
                refresh()
                st.rerun()

            st.markdown("**Option 2 — l'appli envoie pour toi, pour de vrai, via Gmail**")
            send_label = "📧 Envoyer via Gmail" + (" (avec image)" if has_image else "")
            if st.button(send_label, type="primary"):
                image_payload = sheets.get_image(selected_image_name) if has_image else None
                with st.spinner("Envoi en cours…"):
                    try:
                        gmail_sync.send_single(
                            client.get("email", ""), edited_subject, edited, image=image_payload
                        )
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
                        sheets.log_message(
                            int(client_id), client.get("company", ""), "Premier email (envoyé via Gmail)", edited
                        )
                        st.success(
                            f"✅ Email envoyé pour de vrai via Gmail. Relance programmée dans "
                            f"{config.RELANCE_DELAY_DAYS} jours."
                        )
                        del st.session_state["draft_letter"]
                        st.session_state.pop("draft_subject", None)
                        refresh()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erreur lors de l'envoi : {e}")

# ---------------------------------------------------------------------------
# TAB: Relances
# ---------------------------------------------------------------------------
with tab_relance:
    st.subheader(
        f"Relances (par défaut {config.RELANCE_DELAY_DAYS} jours après un email, "
        f"{config.SAMPLE_RELANCE_DELAY_DAYS} jours après l'envoi d'échantillons)"
    )

    if df.empty:
        st.info("Pas encore de client.")
    else:
        open_df = df[df["status"].isin(config.OPEN_STATUSES_FOR_RELANCE)].copy()
        due_today = open_df[
            open_df["next_relance_date"].fillna("") <= today_str()
        ]
        due_today = due_today[due_today["next_relance_date"].fillna("") != ""]

        st.write(f"**{len(due_today)}** client(s) à relancer aujourd'hui (ou en retard).")
        if not due_today.empty:
            due_today_display = due_today.copy()
            due_today_display["Motif"] = due_today_display["status"].map(
                lambda s: "📦 Échantillons envoyés" if s == "RDV / Échantillons" else "✉️ Email sans réponse"
            )
            st.dataframe(
                due_today_display[
                    ["id", "company", "Motif", "last_contact_date", "next_relance_date", "relance_count"]
                ],
                hide_index=True, use_container_width=True,
            )

        st.divider()
        pool = due_today if not due_today.empty else open_df
        if pool.empty:
            st.info("Rien à relancer pour l'instant.")
        else:
            client_id = st.selectbox(
                "Client à relancer", pool["id"].tolist(),
                format_func=lambda i: f"{i} — {pool[pool['id'] == i]['company'].values[0]}",
            )
            client = sheets.get_client_by_id(int(client_id), df=df)

            st.markdown("**Tu viens d'envoyer des échantillons à ce client ?**")
            if st.button("📦 Marquer échantillons envoyés"):
                sheets.update_client(
                    int(client_id),
                    {
                        "status": "RDV / Échantillons",
                        "last_contact_date": today_str(),
                        "next_relance_date": add_days(today_str(), config.SAMPLE_RELANCE_DELAY_DAYS),
                    },
                )
                sheets.log_message(
                    int(client_id), client.get("company", ""), "Échantillons envoyés",
                    "(envoi physique — pas de texte d'email)",
                )
                st.success(
                    f"Noté. Relance programmée dans {config.SAMPLE_RELANCE_DELAY_DAYS} jours "
                    "pour avoir son avis sur les échantillons."
                )
                refresh()
                st.rerun()

            st.divider()
            if st.button("✍️ Générer la relance (objet + texte)"):
                with st.spinner("Génération en cours…"):
                    try:
                        result = letters.generate_relance(client)
                        st.session_state["draft_relance_subject"] = result["subject"]
                        st.session_state["draft_relance"] = result["body"]
                    except Exception as e:
                        st.error(f"Erreur lors de la génération : {e}")

            if "draft_relance" in st.session_state:
                edited_relance_subject = st.text_input(
                    "Objet de la relance (modifiable, généré automatiquement)",
                    value=st.session_state.get("draft_relance_subject", ""),
                    key="relance_subject_input",
                )
                edited = st.text_area("Texte de la relance (modifiable)", value=st.session_state["draft_relance"], height=200)
                mailto_body = quote(edited)
                mailto_subject = quote(edited_relance_subject)
                mail_link = f"mailto:{client.get('email', '')}?subject={mailto_subject}&body={mailto_body}"

                c1, c2 = st.columns(2)
                with c1:
                    st.link_button("📧 Ouvrir dans mon client mail", mail_link)
                with c2:
                    if st.button("✅ Marquer la relance comme envoyée"):
                        sheets.update_client(
                            int(client_id),
                            {
                                "status": "Relance envoyée",
                                "last_contact_date": today_str(),
                                "next_relance_date": add_days(today_str(), config.RELANCE_DELAY_DAYS),
                                "relance_count": int(client.get("relance_count") or 0) + 1,
                            },
                        )
                        sheets.log_message(int(client_id), client.get("company", ""), "Relance", edited)
                        st.success("Relance enregistrée.")
                        del st.session_state["draft_relance"]
                        st.session_state.pop("draft_relance_subject", None)
                        refresh()
                        st.rerun()

                if st.button("📧 Envoyer cette relance via Gmail (pour de vrai)"):
                    with st.spinner("Envoi en cours…"):
                        try:
                            gmail_sync.send_single(client.get("email", ""), edited_relance_subject, edited)
                            sheets.update_client(
                                int(client_id),
                                {
                                    "status": "Relance envoyée",
                                    "last_contact_date": today_str(),
                                    "next_relance_date": add_days(today_str(), config.RELANCE_DELAY_DAYS),
                                    "relance_count": int(client.get("relance_count") or 0) + 1,
                                },
                            )
                            sheets.log_message(
                                int(client_id), client.get("company", ""), "Relance (envoyée via Gmail)", edited
                            )
                            st.success("✅ Relance envoyée pour de vrai via Gmail.")
                            del st.session_state["draft_relance"]
                            st.session_state.pop("draft_relance_subject", None)
                            refresh()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erreur lors de l'envoi : {e}")

            st.divider()
            st.markdown("**Ou mettre à jour le statut directement** (le client a répondu, a dit non, etc.)")
            new_status = st.selectbox("Nouveau statut", config.STATUSES, key="relance_status_update")
            if st.button("Mettre à jour le statut"):
                sheets.update_client(int(client_id), {"status": new_status})
                st.success("Statut mis à jour.")
                refresh()
                st.rerun()

# ---------------------------------------------------------------------------
# TAB: Dashboard
# ---------------------------------------------------------------------------
with tab_dashboard:
    st.subheader("Métriques")

    if df.empty:
        st.info("Pas encore de données.")
    else:
        total = len(df)
        scored = df[df["fit_score"].notna()]
        fit_count = len(df[df["fit_label"] == "Fit ✅"])
        contacted = df[df["status"].isin(
            ["Contacté", "Relance envoyée", "Répondu", "RDV / Échantillons", "Client", "Pas intéressé"]
        )]
        replied = df[df["status"].isin(["Répondu", "RDV / Échantillons", "Client"])]
        clients_won = len(df[df["status"] == "Client"])

        due_today = df[
            df["status"].isin(config.OPEN_STATUSES_FOR_RELANCE)
            & (df["next_relance_date"].fillna("") <= today_str())
            & (df["next_relance_date"].fillna("") != "")
        ]

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Clients au total", total)
        c2.metric("Fit ✅", fit_count, f"{(fit_count/total*100):.0f}% de la base" if total else None)
        c3.metric("Contactés", len(contacted))
        response_rate = (len(replied) / len(contacted) * 100) if len(contacted) else 0
        c4.metric("Taux de réponse", f"{response_rate:.0f}%")
        c5.metric("À relancer aujourd'hui", len(due_today))

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Répartition par statut**")
            status_counts = df["status"].value_counts().reindex(config.STATUSES).fillna(0)
            st.bar_chart(status_counts)
        with col2:
            st.markdown("**Répartition par secteur**")
            sector_counts = df["sector"].value_counts()
            st.bar_chart(sector_counts)

        st.divider()
        st.metric("Clients gagnés 🏆", clients_won)
        if not scored.empty:
            st.metric("Score moyen (clients scorés)", f"{scored['fit_score'].mean():.0f}/100")

# ---------------------------------------------------------------------------
# TAB: Gmail (import automatique des emails + CRM stage déterminé par l'IA)
# ---------------------------------------------------------------------------
with tab_gmail:
    st.subheader("Import automatique depuis Gmail")
    st.caption(
        "Connecte ta boîte Gmail pour importer l'historique des emails envoyés "
        "et reçus (12 derniers mois au premier import), créer/lier "
        "automatiquement les clients correspondants, et laisser l'IA "
        "déterminer où en est chaque conversation."
    )

    if not (config.GMAIL_CLIENT_ID and config.GMAIL_CLIENT_SECRET and config.GMAIL_REDIRECT_URI):
        st.warning(
            "Gmail n'est pas encore configuré. Il faut d'abord créer un "
            "identifiant OAuth (\"Application Web\") dans Google Cloud Console "
            "et renseigner GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / "
            "GMAIL_REDIRECT_URI dans les Secrets de l'appli — voir "
            "GMAIL_SETUP.md pour le guide pas à pas."
        )
    else:
        try:
            gmail_auth = sheets.load_gmail_auth()
        except Exception as e:
            gmail_auth = {}
            st.error(f"Impossible de lire la connexion Gmail : {e}")

        connected = bool(gmail_auth.get("refresh_token"))

        if not connected:
            auth_url = gmail_sync.get_authorization_url(state="roka_crm")
            st.link_button("🔐 Se connecter avec Google", auth_url)
            st.caption(
                "Tu seras redirigé vers Google pour autoriser la LECTURE SEULE "
                "de ta boîte Gmail — aucun envoi, aucune suppression, aucune "
                "modification n'est possible avec cet accès."
            )
        else:
            last_sync = gmail_auth.get("last_synced_at", "")
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                st.success(f"✅ Connecté : {gmail_auth.get('email_address', '')}")
                st.caption(f"Dernière synchro : {last_sync or 'jamais — fais le premier import ci-contre'}")
            with c2:
                sync_label = "📥 Premier import (12 mois)" if not last_sync else "🔄 Synchroniser maintenant"
                if st.button(sync_label):
                    with st.spinner(
                        "Synchronisation en cours… le premier import peut prendre "
                        "plusieurs minutes selon le volume d'emails."
                    ):
                        try:
                            result = gmail_sync.sync()
                            st.success(
                                f"{result['new_messages']} nouveau(x) message(s) · "
                                f"{result['threads_updated']} conversation(s) mise(s) à jour · "
                                f"{result['threads_classified']} classifiée(s) par l'IA."
                            )
                            refresh()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erreur pendant la synchronisation : {e}")
            with c3:
                if st.button("🔌 Déconnecter"):
                    sheets.disconnect_gmail()
                    st.success("Gmail déconnecté.")
                    st.rerun()

        st.divider()
        st.markdown("**Conversations importées**")
        try:
            threads_df = sheets.load_email_threads_df()
        except Exception as e:
            threads_df = pd.DataFrame()
            st.caption(f"Impossible de charger les conversations : {e}")

        if threads_df.empty:
            st.info("Aucune conversation importée pour l'instant.")
        else:
            stage_filter = st.multiselect("Filtrer par stage", config.GMAIL_CRM_STAGES, key="gmail_stage_filter")
            shown_threads = threads_df.copy()
            if stage_filter:
                shown_threads = shown_threads[shown_threads["ai_stage"].isin(stage_filter)]

            shown_threads = shown_threads.sort_values("last_inbound_date", ascending=False)
            st.dataframe(
                shown_threads[
                    [
                        "contact_email", "subject", "ai_stage", "next_action",
                        "next_follow_up_date", "last_inbound_date", "last_outbound_date", "message_count",
                    ]
                ],
                hide_index=True, use_container_width=True,
            )

            st.markdown("**Voir un thread en détail**")
            thread_options = shown_threads["thread_id"].tolist()
            if thread_options:
                selected_thread = st.selectbox(
                    "Conversation",
                    thread_options,
                    format_func=lambda tid: shown_threads[shown_threads["thread_id"] == tid]["subject"].values[0] or tid,
                    key="gmail_thread_select",
                )
                thread_messages = sheets.load_email_messages_for_thread(selected_thread)
                for _, m in thread_messages.iterrows():
                    who = "📤 Nous" if m["direction"] == "out" else "📥 Eux"
                    with st.expander(f"{m['date']} — {who} — {m.get('subject', '')}"):
                        st.text(m.get("body_text", "") or "(pas de texte)")
