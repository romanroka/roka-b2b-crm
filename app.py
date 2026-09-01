# -*- coding: utf-8 -*-
"""
ROKA B2B CRM — MVP Streamlit.

Запуск: streamlit run app.py
Инструкция по настройке — в README.md.
"""

from datetime import datetime, timedelta
from urllib.parse import quote

import pandas as pd
import streamlit as st

import config
import letters
import prospecting
import scoring
import sheets

st.set_page_config(page_title=config.APP_TITLE, page_icon=config.APP_ICON, layout="wide")


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

tab_clients, tab_prospecting, tab_scoring, tab_letters, tab_relance, tab_dashboard = st.tabs(
    ["📋 Clients", "🔎 Prospection", "🎯 Scoring", "✉️ Lettres", "🔁 Relances", "📊 Dashboard"]
)

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
                st.success(f"{len(new_ids)} prospect(s) ajouté(s) à la base, statut « Nouveau ».")
                del st.session_state["prospect_results"]
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
        "Générée par Claude à partir du profil du client. Relis toujours avant "
        "d'envoyer — tu gardes la main, rien n'est envoyé automatiquement."
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

        if st.button("✍️ Générer la lettre"):
            with st.spinner("Génération en cours…"):
                try:
                    text = letters.generate_first_letter(client)
                    st.session_state["draft_letter"] = text
                except Exception as e:
                    st.error(f"Erreur lors de la génération : {e}")

        if "draft_letter" in st.session_state:
            edited = st.text_area("Texte de la lettre (modifiable)", value=st.session_state["draft_letter"], height=300)

            mailto_body = quote(edited)
            mailto_subject = quote(f"ROKA — café spécialité pour {client.get('company', '')}")
            mail_link = f"mailto:{client.get('email', '')}?subject={mailto_subject}&body={mailto_body}"

            c1, c2, c3 = st.columns(3)
            with c1:
                st.link_button("📧 Ouvrir dans mon client mail", mail_link)
            with c2:
                if st.button("💾 Sauvegarder le brouillon"):
                    sheets.update_client(
                        int(client_id),
                        {"letter_text": edited, "letter_generated_at": today_str()},
                    )
                    st.success("Brouillon sauvegardé (statut inchangé).")
                    refresh()
            with c3:
                if st.button("✅ Marquer comme envoyé"):
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
                    st.success(f"Marqué comme envoyé. Relance programmée dans {config.RELANCE_DELAY_DAYS} jours.")
                    del st.session_state["draft_letter"]
                    refresh()
                    st.rerun()

# ---------------------------------------------------------------------------
# TAB: Relances
# ---------------------------------------------------------------------------
with tab_relance:
    st.subheader(f"Relances (par défaut {config.RELANCE_DELAY_DAYS} jours après le dernier contact)")

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
            st.dataframe(
                due_today[["id", "company", "status", "last_contact_date", "next_relance_date", "relance_count"]],
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

            if st.button("✍️ Générer la relance"):
                with st.spinner("Génération en cours…"):
                    try:
                        text = letters.generate_relance(client)
                        st.session_state["draft_relance"] = text
                    except Exception as e:
                        st.error(f"Erreur lors de la génération : {e}")

            if "draft_relance" in st.session_state:
                edited = st.text_area("Texte de la relance (modifiable)", value=st.session_state["draft_relance"], height=200)
                mailto_body = quote(edited)
                mailto_subject = quote(f"ROKA — un petit mot de plus, {client.get('company', '')}")
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
                        st.success("Relance enregistrée.")
                        del st.session_state["draft_relance"]
                        refresh()
                        st.rerun()

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
